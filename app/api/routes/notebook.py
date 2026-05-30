import os
import re
import uuid
import shutil
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, File, UploadFile, Form, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse

from app.core.auth_guard import check_premium_plans
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.subscription_service import use_lecture_quota, log_usage_failure, get_supabase
from app.services.link_ingest_service import download_audio_from_link
from app.core.queue_manager import queue_manager, JobStatus
from app.core.logger import logger
from app.services.ai.factory import get_llm_for_user
from app.services.aesthetic_notebook_service import generate_aesthetic_notebook_pdf
from langchain_openai import ChatOpenAI
from app.core.config import settings

router = APIRouter()

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
    profile=Depends(check_premium_plans)
):
    """
    Aesthetic ruled notebook generator endpoint for Premium users.
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

    active_sub = profile.get("active_subscription")
    plan_name = active_sub.get("subscription_plans", {}).get("name", "شهري - برو") if active_sub else "شهري - برو"
    # Use the form-submitted name if provided and non-empty, otherwise fall back to profile name
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
                
            if os.path.exists(processed_p):
                logger.info(f"[NOTEBOOK] Found processed audio: {processed_p}")
                audio_paths.append(processed_p)
            elif os.path.exists(temp_path):
                logger.info(f"[NOTEBOOK] Found original audio: {temp_path}")
                audio_paths.append(temp_path)
            else:
                raise HTTPException(status_code=400, detail="temp_file_missing")
                
    elif audio_file:
        os.makedirs(upload_dir, exist_ok=True)
        audio_path = os.path.join(upload_dir, f"{unique_id}_{audio_file.filename}")
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)
        audio_paths.append(audio_path)
        
    elif lecture_urls or lecture_url:
        pass
    else:
        raise HTTPException(status_code=400, detail="Missing audio source")

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
        try:
            # 0. Header padding
            yield ":" + " " * 1024 + "\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🚀 جاري تحضير دفترك الدراسي الفاخر...'}, ensure_ascii=False)}\n\n"
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
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '📥 تم استلام وتجهيز الملفات.'}, ensure_ascii=False)}\n\n"

            if not audio_paths:
                yield f"data: {json.dumps({'type': 'error', 'message': 'لم يتم العثور على أي ملف صوتي أو فشل تحميل الروابط.'}, ensure_ascii=False)}\n\n"
                return

            # calculate total duration in minutes
            total_seconds = 0.0
            import subprocess
            import math
            
            yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '📏 جاري حساب مدة المحاضرة وحصتك المتبقية...'}, ensure_ascii=False)}\n\n"
            
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

            from app.services.subscription_service import get_user_subscription_info
            sub_info = get_user_subscription_info(user_id)
            remaining_quota = sub_info.get("quota_remaining", 0)
            
            if remaining_quota < calculated_cost:
                yield f"data: {json.dumps({'type': 'error', 'message': f'🚫 رصيدك الحالي ({remaining_quota} دقيقة) لا يكفي لتوليد الدفتر لهذه الملفات الصوتية (المطلوب: {calculated_cost} دقيقة). يرجى شحن رصيدك أو ترقية اشتراكك.'}, ensure_ascii=False)}\n\n"
                return

            # 2. Queueing
            yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '🎟️ في طابور المعالجة الممتازة...'}, ensure_ascii=False)}\n\n"
            representative_path = audio_paths[0] if audio_paths else None
            job = await queue_manager.enqueue(
                user_id=user_id,
                plan_name=plan_name,
                payload={"audio_path": representative_path, "pdf_path": pdf_path, "unique_id": unique_id, "is_audio_trimmed": False}
            )

            while job.status == JobStatus.QUEUED:
                await asyncio.sleep(4)

            # 3. Transcribing
            yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'message': '🎧 جاري استماع وتفريغ كلام المحاضر بدقة متناهية...'}, ensure_ascii=False)}\n\n"
            transcripts = []
            for idx, path in enumerate(audio_paths):
                if len(audio_paths) > 1:
                    yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'message': f'🎧 جاري تفريغ ملف الصوت #{idx + 1} من {len(audio_paths)}...'}, ensure_ascii=False)}\n\n"
                t = await asyncio.to_thread(transcribe_audio, path)
                if t:
                    if len(audio_paths) > 1:
                        transcripts.append(f"--- [محتوى الملف الصوتي #{idx + 1}] ---\n{t}\n")
                    else:
                        transcripts.append(t)
            
            transcript = "\n\n".join(transcripts)

            # 4. Context Extraction
            pdf_context = []
            if pdf_path:
                yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'message': '📑 جاري تحليل واستخلاص محتوى الشرائح...'}, ensure_ascii=False)}\n\n"
                if pdf_path.lower().endswith(".pptx"):
                    from app.services.pptx_converter import extract_text_from_pptx
                    pptx_pages = await asyncio.to_thread(extract_text_from_pptx, pdf_path)
                    logger.info(f"[NOTEBOOK] PPTX extracted {len(pptx_pages)} slides from {pdf_path}")
                    pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pptx_pages[:150]]
                else:
                    pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                    logger.info(f"[NOTEBOOK] PDF extracted {len(pages)} pages from {pdf_path}")
                    pdf_context = [p['text'] for p in pages[:150]]

                if not pdf_context:
                    logger.warning(f"[NOTEBOOK] pdf_context is EMPTY after extraction — file may be image-based or corrupted: {pdf_path}")

            # 5. Lammah AI Engine Call
            num_sources = len(audio_paths)
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': f'🧠 محرك لمّاح الذكي يُرتّب ويُنسّق {num_sources} مصدر صوتي في ملزمة متكاملة...'}, ensure_ascii=False)}\n\n"
            
            # Instantiate Lammah AI Engine
            llm = get_notebook_llm(user_id)
            
            # Title Generation
            ai_title = lecture_title
            if not ai_title or len(ai_title.strip()) < 2:
                sample_text = transcript[:2000] if transcript else ""
                title_prompt = (
                    "اقترح عنواناً دراسياً احترافياً وموجزاً جداً باللغة العربية (3-5 كلمات) "
                    "لهذه المحاضرة بدون أي مقدمات أو علامات تنصيص:\n\n" + sample_text
                )
                try:
                    title_response = await asyncio.to_thread(llm.invoke, title_prompt)
                    ai_title = title_response.content.strip().replace('"', '').replace('«', '').replace('»', '')
                except Exception as e:
                    logger.warning(f"Title generation failed, falling back: {e}")
                    fallback_llm = get_llm_for_user(user_id)
                    title_response = await asyncio.to_thread(fallback_llm.invoke, title_prompt)
                    ai_title = title_response.content.strip().replace('"', '')

            # Compute depth instruction based on transcript length & source count
            word_count = len(transcript.split())
            depth_note = ""
            if num_sources > 1:
                depth_note = (
                    f"⚠️ تحذير صارم وهام جداً بخصوص دمج التسجيلات الصوتية المتعددة:\n"
                    f"- تم رفع ودمج عدد ({num_sources}) ملفات صوتية مختلفة لهذه الملزمة.\n"
                    f"- يجب عليك قراءة التفريغ النصي لكل ملف صوتي بشكل كامل وتغطية كل محتوياته وتفاصيله وأفكاره بدقة متناهية دون إهمال أو حذف أي جزء منها!\n"
                    f"- قم بتقسيم الملزمة هيكلياً إلى فصول أو أقسام واضحة ومستقلة تمثل كافة هذه التسجيلات بالترتيب (مثال: الفصل الأول يغطي الملف الصوتي الأول، والفصل الثاني يغطي الملف الصوتي الثاني وهكذا).\n"
                    f"- احرص على ألا تضيع أي معلومة أو شرح ورد في أي تسجيل صوتي من التسجيلات المرفوعة، ولا تقم باختصار أو دمج الفصول بشكل يخل بالتفاصيل."
                )
            elif word_count > 5000:
                depth_note = "المحاضرة طويلة ومفصلة، غطِّ كل نقطة بعمق وشمولية تامة. "

            # Calculate target length instruction dynamically based on transcript word count
            if word_count < 1000:
                length_instruction = (
                    "RULE 6 — ADAPTIVE LENGTH: Since the lecture is short, write a concise but comprehensive notebook. "
                    "The English section should be around 400-600 words, and the Arabic section around 400-600 words. "
                    "Do not compromise on quality, but do not force artificial fluff."
                )
            elif word_count < 3000:
                length_instruction = (
                    "RULE 6 — ADAPTIVE LENGTH: Write a moderately detailed notebook. "
                    "The English section should be around 800-1200 words, and the Arabic section around 800-1200 words. "
                    "Ensure everything explained in the audio is covered."
                )
            else:
                length_instruction = (
                    "RULE 6 — ADAPTIVE LENGTH: The lecture is very detailed. Write an extremely comprehensive notebook. "
                    "The English section should be at least 1500-2500 words, and the Arabic section at least 1500-2500 words. "
                    "Cover every sub-concept and explanation thoroughly."
                )

            # STEP 1: Generate comprehensive bilingual Markdown study notebook
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': '🧠 محرك لمّاح الذكي يكتب محتوى الدفتر الدراسي ثنائي اللغة بالتفصيل...'}, ensure_ascii=False)}\n\n"

            prompt_step1 = (
                "أنت 'محرك لمّاح الدراسي' — ذكاء اصطناعي متخصص في إعداد ملازم ودفاتر دراسية جامعية احترافية فائقة الدقة والشمولية والروعة للبيع والتسويق.\n"
                f"{depth_note}\n\n"
                "══════════════════════════════════════\n"
                "🚨 STRICT BILINGUAL STRUCTURE — MANDATORY RULES (100% ENFORCED) 🚨\n"
                "══════════════════════════════════════\n"
                "\n"
                "RULE 1 — TWO COMPLETELY SEPARATE SECTIONS, SEQUENTIAL ORDER:\n"
                "  ▶ SECTION 1: Complete English Study Notebook\n"
                "    - Write the ENTIRE first section in English ONLY.\n"
                "    - Cover EVERY idea, concept, example, and detail from the transcript with maximum depth.\n"
                "    - Use ## for chapter headings, ### for sub-sections, and bullet points / numbered lists throughout.\n"
                "    - Mark important terms: <mark>term</mark>\n"
                "    - Use English callouts: 💡 Note: / ⚠ Exam Alert: / 📌 Remember:\n"
                "    - DO NOT write a single Arabic word in this section.\n"
                "    - The English section must be at least 1200 words long and deeply detailed.\n"
                "\n"
                "  ▶ SECTION 2: الترجمة والشرح الكامل بالعربية\n"
                "    - يبدأ هذا القسم فقط بعد انتهاء القسم الإنجليزي بالكامل.\n"
                "    - اكتب هذا القسم بالكامل باللغة العربية الفصحى فقط دون أي كلمات إنجليزية.\n"
                "    - نفس العناوين والترتيب والأقسام الموجودة في القسم الإنجليزي تماماً (ترجمة متوازية).\n"
                "    - ## للعناوين الرئيسية، ### للعناوين الفرعية، ونقاط مخرزة وقوائم مرقمة في كل مكان.\n"
                "    - ضع المصطلحات المهمة داخل: <mark>المصطلح</mark>\n"
                "    - استخدم التنبيهات العربية: 💡 ملاحظة: / ⚠ هام للامتحان: / 📌 تذكّر:\n"
                "    - القسم العربي يجب ألا يقل عن 1200 كلمة أيضاً.\n"
                "\n"
                "RULE 2 — NO LINE-BY-LINE MIXING: NEVER alternate between Arabic and English within the body.\n"
                "RULE 3 — NO NUMBERED HEADINGS (avoid '1.1' or '2.'). Use clean Markdown: ## Title (not ## 2. Title).\n"
                "RULE 4 — NO DENSE PARAGRAPHS: Break everything into bullet points and numbered lists.\n"
                "RULE 5 — NO 'بروفيسور': Always use 'الدكتور' or 'المحاضر'.\n"
                f"{length_instruction}\n"
                "RULE 7 — RICHNESS: Every section must have sub-points, examples, and explanations. No skeleton outlines.\n"
                "\n"
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
                markdown_content = ai_response.content.strip()
            except Exception as e:
                logger.warning(f"[NOTEBOOK] Call 1: Lammah AI Engine failed: {e}. Falling back to Gemini 2.0...")
                fallback_llm = get_llm_for_user(user_id)
                ai_response = await asyncio.to_thread(fallback_llm.invoke, prompt_step1)
                markdown_content = ai_response.content.strip()

            # STEP 2: Generate structured cheat_sheet, mcqs, and comparisons based on the generated markdown
            yield f"data: {json.dumps({'type': 'progress', 'step': '4.5/5', 'message': '🧠 محرك لمّاح يستخلص خلاصة ليلة الامتحان وأسئلة المراجعة...'}, ensure_ascii=False)}\n\n"

            prompt_step2 = (
                "أنت 'محرك لمّاح الدراسي' — ذكاء اصطناعي متخصص في استخلاص المخرجات المهيكلة والتلخيصات الذكية للملازم الدراسية.\n"
                "بناءً على نص الدفتر الدراسي (Markdown) المرفق بالأسفل، يجب عليك توليد مخرجات JSON تحتوي بدقة على المفاتيح التالية (cheat_sheet, mcqs, comparisons) ودون حذف أو تجاهل أي منها:\n"
                "1. خلاصة ليلة الامتحان (cheat_sheet): حصر جميع القوانين والمصطلحات والرموز الواردة في الدفتر مع تعريف مختصر من سطر واحد (من 10 إلى 25 مصطلحاً).\n"
                "2. جداول المقارنة الذكية (comparisons): جدول مقارنة ذكي واحد على الأقل يقارن بين أهم مفهومين أو وسيلتين أو قانونين في الدفتر. ممنوع إبقاء هذا الحقل فارغاً.\n"
                "3. أسئلة اختبر نفسك (mcqs): ما لا يقل عن 5 أسئلة اختيار من متعدد خياراتها (أ، ب، ج، د) وتغطي كامل الدفتر بشكل ذكي ودقيق.\n\n"
                "══════════════════════════════════════\n"
                "⚠️ قواعد المخرجات الإلزامية:\n"
                "══════════════════════════════════════\n"
                "يجب عليك إرجاع المخرجات بصيغة JSON فقط ولا تكتب أي كود أو تعليق خارج الـ JSON. "
                "يجب أن يكون الـ JSON صالحاً ومطابقاً للنموذج التالي 100%:\n\n"
                "{\n"
                '  "cheat_sheet": [\n'
                '    {"term": "مصطلح أو قانون", "brief_definition": "تعريف بسطر واحد وموجز جداً"}\n'
                '  ],\n'
                '  "mcqs": [\n'
                '    {\n'
                '      "question": "نص السؤال الشامل من صلب المحاضرة",\n'
                '      "choices": ["أ) ...", "ب) ...", "ج) ...", "د) ..."],\n'
                '      "answer": "أ"\n'
                '    }\n'
                '  ],\n'
                '  "comparisons": [\n'
                '    {\n'
                '      "title": "عنوان المقارنة الذكية",\n'
                '      "headers": ["المفهوم الأول", "المفهوم الثاني"],\n'
                '      "rows": [["وجه المقارنة", "قيمة أ", "قيمة ب"]]\n'
                '    }\n'
                '  ]\n'
                "}\n\n"
                "══════════════════════════════════════\n"
                "نص الدفتر الدراسي (Markdown) المستخلص:\n"
                "══════════════════════════════════════\n"
                f"{markdown_content}\n"
            )

            cheat_sheet = []
            mcqs = []
            comparisons = []
            raw_json_content = ""

            try:
                logger.info("[NOTEBOOK] Call 2: Requesting structured JSON from Lammah AI Engine...")
                ai_response_json = await asyncio.to_thread(llm.invoke, prompt_step2)
                raw_json_content = ai_response_json.content.strip()
            except Exception as e:
                logger.warning(f"[NOTEBOOK] Call 2: Lammah AI Engine failed: {e}. Falling back to Gemini 2.0...")
                fallback_llm = get_llm_for_user(user_id)
                ai_response_json = await asyncio.to_thread(fallback_llm.invoke, prompt_step2)
                raw_json_content = ai_response_json.content.strip()

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
                cheat_sheet = parsed.get("cheat_sheet", [])
                mcqs = parsed.get("mcqs", [])
                comparisons = parsed.get("comparisons", [])
                logger.info(f"[NOTEBOOK] Call 2 Parsed: {len(cheat_sheet)} cheat items, {len(mcqs)} MCQs, {len(comparisons)} comparisons.")
            except Exception as parse_err:
                logger.error(f"[NOTEBOOK] Call 2 JSON parse failed: {parse_err}. Raw JSON content: {raw_json_content}")

            # 6. Render Premium PDF
            yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'message': '🎨 جاري رسم الدفتر الاحترافي بكل الميزات الجديدة...'}, ensure_ascii=False)}\n\n"
            
            output_filename = f"notebook_{unique_id}.pdf"
            preview_filename = f"preview_notebook_{unique_id}.pdf"
            output_pdf_path = os.path.join(base_dir, "outputs", output_filename)
            preview_pdf_path = os.path.join(base_dir, "outputs", preview_filename)
            
            from app.services.aesthetic_notebook_service import generate_aesthetic_notebook_pdf, generate_preview_pdf
            
            await asyncio.to_thread(
                generate_aesthetic_notebook_pdf,
                output_path=output_pdf_path,
                continuous_markdown=markdown_content,
                student_name=student_name,
                lecture_title=ai_title,
                cheat_sheet=cheat_sheet,
                mcqs=mcqs,
                comparisons=comparisons,
            )
            
            await asyncio.to_thread(
                generate_preview_pdf,
                original_pdf_path=output_pdf_path,
                preview_pdf_path=preview_pdf_path,
                max_pages=3
            )

            # 7. Quota Deduction & History Save
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
                "cheat_sheet": cheat_sheet,
                "mcqs": mcqs,
                "comparisons": comparisons
            }
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
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    })

@router.get("/download/{job_id}.pdf")
def download_notebook(job_id: str):
    """Download generated notebook PDF checking both backend and project root outputs.
    Regenerates the file dynamically from Supabase history metadata if missing from disk.
    """
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
            if res.data and len(res.data) > 0:
                log_entry = res.data[0]
                metadata = log_entry.get("metadata", {})
                annotations = metadata.get("annotations", {})
                markdown_content = annotations.get("markdown", "")
                student_name = metadata.get("student_name", "طالب متميز")
                ai_title = metadata.get("title", "ملزمة دراسية ممتازة")
                cheat_sheet = metadata.get("cheat_sheet", None)
                mcqs = metadata.get("mcqs", None)
                comparisons = metadata.get("comparisons", None)
                
                if markdown_content:
                    original_pdf_path = os.path.join(base_dir, "outputs", f"notebook_{unique_id}.pdf")
                    preview_pdf_path = os.path.join(base_dir, "outputs", f"preview_notebook_{unique_id}.pdf")
                    
                    logger.info(f"[DOWNLOAD] Regenerating original PDF to {original_pdf_path}")
                    from app.services.aesthetic_notebook_service import generate_aesthetic_notebook_pdf, generate_preview_pdf
                    
                    generate_aesthetic_notebook_pdf(
                        output_path=original_pdf_path,
                        continuous_markdown=markdown_content,
                        student_name=student_name,
                        lecture_title=ai_title,
                        cheat_sheet=cheat_sheet,
                        mcqs=mcqs,
                        comparisons=comparisons,
                    )
                    
                    logger.info(f"[DOWNLOAD] Regenerating preview PDF to {preview_pdf_path}")
                    generate_preview_pdf(
                        original_pdf_path=original_pdf_path,
                        preview_pdf_path=preview_pdf_path,
                        max_pages=3
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
        
    return FileResponse(resolved_path, media_type="application/pdf", filename=f"دفتر_{job_id}.pdf")
