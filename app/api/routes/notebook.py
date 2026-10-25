import os
import re
import uuid
import shutil
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, File, UploadFile, Form, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse
import subprocess

from app.core.auth_guard import check_premium_plans, check_quota
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.subscription_service import use_lecture_quota, log_usage_failure, get_supabase
from app.db.supabase_client import get_auth_client
from app.services.link_ingest_service import download_audio_from_link
from app.core.queue_manager import queue_manager, JobStatus
from app.core.logger import logger
from app.services.ai.factory import get_llm_for_user
from app.services.aesthetic_notebook_service import generate_aesthetic_notebook_pdf
from langchain_openai import ChatOpenAI
from app.core.config import settings

router = APIRouter()

def trim_audio_to_limit(input_path: str, max_seconds: int = 1200) -> str:
    """
    Trims the input audio file to max_seconds (default 1200s = 20 minutes) using ffmpeg
    if it is longer than max_seconds. Returns the new file path or the original if it fails.
    """
    if not input_path or not os.path.exists(input_path):
        return input_path
    
    # Check current duration using ffprobe
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        duration_str = subprocess.check_output(cmd).decode().strip()
        duration = float(duration_str)
        if duration <= max_seconds:
            logger.info(f"[TRIM] Audio duration ({duration}s) is <= {max_seconds}s. Skipping trim.")
            return input_path
    except Exception as e:
        logger.warning(f"[TRIM] Failed to probe duration for {input_path}: {e}")
        # Proceed with trimming anyway to be safe

    mins_label = max_seconds // 60
    output_path = f"{os.path.splitext(input_path)[0]}_trimmed_{mins_label}m.mp3"
    try:
        logger.info(f"[TRIM] Trimming audio to {mins_label} minutes ({max_seconds}s): {input_path} -> {output_path}")
        subprocess.run([
            "ffmpeg", "-y", "-ss", "0", "-t", str(max_seconds), "-i", input_path,
            "-c:a", "libmp3lame", "-b:a", "128k", output_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(output_path):
            logger.info(f"✂️ Successfully trimmed audio to {mins_label} minutes: {output_path}")
            return output_path
    except Exception as e:
        logger.error(f"Failed to trim audio: {e}")
    return input_path

# Backward-compatible alias
trim_audio_to_5_mins = trim_audio_to_limit

# Helper: Instantiate primary Lammah model or fallback Gemini model
def get_notebook_llm(user_id: str):
    """
    Returns ChatOpenAI pointing to the primary engine.
    If settings.OPENROUTER_API_KEY is missing, falls back to the default user LLM.
    """
    if settings.OPENROUTER_API_KEY:
        logger.info(f"[NOTEBOOK] Instantiating primary model (Lammah AI Engine) via OpenRouter.")
        return ChatOpenAI(
            model="qwen/qwen2.5-vl-72b-instruct",
            openai_api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.25,
            max_tokens=4096,
            request_timeout=120,
            max_retries=1,
            default_headers={
                "HTTP-Referer": "https://lammah.site",
                "X-Title": "Lammah Notebook Creator"
            }
        )
    return get_llm_for_user(user_id)

@router.post("/process-notebook")
async def process_notebook(
    request: Request,
    audio_file: Optional[UploadFile] = File(None),
    pdf_file: Optional[UploadFile] = File(None),
    audio_temp_id: Optional[str] = Form(None),
    audio_temp_ids: Optional[str] = Form(None),
    pdf_temp_id: Optional[str] = Form(None),
    lecture_url: Optional[str] = Form(None),
    lecture_urls: Optional[str] = Form(None),
    lecture_title: Optional[str] = Form(None),
    student_name: Optional[str] = Form(None),
    notebook_type: Optional[str] = Form("university"),
    language_mode: Optional[str] = Form("bilingual"),
    profile=Depends(check_quota)
):
    """
    Aesthetic ruled notebook generator endpoint for subscribed users.
    Supports single or multiple audio files / URLs and outputs a premium watermarked PDF.
    """
    user_id = profile["id"]
    
    # Enforce YouTube links only, explicitly ban Google Drive & Zoom
    resolved_urls = lecture_urls or lecture_url
    if resolved_urls:
        for url in resolved_urls.split(","):
            url = url.strip()
            if not url:
                continue
            lower_url = url.lower()
            if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
                raise HTTPException(status_code=400, detail={
                    "user_message": "🚫 عذراً، روابط Google Drive غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥"
                })
            if "zoom.us" in lower_url or "zoom.com" in lower_url:
                raise HTTPException(status_code=400, detail={
                    "user_message": "🚫 عذراً، روابط Zoom غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥"
                })

    # -------------------------------------------------------------
    # NOTEBOOK ACCESS GATE:
    # Users with an active subscription or remaining quota
    # are permitted to generate university notebooks / ملازم!
    # -------------------------------------------------------------
    active_sub = None
    supabase = get_supabase()
    try:
        from datetime import timezone
        sub_result = supabase.table("user_subscriptions") \
            .select("*, subscription_plans(*)") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .gte("expires_at", datetime.now(timezone.utc).isoformat()) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        active_sub = sub_result.data[0] if (sub_result.data and isinstance(sub_result.data[0], dict)) else None
    except Exception as e:
        logger.error(f"Error fetching subscription in process_notebook: {e}")

    if not isinstance(active_sub, dict):
        logger.warning(f"🚫 [PROCESS_NOTEBOOK BLOCKED] User {user_id} has no active subscription.")
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "SUBSCRIPTION_REQUIRED",
                "user_message": "🔒 ميزة إنشاء وتوليد الملازم والدفاتر الأكاديمية متاحة حصرياً لمشتركي باقة النخبة (100 شيكل)! 👑🚀"
            }
        )

    sub_plan = active_sub.get("subscription_plans") if isinstance(active_sub, dict) else {}
    if not isinstance(sub_plan, dict):
        sub_plan = {}

    plan_name = str(sub_plan.get("name", "باقة نشطة"))
    plan_name_en = str(sub_plan.get("name_en", ""))
    plan_price = float(sub_plan.get("price", 0) or 0)

    is_elite = plan_price >= 100 or any(k in plan_name.lower() for k in ["نخبة", "elite", "ذهبية", "100", "full term"])
    if not is_elite:
        logger.warning(f"🚫 [PROCESS_NOTEBOOK BLOCKED] User {user_id} is on plan '{plan_name}' (price: {plan_price}) - Elite required.")
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "ELITE_PLAN_REQUIRED",
                "user_message": "🔒 ميزة إنشاء وتوليد الملازم والدفاتر الأكاديمية متاحة حصرياً لمشتركي باقة النخبة (100 شيكل)! 👑🚀"
            }
        )
    profile["active_subscription"] = active_sub

    student_major = profile.get("major") or "عام"
    profile_name = profile.get("full_name") or "طالب متميز"
    student_name = (student_name.strip() if student_name and student_name.strip() else None) or profile_name

    logger.info(f"✨ [PROCESS_NOTEBOOK] Request received by {student_name} ({user_id})")
    
    unique_id = str(uuid.uuid4())[:8]
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    
    upload_dir_env = os.getenv("UPLOAD_DIR")
    if upload_dir_env:
        upload_dir = os.path.normpath(upload_dir_env)
    else:
        upload_dir = os.path.normpath(os.path.join(base_dir, "backend/uploads"))
        
    temp_dir_env = os.getenv("TEMP_DIR")
    if temp_dir_env:
        temp_dir = os.path.normpath(temp_dir_env)
    else:
        temp_dir = os.path.normpath(os.path.join(base_dir, "uploader-go/uploads/temp"))
    
    # Sync with Go Uploader if needed
    audio_paths = []
    pdf_path = None
    
    # 1. Resolve Audio Temp IDs
    resolved_ids = audio_temp_ids or audio_temp_id
    if resolved_ids:
        for temp_id in resolved_ids.split(","):
            temp_id = temp_id.strip()
            if not temp_id:
                continue
            temp_path = os.path.join(temp_dir, temp_id)
            base, _ = os.path.splitext(temp_id)
            processed_p = os.path.join(temp_dir, f"{base}_processed.mp3")
            
            logger.info(f"[NOTEBOOK] Looking for audio: {temp_id} in {temp_dir}")
            
            # Wait up to 15 seconds for file to appear (shared volume sync delay in Docker)
            max_wait = 15
            waited = 0
            while not os.path.exists(temp_path) and not os.path.exists(processed_p) and waited < max_wait:
                await asyncio.sleep(1)
                waited += 1
                
            target_source = None
            if os.path.exists(processed_p):
                logger.info(f"[NOTEBOOK] Found processed audio: {processed_p}")
                target_source = processed_p
            elif os.path.exists(temp_path):
                logger.info(f"[NOTEBOOK] Found original audio: {temp_path}")
                target_source = temp_path
            else:
                raise HTTPException(status_code=400, detail="temp_file_missing")

            # Safely copy to backend upload_dir to isolate from Go uploader deletion & WinError 32
            os.makedirs(upload_dir, exist_ok=True)
            safe_audio_dst = os.path.join(upload_dir, f"{unique_id}_{os.path.basename(target_source)}")
            from app.services.file_manager import safe_copy_file_async
            await safe_copy_file_async(target_source, safe_audio_dst)
            audio_paths.append(safe_audio_dst)
                
    elif audio_file:
        os.makedirs(upload_dir, exist_ok=True)
        audio_path = os.path.join(upload_dir, f"{unique_id}_{audio_file.filename}")
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)
        audio_paths.append(audio_path)
        
    elif lecture_urls or lecture_url:
        pass
    elif pdf_temp_id or pdf_file:
        pass
    else:
        raise HTTPException(status_code=400, detail="Missing source files: please provide either audio, URL, or document.")

    if pdf_temp_id:
        pdf_path = os.path.join(temp_dir, pdf_temp_id)
        logger.info(f"[NOTEBOOK] Looking for PDF temp file: {pdf_path}")
        max_pdf_wait = 10
        pdf_waited = 0
        while not os.path.exists(pdf_path) and pdf_waited < max_pdf_wait:
            await asyncio.sleep(1)
            pdf_waited += 1
        if os.path.exists(pdf_path):
            logger.info(f"[NOTEBOOK] Found PDF: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")
            os.makedirs(upload_dir, exist_ok=True)
            safe_pdf_dst = os.path.join(upload_dir, f"{unique_id}_{pdf_temp_id}")
            from app.services.file_manager import safe_copy_file_async
            await safe_copy_file_async(pdf_path, safe_pdf_dst)
            pdf_path = safe_pdf_dst
        else:
            logger.warning(f"[NOTEBOOK][WARN] PDF not found after {max_pdf_wait}s: {pdf_path} — continuing without slides.")
            pdf_path = None
    elif pdf_file:
        os.makedirs(upload_dir, exist_ok=True)
        pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_file.filename}")
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(pdf_file.file, buffer)

    async def event_stream():
        nonlocal audio_paths, pdf_path
        is_free_trial = False
        try:
            # 0. Header padding
            yield ":" + " " * 1024 + "\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '⚡ جاري بدء تحضير ملزمة الامتحان...'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.1)

            # 1. Download audio if links
            resolved_urls = lecture_urls or lecture_url
            if resolved_urls and not audio_paths:
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '🔗 جاري تحميل المحاضرات من الروابط...'}, ensure_ascii=False)}\n\n"
                for idx, url in enumerate(resolved_urls.split(",")):
                    url = url.strip()
                    if not url:
                        continue
                    try:
                        dl_path = await asyncio.to_thread(download_audio_from_link, url, upload_dir, f"{unique_id}_{idx}")
                        audio_paths.append(dl_path)
                    except Exception as e:
                        logger.error(f"Failed to download audio from {url}: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                        return
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '📥 تم استلام وتجهيز الملفات الدراسية.'}, ensure_ascii=False)}\n\n"

            # If presentation file (PPTX/PPT), convert immediately to PDF as single source of truth
            if pdf_path and (pdf_path.lower().endswith(".pptx") or pdf_path.lower().endswith(".ppt")):
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '📑 جاري فحص وتحضير شرائح العرض (PowerPoint)...'}, ensure_ascii=False)}\n\n"
                from app.services.pptx_converter import convert_pptx_to_pdf
                pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, upload_dir)
                logger.info(f"[NOTEBOOK] PPTX converted immediately to PDF: {pdf_path}")

            # calculate cost in minutes
            import subprocess
            import math
            
            calculated_cost = 5  # default flat cost
            
            if audio_paths:
                total_seconds = 0.0
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '🎧 جاري فحص ملفات التسجيل الصوتي وتجهيزها للمعالجة...'}, ensure_ascii=False)}\n\n"
                for path in audio_paths:
                    try:
                        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
                        duration_str = (await asyncio.to_thread(subprocess.check_output, cmd)).decode().strip()
                        total_seconds += float(duration_str)
                    except Exception as e:
                        logger.warning(f"ffprobe failed for {path}: {e}. Falling back to file size calculation.")
                        if os.path.exists(path):
                            total_seconds += os.path.getsize(path) / (128000 / 8)
                calculated_cost = math.ceil(total_seconds / 60)
                if calculated_cost < 1:
                    calculated_cost = 1
            else:
                # Document-only: calculate pages to determine cost
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '📚 جاري فحص وتنظيم شرائح المحاضرة واستخراج المفاهيم...'}, ensure_ascii=False)}\n\n"
                if pdf_path:
                    try:
                        pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                        num_pages = len(pages)
                        
                        # Charge 1 minute per 2 slides/pages, minimum 3 minutes
                        calculated_cost = max(3, math.ceil(num_pages / 2))
                    except Exception as page_err:
                        logger.error(f"Failed to count document pages: {page_err}")
                        calculated_cost = 5
                else:
                    calculated_cost = 5

            from app.services.subscription_service import get_user_subscription_info
            sub_info = get_user_subscription_info(user_id)
            remaining_quota = sub_info.get("quota_remaining", 0)
            
            if remaining_quota < calculated_cost:
                quota_msg = f"🚫 رصيدك الحالي ({remaining_quota} دقيقة) لا يكفي لتوليد الدفتر للملف المرفق (المطلوب: {calculated_cost} دقيقة)."
                yield f"data: {json.dumps({'type': 'error', 'message': quota_msg}, ensure_ascii=False)}\n\n"
                return

            # 2. Queueing
            yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '⚡ جاري بدء المعالجة وتحضير الملزمة الدراسية...'}, ensure_ascii=False)}\n\n"
            representative_path = audio_paths[0] if audio_paths else None
            job = await queue_manager.enqueue(
                user_id=user_id,
                plan_name=plan_name,
                payload={"audio_path": representative_path, "pdf_path": pdf_path, "unique_id": unique_id, "is_audio_trimmed": is_free_trial}
            )

            while job.status == JobStatus.QUEUED:
                await asyncio.sleep(4)

            # 3. Transcribing / Slides Analysis
            if audio_paths:
                yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'message': '🎙️ جاري الاستماع للمحاضرة وتفريغ شرح الدكتور بدقة...'}, ensure_ascii=False)}\n\n"
                transcripts = []
                for idx, path in enumerate(audio_paths):
                    if len(audio_paths) > 1:
                        yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'message': f'🎙️ جاري تفريغ شرح المحاضرة ({idx + 1} من {len(audio_paths)})...'}, ensure_ascii=False)}\n\n"
                    t = await asyncio.to_thread(transcribe_audio, path)
                    if t:
                        if len(audio_paths) > 1:
                            transcripts.append(f"--- [محتوى الملف الصوتي #{idx + 1}] ---\n{t}\n")
                        else:
                            transcripts.append(t)
                transcript = "\n\n".join(transcripts)
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'message': '📑 جاري تفكيك ومطابقة كل شريحة مع محتوى الامتحان...'}, ensure_ascii=False)}\n\n"
                transcript = ""

            # 4. Context Extraction
            pdf_context = []
            if pdf_path:
                yield f"data: {json.dumps({'type': 'progress', 'step': '3.5/5', 'message': '📑 جاري مطابقة شرح الدكتور مع محتوى السلايدات...'}, ensure_ascii=False)}\n\n"
                max_slides = 150
                if pdf_path.lower().endswith(".pptx"):
                    from app.services.pptx_converter import extract_text_from_pptx
                    pptx_pages = await asyncio.to_thread(extract_text_from_pptx, pdf_path)
                    logger.info(f"[NOTEBOOK] PPTX extracted {len(pptx_pages)} slides from {pdf_path}")
                    pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pptx_pages[:max_slides]]
                else:
                    pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                    logger.info(f"[NOTEBOOK] PDF extracted {len(pages)} pages from {pdf_path}")
                    pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pages[:max_slides]]

                if not pdf_context:
                    logger.warning(f"[NOTEBOOK] pdf_context is EMPTY after extraction — file may be image-based or corrupted: {pdf_path}")

            # 5. Lammah AI Engine Call
            num_sources = len(audio_paths)
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': f'🧠 جاري صياغة التلخيص ودمج {num_sources} تسجيلات في ملزمة واحدة...'}, ensure_ascii=False)}\n\n"
            
            # Instantiate Lammah AI Engine
            llm = get_notebook_llm(user_id)
            
            # Determine language mode
            lang_mode = (language_mode or "bilingual").strip().lower()
            if lang_mode not in ("bilingual", "english", "arabic"):
                lang_mode = "bilingual"

            # Title Generation
            ai_title = lecture_title
            if not ai_title or len(ai_title.strip()) < 2:
                sample_text = transcript[:2000] if transcript else ""
                if lang_mode == "english":
                    title_prompt = (
                        "Suggest a concise, professional study guide title in English (3-6 words) "
                        "for this lecture without any introductions or quotation marks:\n\n" + sample_text
                    )
                else:
                    title_prompt = (
                        "اقترح عنواناً دراسياً احترافياً وموجزاً جداً باللغة العربية (3-5 كلمات) "
                        "لهذه المحاضرة بدون أي مقدمات أو علامات تنصيص:\n\n" + sample_text
                    )
                try:
                    title_response = await asyncio.to_thread(llm.invoke, title_prompt)
                    ai_title = str(title_response.content).strip().replace('"', '').replace('«', '').replace('»', '')
                except Exception as e:
                    logger.warning(f"Title generation failed, falling back: {e}")
                    fallback_llm = get_llm_for_user(user_id)
                    title_response = await asyncio.to_thread(fallback_llm.invoke, title_prompt)
                    ai_title = str(title_response.content).strip().replace('"', '')

            # Compute depth instruction based on transcript length & source count
            word_count = len(transcript.split())
            depth_note = ""
            if num_sources > 1:
                depth_note = (
                    f"⚠️ تحذير صارم وهام جداً بخصوص دمج التسجيلات الصوتية المتعددة:\n"
                    f"- تم رفع ودمج عدد ({num_sources}) ملفات صوتية مختلفة لهذه الملزمة.\n"
                    f"- يجب عليك قراءة التفريغ النصي لكل ملف صوتي بشكل كامل وتغطية كل محتوياته وتفاصيله وأفكاره بدقة متناهية دون إهمال أو حذف أي جزء منها!\n"
                    f"- قم بتقسيم الملزمة هيكلياً إلى فصول أو أقسام واضحة ومستقلة تمثل كافة هذه التسجيلات بالترتيب.\n"
                    f"- احرص على ألا تضيع أي معلومة أو شرح ورد في أي تسجيل صوتي من التسجيلات المرفوعة، ولا تقم باختصار أو دمج الفصول بشكل يخل بالتفاصيل."
                )
            elif word_count > 5000:
                depth_note = "المحاضرة طويلة ومفصلة، غطِّ كل نقطة وتفريعة بعمق وشمولية تامة. "

            # Exhaustive Deep-Dive / Zero-Omission Rule:
            # The notebook is intended as the primary standalone source so students NEVER need the raw slides or audio again.
            length_instruction = (
                "RULE OF EXHAUSTIVE DEEP-DIVE & STANDALONE COVERAGE (تغطية استيعابية فائقة تغني تماماً عن السلايدات والمحاضرة):\n"
                "- This notebook is designed as the SOLE, definitive study replacement for the student. The student should NEVER need to reopen the original slides or listen to the raw audio recording.\n"
                "- Cover EVERY topic, subtopic, definition, classification, formula, clinical scenario, code concept, architecture, and edge case mentioned in the materials.\n"
                "- DO NOT compress or summarize by omitting details. Expand naturally: take as many pages, paragraphs, and sections as the subject matter requires.\n"
                "- CRITICAL RULE: ZERO FLUFF (العمق المعرفي الخالص دون حشو إنشائي). Do NOT pad with repetitive words or poetic filler. Provide concrete substance: step-by-step logic, precise mechanisms, real-world examples, and specific exam traps."
            )

            source_instruction = ""
            if not transcript:
                source_instruction = "⚠️ هام جداً: المحاضرة لا تحتوي على تسجيل صوتي. يجب عليك توليد الملزمة الدراسية والشرح والمقارنات والأسئلة بالكامل بناءً على 'محتوى شرائح المحاضرة' المرفق بالأسفل فقط! قم بتغطية كل شريحة بالتفصيل وفسر المفاهيم الواردة فيها."

            # STEP 1: Generate comprehensive Markdown study notebook
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': '✍️ جاري صياغة ملزمة شاملة ومستقلة تغنيك عن كل السلايدات...'}, ensure_ascii=False)}\n\n"

            if notebook_type == "course_podcast":
                prompt_step1 = (
                    "You are 'Lammah Content Synthesizer' — an elite assistant and expert in creating premium, high-value summaries of courses, workshops, and podcasts. "
                    "Your task is to transform the provided content transcript into a 'Premium Knowledge Guide'. It must be highly organized, structured, insightful, and designed for self-development and professional application. "
                    "Avoid academic/university terminology (like 'students', 'professors', 'exams', 'lectures'). Refer to 'the content', 'the speaker', or 'the listeners' instead.\n\n"
                    f"{depth_note}\n"
                    f"{source_instruction}\n\n"
                    "══════════════════════════════════════\n"
                    "🎯 STRICT GUIDELINES (100% ENFORCED):\n"
                    "══════════════════════════════════════\n"
                    "1. Smart Language Style:\n"
                    + (
                        "   - Write the entire guide in formal, polished, and actionable English.\n\n"
                        if lang_mode == "english" else
                        "   - Write the core explanations in clear, smooth, and professional Arabic.\n"
                        "   - MANDATORY: ALL technical, scientific, or specific domain terms MUST be written in English. Provide the Arabic translation in parentheses ONLY upon the first mention. (e.g., 'تعتبر الـ Strategy (الاستراتيجية) أساسية...').\n"
                        "   - NEVER generate separate Arabic and English sections. Integrate them naturally.\n\n"
                    ) +
                    "2. Minimalist & Clean Structure:\n"
                    "   - Use ## for main topics and ### for subtopics.\n"
                    "   - STRICTLY avoid walls of text. Use bullet points and numbered lists extensively for readability.\n\n"
                    "3. Section Components (For every ## main topic, use these exactly):\n"
                    "   - 💡 **الفكرة الجوهرية (Core Idea):** 2-3 lines summarizing the essence of the topic.\n"
                    "   - 🧠 **الشرح والتفصيل (Deep Dive):** Comprehensive explanation covering all details and examples mentioned by the speaker.\n"
                    "   - 🔑 **المصطلحات والأدوات (Key Concepts/Tools):** A bulleted list of terms and their concise definitions.\n"
                    "   - 💬 **اقتباسات مميزة (Speaker's Quotes):** Extract 1-2 exact, notable sentences or literal advice the speaker said, wrapped in quotes.\n\n"
                    "4. Comprehensiveness & Standalone Depth:\n"
                    f"   - {length_instruction}\n"
                    "══════════════════════════════════════\n"
                    "CONTENT TRANSCRIPT:\n"
                    "══════════════════════════════════════\n"
                    f"{transcript[:180000]}\n"
                )
            elif lang_mode == "english":
                prompt_step1 = (
                    "You are 'Lammah Academic Engine' — an elite academic assistant and expert in synthesizing university lectures into bookstore-grade study guides.\n"
                    "Your task is to transform the provided lecture transcript and slide materials into a 'Master Study Guide'. It must be thoroughly comprehensive, highly organized, visually structured, and completely devoid of filler fluff, designed for students to rely on 100% as their sole study source for exams.\n\n"
                    f"Context: The student's academic major is {student_major}. Tailor all explanations, technical terminology, and analogies precisely to this field.\n\n"
                    f"{depth_note}\n"
                    f"{source_instruction}\n\n"
                    "══════════════════════════════════════\n"
                    "🎯 STRICT GUIDELINES (100% ENFORCED):\n"
                    "══════════════════════════════════════\n"
                    "1. Language Directive: 100% PURE ACADEMIC ENGLISH (Elite Global Standard):\n"
                    "   - Write the entire study guide in formal, clear, high-yield academic English (matching Harvard, MIT, Oxford, and USMLE standards).\n"
                    "   - Do NOT include any Arabic words anywhere in this guide.\n"
                    "   - Bold all key terms, formulas, and critical concepts.\n\n"
                    "2. Minimalist & Clean Structure:\n"
                    "   - Use ## for main topics and ### for subtopics.\n"
                    "   - STRICTLY avoid continuous walls of text. Use bullet points and numbered lists extensively for readability.\n\n"
                    "3. Section Components (For every ## main topic, use these exactly):\n"
                    "   - 💡 **Core Idea:** 2-3 lines summarizing the fundamental essence of the topic.\n"
                    "   - 🧠 **Deep Dive & Technical Breakdown:** Comprehensive explanation covering all details, rules, derivations, and examples from the materials.\n"
                    "   - 🔑 **Key Terminology:** A bulleted list of essential terms with crisp, precise definitions.\n"
                    "   - ⚠️ **High-Yield Exam Tips:** Highlight areas professors emphasize or common pitfalls.\n"
                    "   - ⚠️ **Exam Trap & Common Distractor:** Identify typical traps, confusion points, or tricky distractor options.\n"
                    "   - 💬 **Professor's Verbatim Insights:** Extract 1-2 notable, authentic insights or advice from the lecture.\n\n"
                    "4. Standalone Exhaustive Depth (Zero Omission):\n"
                    f"   - {length_instruction}\n"
                    "══════════════════════════════════════\n"
                    "LECTURE TRANSCRIPT:\n"
                    "══════════════════════════════════════\n"
                    f"{transcript[:180000]}\n"
                )
            elif lang_mode == "arabic":
                prompt_step1 = (
                    "أنت 'محرك لمّاح الأكاديمي' — مساعد أكاديمي نخبوي وخبير في تلخيص وصياغة المحاضرات الجامعية في مراجع وملازم دراسية فاخرة.\n"
                    "مهمتك هي تحويل تفريغ المحاضرة والشرائح المرفقة إلى 'ملزمة دراسية شاملة ومستقلة'. يجب أن تكون منظمة جداً، خالية تماماً من الحشو والإنشاء، ومصاغة بطريقة تجعل الطالب يستغني بها عن كل المراجع والسلايدات ليلة الامتحان.\n\n"
                    f"السياق: تخصص الطالب هو {student_major}. وجه الشرح والأمثلة والمصطلحات لتلائم هذا التخصص بدقة.\n\n"
                    f"{depth_note}\n"
                    f"{source_instruction}\n\n"
                    "══════════════════════════════════════\n"
                    "🎯 التوجيهات الصارمة (إلزامية بنسبة 100%):\n"
                    "══════════════════════════════════════\n"
                    "1. توجيه اللغة: لغة عربية أكاديمية فصيحة ورصينة بالكامل (100% Arabic):\n"
                    "   - صياغة كامل الملزمة باللغة العربية الفصيحة والرصينة والمتقنة مع تعريب وتأصيل كل مفهوم وشرحه علمياً (مناسبة لكليات الحقوق، الشريعة، والعلوم الإنسانية).\n"
                    "   - استخدام التنسيق العريض (Bold) للكلمات المفتاحية والمصطلحات.\n\n"
                    "2. الهيكل الواضح والمنظم:\n"
                    "   - استخدام ## للمواضيع الرئيسية و ### للعناوين الفرعية.\n"
                    "   - تجنب الكتل النصية الصماء، واستخدام النقاط والقوائم المرقمة لتسهيل القراءة.\n\n"
                    "3. مكونات كل موضوع رئيسي (##):\n"
                    "   - 💡 **الفكرة الجوهرية:** سطرين إلى ثلاثة تلخص زبدة الموضوع.\n"
                    "   - 🧠 **الشرح والتفصيل:** شرح وافٍ ومفصل يغطي كل القواعد والتفريعات والمسائل والأمثلة.\n"
                    "   - 🔑 **المصطلحات والمفاهيم الأساسية:** قائمة نقطية بالمفاهيم المركزية وتعريفها المركز.\n"
                    "   - ⚠️ **نقاط الامتحان والتركيز:** تنبيهات للنقاط التي يركز عليها الأساتذة.\n"
                    "   - ⚠️ **فخ امتحاني:** تفكيك اللبس بين المفاهيم المتشابهة لتفادي الخطأ في الاختبار.\n"
                    "   - 💬 **اقتباسات المحاضر:** استخراج جمل أو ملاحظات نوعية قالها المحاضر.\n\n"
                    "4. الشمولية الفائقة المستقلة (دون حشو):\n"
                    f"   - {length_instruction}\n"
                    "══════════════════════════════════════\n"
                    "تفريغ المحاضرة:\n"
                    "══════════════════════════════════════\n"
                    f"{transcript[:180000]}\n"
                )
            else:
                # Default: Bilingual Enriched Hybrid
                prompt_step1 = (
                    "You are 'Lammah Academic Engine' — an elite academic assistant and expert in synthesizing university lectures into bookstore-grade study guides. "
                    "Your task is to transform the provided lecture transcript and slides into a 'Master Study Guide'. It must be thoroughly comprehensive, highly organized, visually clean, devoid of fluff, and perfectly structured for students to rely on 100% as their sole primary study source for exams.\n\n"
                    f"Context: The student's major is {student_major}. Tailor your explanations, tone, and analogies to perfectly suit a student in this specific field.\n\n"
                    f"{depth_note}\n"
                    f"{source_instruction}\n\n"
                    "══════════════════════════════════════\n"
                    "🎯 STRICT GUIDELINES (100% ENFORCED):\n"
                    "══════════════════════════════════════\n"
                    "1. Smart Bilingual Style (Enriched Hybrid Terminology Injection - أسلوب النص المُطعّم بالإنجليزية الامتحانية):\n"
                    "   - Write the core explanations in clear, smooth, engaging Arabic (لشرح المنطق والفهم بسلاسة وأسلوب أخوي ذكي).\n"
                    "   - MANDATORY: Every stage, technical concept, tool, output, diagram, law, or property MUST be injected in English alongside its Arabic translation directly inside the flow of the explanation.\n"
                    "   - Example:\n"
                    "     'المرحلة الأولى وهي الـ Requirements (المتطلبات). الهدف الأساسي هو الوصول لـ Shared View (رؤية مشتركة) لضمان بناء نفس النظام. المخرجات بتكون List of requirements، وتُكتب بصيغة نصية Textual format زي الـ User stories، أو بصيغة تخطيطية Diagrammatic زي الـ Use case diagrams.'\n"
                    "   - Pedagogical Reason: The student understands the logic in Arabic, but their eye captures the exact English terminology that appears as multiple-choice options (A, B, C, D) in the exam without needing mental translation.\n"
                    "   - NEVER generate separate disconnected Arabic and English sections. Integrate them naturally.\n\n"
                    "2. Minimalist & Clean Structure:\n"
                    "   - Use ## for main topics and ### for subtopics.\n"
                    "   - STRICTLY avoid walls of text. Use bullet points and numbered lists extensively for readability.\n\n"
                    "3. Section Components (For every ## main topic, use these exactly):\n"
                    "   - 💡 **الفكرة الجوهرية (Core Idea):** 2-3 lines summarizing the essence of the topic.\n"
                    "   - 🧠 **الشرح والتفصيل (Deep Dive):** Comprehensive explanation covering all details, rules, and examples mentioned by the professor or slides.\n"
                    "   - 🔑 **المصطلحات الأساسية (Key Terms):** A bulleted list of English terms and their concise Arabic definitions.\n"
                    "   - ⚠️ **نقاط امتحان (Exam Tips):** Highlight areas the professor emphasized or common pitfalls.\n"
                    "   - ⚠️ **فخ امتحاني (Exam Trap):** Identify confusing elements or typical mistakes students make on this topic (e.g. 'انتبه: يقع الطلاب في فخ خلط X مع Y...').\n"
                    "   - 💬 **اقتباسات المحاضر (Professor's Quotes):** Extract 1-2 exact, notable sentences, jokes, or literal advice the professor said, wrapped in quotes. This adds a personalized, human touch proving the guide is authentic.\n\n"
                    "4. Standalone Exhaustive Depth (Zero Omission):\n"
                    f"   - {length_instruction}\n"
                    "══════════════════════════════════════\n"
                    "LECTURE TRANSCRIPT:\n"
                    "══════════════════════════════════════\n"
                    f"{transcript[:180000]}\n"
                )

            if pdf_context:
                prompt_step1 += (
                    "\n══════════════════════════════════════\n"
                    "📊 محتوى شرائح المحاضرة:\n"
                    "══════════════════════════════════════\n"
                    f"{json.dumps(pdf_context[:60], ensure_ascii=False)}"
                )

            try:
                logger.info("[NOTEBOOK] Call 1: Requesting Lammah AI Engine for bilingual markdown content...")
                ai_response = await asyncio.to_thread(llm.invoke, prompt_step1)
                markdown_content = str(ai_response.content).strip()
            except Exception as e:
                logger.warning(f"[NOTEBOOK] Call 1: Lammah AI Engine failed: {e}. Falling back to Gemini 2.0...")
                fallback_llm = get_llm_for_user(user_id)
                ai_response = await asyncio.to_thread(fallback_llm.invoke, prompt_step1)
                markdown_content = str(ai_response.content).strip()

            # STEP 2: Generate structured cheat_sheet, mcqs, and comparisons based on the generated markdown
            if notebook_type == "course_podcast":
                yield f"data: {json.dumps({'type': 'progress', 'step': '4.5/5', 'message': '🧠 محرك لمّاح يستخلص الخلاصة الجوهرية والخطوات العملية والأسئلة الذاتية...'}, ensure_ascii=False)}\n\n"
                prompt_step2 = (
                    "You are 'Lammah Content Synthesizer' — an expert in extracting structured, actionable insights from course and podcast summaries.\n"
                    "Based ONLY on the provided Markdown Guide, generate a precise JSON output containing the following keys (big_picture, concept_breakdown, key_takeaways, actionable_blueprint, roadblocks_solutions, reflection_prompts, shareable_quotes, resources_tools):\n\n"
                    "1. big_picture: A 2-3 sentence Arabic paragraph summarizing the core philosophy or main idea of the content (giving the reader immediate value).\n"
                    "2. concept_breakdown: A list of the most important concepts, tools, or theories explained. The 'term' must be in English, and the 'definition' must be a simple, direct explanation in Arabic (1-2 sentences).\n"
                    "3. key_takeaways: A list of 5-8 detailed Arabic bullet points representing the core takeaways and central arguments of the content.\n"
                    "4. actionable_blueprint: A list of 4-6 practical, numbered Arabic steps (1, 2, 3...) that the user can immediately implement in their work or personal life.\n"
                    "5. roadblocks_solutions: A list of 3-4 objects of potential roadblocks matching the advice, containing two keys: 'roadblock' (العقبة المتوقعة عند التطبيق) and 'solution' (الحل المقترح للتغلب عليها).\n"
                    "6. reflection_prompts: A list of 3-5 deep self-reflection questions in Arabic, prompting the user to think about how this content applies to their specific situation (to turn it into a workbook).\n"
                    "7. shareable_quotes: A list of 3-5 of the most powerful, inspiring quotes or literal sentences said by the speaker.\n"
                    "8. resources_tools: A list of books, apps, sites, or references recommended or mentioned in the content.\n\n"
                    "══════════════════════════════════════\n"
                    "⚠️ STRICT JSON FORMAT RULE:\n"
                    "══════════════════════════════════════\n"
                    "Your output MUST be ONLY valid JSON. No markdown formatting outside the JSON, no intro, no outro.\n"
                    "{\n"
                    '  "big_picture": "الخلاصة الجوهرية هنا...",\n'
                    '  "concept_breakdown": [\n'
                    '    {"term": "Concept Name", "definition": "الشرح المبسط جداً..."}\n'
                    '  ],\n'
                    '  "key_takeaways": [\n'
                    '    "الاستنتاج الأول التفصيلي...",\n'
                    '    "الاستنتاج الثاني التفصيلي..."\n'
                    '  ],\n'
                    '  "actionable_blueprint": [\n'
                    '    "الخطوة الأولى العملية...",\n'
                    '    "الخطوة الثانية العملية..."\n'
                    '  ],\n'
                    '  "roadblocks_solutions": [\n'
                    '    {"roadblock": "العقبة المحتملة...", "solution": "كيفية التغلب عليها..."}\n'
                    '  ],\n'
                    '  "reflection_prompts": [\n'
                    '    "سؤال للتفكير 1...",\n'
                    '    "سؤال للتفكير 2..."\n'
                    '  ],\n'
                    '  "shareable_quotes": [\n'
                    '    "اقتباس ملهم 1...",\n'
                    '    "اقتباس ملهم 2..."\n'
                    '  ],\n'
                    '  "resources_tools": [\n'
                    '    "أداة أو كتاب أو موقع 1...",\n'
                    '    "أداة أو كتاب أو موقع 2..."\n'
                    '  ]\n'
                    "}\n\n"
                    "══════════════════════════════════════\n"
                    "MARKDOWN GUIDE:\n"
                    "══════════════════════════════════════\n"
                    f"{markdown_content}\n"
                )
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '4.5/5', 'message': '🎯 جاري استخراج زبدة ليلة الامتحان وفخاخ الأسئلة والمقارنات...'}, ensure_ascii=False)}\n\n"
                if lang_mode == "english":
                    prompt_step2 = (
                        "You are 'Lammah Academic Engine' — an expert in extracting structured, high-yield study materials for university exam preparation.\n"
                        "Based ONLY on the provided Markdown Study Guide, generate a precise JSON output in 100% PURE ENGLISH containing the following keys (study_strategy, tldr, cheat_sheet, mcqs, comparisons):\n\n"
                        "1. study_strategy: A 2-sentence English advice directing the student how to tackle this lecture for their exams.\n"
                        "2. tldr: A list of 4-6 concise, high-yield English bullet points summarizing the absolute most critical takeaways (the '3-minute summary' before the exam).\n"
                        "3. cheat_sheet: A list of the 12-20 most critical concepts or formulas. The 'term' must be in English. The 'brief_definition' must be a crisp, one-line English capsule definition for instant revision.\n"
                        "4. comparisons: Extract at least one smart comparison table contrasting the most easily confused concepts in this lecture.\n"
                        "5. mcqs: Create 20-30 multiple-choice questions (A, B, C, D) at a rigorous university exam level testing deep comprehension and problem solving. All questions and choices MUST be in English.\n\n"
                        "══════════════════════════════════════\n"
                        "⚠️ STRICT JSON FORMAT RULE (NO EXTRA TEXT):\n"
                        "══════════════════════════════════════\n"
                        "{\n"
                        '  "study_strategy": "Strategic study advice in English...",\n'
                        '  "tldr": ["Key takeaway 1...", "Key takeaway 2..."],\n'
                        '  "cheat_sheet": [{"term": "Concept Name", "brief_definition": "Crisp 1-line definition"}],\n'
                        '  "mcqs": [\n'
                        '    {\n'
                        '      "question": "Question text in English...",\n'
                        '      "choices": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],\n'
                        '      "answer": "A"\n'
                        '    }\n'
                        '  ],\n'
                        '  "comparisons": [\n'
                        '    {\n'
                        '      "title": "Comparison between X and Y",\n'
                        '      "headers": ["Concept A", "Concept B"],\n'
                        '      "rows": [["Feature", "Value for A", "Value for B"]]\n'
                        '    }\n'
                        '  ]\n'
                        "}\n\n"
                        "══════════════════════════════════════\n"
                        "MARKDOWN STUDY GUIDE:\n"
                        "══════════════════════════════════════\n"
                        f"{markdown_content}\n"
                    )
                elif lang_mode == "arabic":
                    prompt_step2 = (
                        "أنت 'محرك لمّاح الأكاديمي' — خبير في استخلاص المواد والكبسولات الامتحانية الذكية للاستعداد للاختبارات الجامعية.\n"
                        "بناءً على دليل الدراسة المرفق فقط، قم بتوليد كائن JSON دقيق باللغة العربية بالكامل يحتوي على المفاتيح التالية (study_strategy, tldr, cheat_sheet, mcqs, comparisons):\n\n"
                        "1. study_strategy: نصيحة أكاديمية في جملتين توجه الطالب لكيفية مذاكرة هذه المحاضرة بكفاءة.\n"
                        "2. tldr: قائمة من 4-6 نقاط تلخيصية مركزة تمثل زبدة المحاضرة لليلة الامتحان.\n"
                        "3. cheat_sheet: قائمة بأهم 12-20 مفهوماً وقاعدة، بحيث يكون term هو المصطلح، و brief_definition هو التعريف المركز في سطر واحد بنظام الكبسولة.\n"
                        "4. comparisons: استخراج جدول مقارنة ذكي يفكك اللبس بين أكثر المفاهيم تشابهاً.\n"
                        "5. mcqs: إنشاء 20-30 سؤال اختيار من متعدد (أ، ب، ج، د) بمستوى جامعي يقيس الفهم العميق والتحليل باللغة العربية.\n\n"
                        "══════════════════════════════════════\n"
                        "⚠️ قاعدة JSON الصارمة:\n"
                        "══════════════════════════════════════\n"
                        "{\n"
                        '  "study_strategy": "نصيحة لمّاح الأكاديمية للدراسة...",\n'
                        '  "tldr": ["التلخيص الأول...", "التلخيص الثاني..."],\n'
                        '  "cheat_sheet": [{"term": "المصطلح", "brief_definition": "التعريف المركز"}],\n'
                        '  "mcqs": [\n'
                        '    {\n'
                        '      "question": "نص السؤال العربي بمستوى امتحاني...",\n'
                        '      "choices": ["أ) خيار 1", "ب) خيار 2", "ج) خيار 3", "د) خيار 4"],\n'
                        '      "answer": "أ"\n'
                        '    }\n'
                        '  ],\n'
                        '  "comparisons": [\n'
                        '    {\n'
                        '      "title": "مقارنة ذكية بين ... و ...",\n'
                        '      "headers": ["المفهوم الأول", "المفهوم الثاني"],\n'
                        '      "rows": [["وجه المقارنة", "قيمة أ", "قيمة ب"]]\n'
                        '    }\n'
                        '  ]\n'
                        "}\n\n"
                        "══════════════════════════════════════\n"
                        "دليل الدراسة:\n"
                        "══════════════════════════════════════\n"
                        f"{markdown_content}\n"
                    )
                else:
                    # Default: Bilingual Mode
                    prompt_step2 = (
                        "You are 'Lammah Academic Engine' — an expert in extracting structured, high-yield study materials for university exam preparation.\n"
                        "Based ONLY on the provided Markdown Study Guide, generate a precise JSON output containing the following keys (study_strategy, tldr, cheat_sheet, mcqs, comparisons):\n\n"
                        "1. study_strategy: A 2-sentence Arabic advice directing the student how to study this lecture effectively.\n"
                        "2. tldr: A list of 4-6 concise, high-yield bullet points summarizing the absolute most critical takeaways for exam day (with English keywords in parentheses).\n"
                        "3. cheat_sheet: A list of the 12-20 most critical concepts or formulas. The 'term' must be in English. The 'brief_definition' must be in Arabic, written in a 'Capsule' style (highly concise, direct, one short line for quick revision).\n"
                        "4. comparisons: Extract at least one smart comparison table contrasting the most easily confused concepts in this lecture.\n"
                        "5. mcqs: CRITICAL EXAM PREPARATION DIRECTIVE: University exams are written in ENGLISH. Therefore, every single practice question MUST train the student for their actual English exam format:\n"
                        "   - 'question': Write the question in clear, rigorous academic ENGLISH first, followed immediately by its Arabic translation in parentheses for instant comprehension.\n"
                        "   - 'choices': Exactly 4 choices (A, B, C, D). Every single choice MUST start with the English option, followed by its Arabic translation in parentheses. NEVER write Arabic-only choices!\n"
                        "   - Example format:\n"
                        "     'question': 'What is the primary role of Change Control & Configuration Management? (ما هو الدور الأساسي لإدارة التغيير وضبط التكوين في المشروع؟)',\n"
                        "     'choices': [\n"
                        "       'A) Staffing and hiring project team members (توظيف وتدريب فريق المشروع)',\n"
                        "       'B) Managing, tracking, and approving baseline changes (إدارة وتتبع واعتماد تعديلات وتكوينات النظام)',\n"
                        "       'C) Final product release and marketing (إطلاق وتسويق المنتج النهائي)',\n"
                        "       'D) Defining initial project scope and feasibility (تحديد النطاق الأولي وجدوى المشروع)'\n"
                        "     ],\n"
                        "     'answer': 'B'\n\n"
                        "══════════════════════════════════════\n"
                        "⚠️ STRICT JSON FORMAT RULE:\n"
                        "══════════════════════════════════════\n"
                        "Your output MUST be ONLY valid JSON. No markdown formatting outside the JSON, no intro, no outro.\n"
                        "{\n"
                        '  "study_strategy": "نصيحة لمّاح الأكاديمية للدراسة...",\n'
                        '  "tldr": [\n'
                        '    "التلخيص السريع الأول (مع المصطلحات الإنجليزية)...",\n'
                        '    "التلخيص السريع الثاني..."\n'
                        '  ],\n'
                        '  "cheat_sheet": [\n'
                        '    {"term": "English Term", "brief_definition": "تعريف عربي موجز جداً"}\n'
                        '  ],\n'
                        '  "mcqs": [\n'
                        '    {\n'
                        '      "question": "English question text? (الترجمة العربية للسؤال)",\n'
                        '      "choices": ["A) English option (الترجمة)", "B) English option (الترجمة)", "C) English option (الترجمة)", "D) English option (الترجمة)"],\n'
                        '      "answer": "A"\n'
                        '    }\n'
                        '  ],\n'
                        '  "comparisons": [\n'
                        '    {\n'
                        '      "title": "مقارنة ذكية بين ... و ...",\n'
                        '      "headers": ["المفهوم الأول", "المفهوم الثاني"],\n'
                        '      "rows": [["وجه المقارنة", "قيمة أ", "قيمة ب"]]\n'
                        '    }\n'
                        '  ]\n'
                        "}\n\n"
                        "══════════════════════════════════════\n"
                        "MARKDOWN STUDY GUIDE:\n"
                        "══════════════════════════════════════\n"
                        f"{markdown_content}\n"
                    )

            study_strategy = "هذه المحاضرة غنية بالمفاهيم الأساسية، ننصحك بالتركيز على مراجعة المصطلحات والأسئلة التطبيقية لترسيخ الفهم."
            tldr = [
                "المفاهيم الأساسية الواردة في هذه المحاضرة جوهرية للامتحان.",
                "احرص على مراجعة جدول المقارنة والأسئلة التطبيقية لترسيخ الفهم."
            ]
            cheat_sheet = []
            mcqs = []
            comparisons = []
            
            big_picture = "هذا الدفتر يقدم خلاصة وافية وخطوات عملية مفصلة مستخرجة من المحتوى لمساعدتك على التطبيق المباشر."
            concept_breakdown = []
            key_takeaways = []
            actionable_blueprint = []
            roadblocks_solutions = []
            reflection_prompts = []
            shareable_quotes = []
            resources_tools = []
            
            raw_json_content = ""

            try:
                logger.info("[NOTEBOOK] Call 2: Requesting structured JSON from Lammah AI Engine...")
                ai_response_json = await asyncio.to_thread(llm.invoke, prompt_step2)
                raw_json_content = str(ai_response_json.content).strip()
            except Exception as e:
                logger.warning(f"[NOTEBOOK] Call 2: Lammah AI Engine failed: {e}. Falling back to Gemini 2.0...")
                fallback_llm = get_llm_for_user(user_id)
                ai_response_json = await asyncio.to_thread(fallback_llm.invoke, prompt_step2)
                raw_json_content = str(ai_response_json.content).strip()

            try:
                # Extract JSON block (handle markdown code fences)
                json_str = raw_json_content
                fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_json_content)
                if fence_match:
                    json_str = fence_match.group(1).strip()
                else:
                    brace_start = raw_json_content.find('{')
                    brace_end = raw_json_content.rfind('}')
                    if brace_start != -1 and brace_end != -1:
                        json_str = raw_json_content[brace_start:brace_end + 1]

                parsed = json.loads(json_str)
                if notebook_type == "course_podcast":
                    big_picture = parsed.get("big_picture", big_picture)
                    concept_breakdown = parsed.get("concept_breakdown", [])
                    key_takeaways = parsed.get("key_takeaways", [])
                    actionable_blueprint = parsed.get("actionable_blueprint", [])
                    roadblocks_solutions = parsed.get("roadblocks_solutions", [])
                    reflection_prompts = parsed.get("reflection_prompts", [])
                    shareable_quotes = parsed.get("shareable_quotes", [])
                    resources_tools = parsed.get("resources_tools", [])
                    
                    logger.info(f"[NOTEBOOK] Call 2 Course/Podcast Parsed: {len(key_takeaways)} takeaways, {len(concept_breakdown)} concepts, {len(actionable_blueprint)} actions.")
                else:
                    study_strategy = parsed.get("study_strategy", study_strategy)
                    tldr = parsed.get("tldr", tldr)
                    cheat_sheet = parsed.get("cheat_sheet", [])
                    mcqs = parsed.get("mcqs", [])
                    comparisons = parsed.get("comparisons", [])
                    
                    logger.info(f"[NOTEBOOK] Call 2 University Parsed: {len(cheat_sheet)} cheat items, {len(mcqs)} MCQs, {len(comparisons)} comparisons.")
            except Exception as parse_err:
                logger.error(f"[NOTEBOOK] Call 2 JSON parse failed: {parse_err}. Raw JSON content: {raw_json_content}")

            # Calculate stats for Cover page analytics grid
            word_count = len(markdown_content.split())
            est_study_time = min(60, 20 + math.ceil(word_count / 150))
            if notebook_type == "course_podcast":
                stats = {
                    "num_takeaways": len(key_takeaways),
                    "num_concepts": len(concept_breakdown),
                    "num_action_steps": len(actionable_blueprint),
                    "est_read_time": est_study_time
                }
            else:
                stats = {
                    "num_key_terms": len(cheat_sheet),
                    "num_mcqs": len(mcqs),
                    "num_comparisons": len(comparisons),
                    "est_study_time": est_study_time
                }

            # 6. Render Premium PDF
            yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'message': '📄 جاري تنسيق وتجهيز ملف الملزمة للطباعة والمذاكرة (PDF)...'}, ensure_ascii=False)}\n\n"
            
            output_filename = f"notebook_{unique_id}.pdf"
            preview_filename = f"preview_notebook_{unique_id}.pdf"
            output_pdf_path = os.path.join(base_dir, "outputs", output_filename)
            preview_pdf_path = os.path.join(base_dir, "outputs", preview_filename)
            
            from app.services.aesthetic_notebook_service import generate_aesthetic_notebook_pdf, generate_course_podcast_notebook_pdf, generate_preview_pdf
            
            if notebook_type == "course_podcast":
                await asyncio.to_thread(
                    generate_course_podcast_notebook_pdf,
                    output_path=output_pdf_path,
                    student_name=student_name,
                    lecture_title=ai_title,
                    is_free_trial=is_free_trial,
                    big_picture=big_picture,
                    concept_breakdown=concept_breakdown,
                    key_takeaways=key_takeaways,
                    actionable_blueprint=actionable_blueprint,
                    roadblocks_solutions=roadblocks_solutions,
                    reflection_prompts=reflection_prompts,
                    shareable_quotes=shareable_quotes,
                    resources_tools=resources_tools,
                    stats=stats,
                )
            else:
                await asyncio.to_thread(
                    generate_aesthetic_notebook_pdf,
                    output_path=output_pdf_path,
                    continuous_markdown=markdown_content,
                    student_name=student_name,
                    lecture_title=ai_title,
                    cheat_sheet=cheat_sheet,
                    mcqs=mcqs,
                    comparisons=comparisons,
                    is_free_trial=is_free_trial,
                    study_strategy=study_strategy,
                    tldr=tldr,
                    stats=stats,
                    language_mode=lang_mode,
                )
            
            await asyncio.to_thread(
                generate_preview_pdf,
                original_pdf_path=output_pdf_path,
                preview_pdf_path=preview_pdf_path,
                max_pages=5
            )

            # 7. Quota Deduction & History Save
            is_document_only = not bool(audio_paths)
            history_meta = {
                "transcript": transcript[:3000],
                "annotations": {
                    "markdown": markdown_content
                },
                "job_id": f"notebook_{unique_id}",
                "title": ai_title,
                "has_pdf": bool(pdf_path),
                "is_notebook": True,
                "student_name": student_name,
                "notebook_type": notebook_type,
                "is_prep": is_document_only,
                "has_audio": bool(audio_paths),
            }

            # Save sidecar metadata for downloads
            try:
                sidecar_data = {
                    "title": ai_title,
                    "has_audio": bool(audio_paths),
                    "has_pdf": bool(pdf_path),
                    "is_prep": is_document_only,
                    "type": "notebook",
                    "student_name": student_name
                }
                meta_path = os.path.join(base_dir, "outputs", f"{output_filename}.meta.json")
                meta_path_alt = os.path.join(base_dir, "outputs", f"notebook_{unique_id}.meta.json")
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump(sidecar_data, mf, ensure_ascii=False)
                if meta_path_alt != meta_path:
                    with open(meta_path_alt, "w", encoding="utf-8") as mf:
                        json.dump(sidecar_data, mf, ensure_ascii=False)
            except Exception as meta_e:
                logger.warning(f"Failed to write notebook sidecar meta: {meta_e}")
            if notebook_type == "course_podcast":
                history_meta.update({
                    "big_picture": big_picture,
                    "concept_breakdown": concept_breakdown,
                    "key_takeaways": key_takeaways,
                    "actionable_blueprint": actionable_blueprint,
                    "roadblocks_solutions": roadblocks_solutions,
                    "reflection_prompts": reflection_prompts,
                    "shareable_quotes": shareable_quotes,
                    "resources_tools": resources_tools,
                    "stats": stats
                })
            else:
                history_meta.update({
                    "cheat_sheet": cheat_sheet,
                    "mcqs": mcqs,
                    "comparisons": comparisons,
                    "study_strategy": study_strategy,
                    "tldr": tldr,
                    "stats": stats
                })
            log_id = use_lecture_quota(user_id, metadata=history_meta, cost=calculated_cost)

            download_url = f"/api/v1/notebook/download/{output_filename}"
            preview_url = f"/api/v1/notebook/download/{preview_filename}"
            
            done_payload = json.dumps({
                'type': 'done',
                'download_url': download_url,
                'preview_url': preview_url,
                'history_id': log_id,
                'title': ai_title
            }, ensure_ascii=False)
            yield f"data: {done_payload}\n\n"

        except Exception as e:
            logger.error(f"❌ Error during notebook process: {str(e)}", exc_info=True)
            log_usage_failure(user_id, f"Notebook failure: {str(e)}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'حدث خطأ غير متوقع: {str(e)}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-store",
        "Connection": "keep-alive",
        "Alt-Svc": "clear",
        "X-Content-Type-Options": "nosniff",
    })

@router.api_route("/download/{job_id}.pdf", methods=["GET", "HEAD"])
async def download_notebook(
    job_id: str,
    download: bool = False,
    token: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    is_prep: Optional[bool] = Query(None),
):
    """Download generated notebook PDF checking both backend and project root outputs.
    Regenerates the file dynamically from Supabase history metadata if missing from disk.
    Defaults to inline content-disposition for rich in-browser preview without auto-downloading.
    Includes Path Traversal protection, non-blocking background thread PDF regeneration,
    and accurate Arabic filename formatting:
    - [اسم المادة] - دفتر تحضير سلايدات - لمّاح.pdf (عند رفع الملف فقط بدون فويسات)
    - [اسم المادة] - دفتر مخطط فويسات - لمّاح.pdf (عند دمج الفويسات مع الملف)
    """
    # 1. Sanitize job_id against Path Traversal
    if not re.match(r'^[a-zA-Z0-9_\-]+$', job_id) or os.path.basename(job_id) != job_id:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    routes_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(routes_dir)))
    base_dir = os.path.dirname(backend_dir)
    
    # Normalize job_id for previews
    actual_job_id = job_id
    if job_id.startswith("preview_") and "notebook_" not in job_id:
        actual_job_id = job_id.replace("preview_", "preview_notebook_")
        
    path_1 = os.path.join(backend_dir, "outputs", f"{actual_job_id}.pdf")
    path_2 = os.path.join(base_dir, "outputs", f"{actual_job_id}.pdf")
    
    resolved_path = None
    if os.path.exists(path_1):
        resolved_path = path_1
    elif os.path.exists(path_2):
        resolved_path = path_2
        
    if not resolved_path:
        # File is missing from disk (likely due to container restart on ephemeral storage).
        # We will attempt to regenerate it on the fly using Supabase usage logs history metadata.
        logger.info(f"[DOWNLOAD] PDF file {actual_job_id}.pdf not found on disk. Attempting dynamic regeneration from Supabase...")
        try:
            # Extract unique_id
            if actual_job_id.startswith("preview_notebook_"):
                unique_id = actual_job_id.replace("preview_notebook_", "")
            elif actual_job_id.startswith("notebook_"):
                unique_id = actual_job_id.replace("notebook_", "")
            else:
                unique_id = actual_job_id
                
            db_job_id = f"notebook_{unique_id}"
            supabase = get_supabase()
            
            res = supabase.table("usage_logs").select("*").eq("metadata->>job_id", db_job_id).execute()
            if res.data and len(res.data) > 0 and isinstance(res.data[0], dict):
                log_entry: dict = res.data[0]
                metadata: dict = log_entry.get("metadata", {}) if isinstance(log_entry.get("metadata"), dict) else {}
                annotations: dict = metadata.get("annotations", {}) if isinstance(metadata.get("annotations"), dict) else {}
                markdown_content = annotations.get("markdown", "")
                student_name = metadata.get("student_name", "طالب متميز")
                ai_title = metadata.get("title", "ملزمة دراسية ممتازة")
                notebook_type = metadata.get("notebook_type", "university")
                stats = metadata.get("stats", None)
                
                if markdown_content:
                    original_pdf_path = os.path.join(base_dir, "outputs", f"notebook_{unique_id}.pdf")
                    preview_pdf_path = os.path.join(base_dir, "outputs", f"preview_notebook_{unique_id}.pdf")
                    
                    logger.info(f"[DOWNLOAD] Regenerating original PDF to {original_pdf_path} for type {notebook_type} via thread pool")
                    from app.services.aesthetic_notebook_service import generate_aesthetic_notebook_pdf, generate_course_podcast_notebook_pdf, generate_preview_pdf
                    
                    if notebook_type == "course_podcast":
                        await asyncio.to_thread(
                            generate_course_podcast_notebook_pdf,
                            output_path=original_pdf_path,
                            student_name=student_name,
                            lecture_title=ai_title,
                            big_picture=metadata.get("big_picture"),
                            concept_breakdown=metadata.get("concept_breakdown"),
                            key_takeaways=metadata.get("key_takeaways"),
                            actionable_blueprint=metadata.get("actionable_blueprint"),
                            roadblocks_solutions=metadata.get("roadblocks_solutions"),
                            reflection_prompts=metadata.get("reflection_prompts"),
                            shareable_quotes=metadata.get("shareable_quotes"),
                            resources_tools=metadata.get("resources_tools"),
                            stats=stats,
                        )
                    else:
                        await asyncio.to_thread(
                            generate_aesthetic_notebook_pdf,
                            output_path=original_pdf_path,
                            continuous_markdown=markdown_content,
                            student_name=student_name,
                            lecture_title=ai_title,
                            cheat_sheet=metadata.get("cheat_sheet"),
                            mcqs=metadata.get("mcqs"),
                            comparisons=metadata.get("comparisons"),
                            study_strategy=metadata.get("study_strategy"),
                            tldr=metadata.get("tldr"),
                            stats=stats,
                        )
                    
                    logger.info(f"[DOWNLOAD] Regenerating preview PDF to {preview_pdf_path} via thread pool")
                    await asyncio.to_thread(
                        generate_preview_pdf,
                        original_pdf_path=original_pdf_path,
                        preview_pdf_path=preview_pdf_path,
                        max_pages=5
                    )
                    
                    if os.path.exists(path_1):
                        resolved_path = path_1
                    elif os.path.exists(path_2):
                        resolved_path = path_2
                else:
                    logger.error(f"[DOWNLOAD] Found usage log for {db_job_id} but markdown content was empty.")
            else:
                logger.error(f"[DOWNLOAD] No usage log found in database for job_id={db_job_id}")
        except Exception as regen_err:
            logger.error(f"[DOWNLOAD] Failed to dynamically regenerate missing PDF: {str(regen_err)}", exc_info=True)
            
    if not resolved_path:
        logger.error(f"[DOWNLOAD] Notebook not found for job_id={job_id}. Checked paths: {path_1} and {path_2}")
        raise HTTPException(status_code=404, detail="File not found")

    # Determine title and prep status
    resolved_title = title
    resolved_is_prep = is_prep
    if resolved_is_prep is None and mode is not None:
        resolved_is_prep = mode.lower() in ["prep", "slides", "document", "document_only"]

    # 1. Check sidecar metadata file (.meta.json)
    meta_candidates = [
        os.path.join(backend_dir, "outputs", f"{actual_job_id}.meta.json"),
        os.path.join(base_dir, "outputs", f"{actual_job_id}.meta.json"),
        os.path.join(backend_dir, "outputs", f"{job_id}.meta.json"),
        os.path.join(base_dir, "outputs", f"{job_id}.meta.json"),
    ]
    for mp in meta_candidates:
        if os.path.exists(mp):
            try:
                with open(mp, "r", encoding="utf-8") as mf:
                    meta_data = json.load(mf)
                    if not resolved_title and meta_data.get("title"):
                        resolved_title = meta_data.get("title")
                    if resolved_is_prep is None and "is_prep" in meta_data:
                        resolved_is_prep = bool(meta_data["is_prep"])
                break
            except Exception as meta_err:
                logger.warning(f"Error reading notebook sidecar {mp}: {meta_err}")

    # 2. Check Supabase usage logs if title or mode still unresolved
    if not resolved_title or resolved_is_prep is None:
        try:
            clean_id = actual_job_id.replace("preview_notebook_", "").replace("notebook_", "").replace("preview_", "")
            db_job_id = f"notebook_{clean_id}"
            supabase = get_supabase()
            res = supabase.table("usage_logs").select("metadata").eq("metadata->>job_id", db_job_id).execute()
            if res.data and len(res.data) > 0 and isinstance(res.data[0], dict):
                raw_meta = res.data[0].get("metadata")
                sb_meta: dict = raw_meta if isinstance(raw_meta, dict) else {}
                if not resolved_title:
                    resolved_title = str(sb_meta.get("title", "")) if sb_meta.get("title") else None
                if resolved_is_prep is None:
                    if "is_prep" in sb_meta:
                        resolved_is_prep = bool(sb_meta["is_prep"])
                    elif "has_audio" in sb_meta:
                        resolved_is_prep = not bool(sb_meta["has_audio"])
                    else:
                        tr = str(sb_meta.get("transcript", ""))
                        resolved_is_prep = len(tr.strip()) == 0
        except Exception as sb_err:
            logger.warning(f"Supabase lookup failed for notebook {job_id}: {sb_err}")

    from app.services.filename_helper import format_arabic_download_filename
    arabic_filename = format_arabic_download_filename(
        title=resolved_title,
        is_notebook=True,
        is_prep=bool(resolved_is_prep)
    )

    disposition = "attachment" if download else "inline"
    headers = {
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
        "Accept-Ranges": "bytes",
    }
    return FileResponse(
        resolved_path,
        media_type="application/pdf",
        filename=arabic_filename,
        content_disposition_type=disposition,
        headers=headers,
    )


@router.api_route("/preview/{job_id}.pdf", methods=["GET", "HEAD"])
async def preview_notebook(
    job_id: str,
    token: Optional[str] = Query(None),
):
    """Explicit preview endpoint serving inline PDF."""
    return await download_notebook(job_id=job_id, download=False, token=token)

