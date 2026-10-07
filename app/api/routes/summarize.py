from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Request, Form, BackgroundTasks, Query
from fastapi.responses import StreamingResponse, FileResponse
import shutil
import os
import uuid
import traceback
import json
import asyncio
import subprocess
import re
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.ai.orchestrator import AIOrchestrator
from app.services.subscription_service import use_lecture_quota, log_usage_failure, get_supabase, refund_lecture_quota
from app.services.link_ingest_service import download_audio_from_link
from app.core.auth_guard import check_quota, verify_phone_active, get_user_profile, verify_token, verify_admin
from app.core.queue_manager import queue_manager, JobStatus
from app.core.logger import logger
from app.services.ai.factory import get_llm_for_user

router = APIRouter()

async def generate_ai_title(llm, transcript: str, pdf_context: Optional[List[str]] = None) -> str:
    from datetime import datetime
    try:
        sample_text = ""
        if transcript:
            sample_text = transcript[:4000]
        elif pdf_context:
            sample_text = "\n".join(pdf_context)[:4000]
            
        if not sample_text:
            return f"محاضرة {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        title_prompt = (
            "أنت خبير تلخيص وعنونة محاضرات ومستندات. بناءً على هذا النص، "
            "استنتج عنواناً باللغة العربية يكون موجزاً ومحترفاً جداً ولا يتجاوز 3 إلى 5 كلمات للمحاضرة/الملف. "
            "لا تضع أي مقدمات، جمل تمهيدية، علامات تنصيص، أو نقطة في النهاية. "
            "أعطني العنوان مباشرة فقط.\n\n"
            f"النص:\n{sample_text}"
        )
        title_response = await asyncio.to_thread(llm.invoke, title_prompt)
        ai_title = title_response.content.strip().replace('"', '').replace('«', '').replace('»', '')
        ai_title = re.sub(r'^(العنوان:|العنوان المقترح:|عنوان المحاضرة:|العنوان هو:)\s*', '', ai_title, flags=re.IGNORECASE).strip()
        if len(ai_title) > 1:
            return ai_title
    except Exception as e:
        logger.error(f"Error generating AI title: {e}")
    return f"محاضرة {datetime.now().strftime('%Y-%m-%d %H:%M')}"

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

class LectureRow(BaseModel):
    row_id: str
    audio_temp_id: Optional[str] = None
    pdf_temp_id: Optional[str] = None
    lecture_url: Optional[str] = None

class BatchRequest(BaseModel):
    lectures: List[LectureRow]

async def background_worker(row: LectureRow, user_id: str, plan_name: str, history_id: str, is_free_trial: bool):
    """
    Background worker that processes a single lecture from a batch request.
    Updates progress in Supabase usage_logs table.
    """
    supabase = get_supabase()
    def update_progress(step: str, message: str, is_error: bool = False, is_done: bool = False, result: Optional[dict] = None):
        try:
            # Re-fetch current metadata to avoid overwriting other fields
            res = supabase.table("usage_logs").select("metadata").eq("id", history_id).execute()
            meta: dict[str, Any] = {}
            if res.data and isinstance(res.data[0], dict):
                raw_meta = res.data[0].get("metadata")
                if isinstance(raw_meta, dict):
                    meta = raw_meta
            
            meta["progress_step"] = step
            meta["message"] = message
            
            if result:
                # Ensure result keys are at the top level of metadata
                meta.update(result)
            
            status = "success"
            if is_error: status = "failed"
            
            supabase.table("usage_logs").update({
                "status": status,
                "metadata": meta
            }).eq("id", history_id).execute()
            logger.info(f"[BG_WORKER] Progress updated: {step} for {history_id}")
        except Exception as e:
            logger.error(f"Error updating background progress: {e}")

    audio_path, pdf_path = None, None
    try:
        update_progress("1/5", "جاري تحضير المحرك وتحميل الملفات...")
        unique_id = str(uuid.uuid4())[:8]
        # Project Root is 5 levels up: app/api/routes/summarize.py -> routes -> api -> app -> backend -> root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        upload_dir_env = os.getenv("UPLOAD_DIR", "backend/uploads")
        temp_dir_env = os.getenv("TEMP_DIR", "uploader-go/uploads/temp")
        upload_dir = upload_dir_env if os.path.isabs(upload_dir_env) else os.path.normpath(os.path.join(base_dir, upload_dir_env))
        temp_dir = temp_dir_env if os.path.isabs(temp_dir_env) else os.path.normpath(os.path.join(base_dir, temp_dir_env))
        os.makedirs(upload_dir, exist_ok=True)

        # 1. Prepare Audio
        if row.audio_temp_id:
            temp_p = os.path.join(temp_dir, row.audio_temp_id)
            if not os.path.exists(temp_p):
                # Fallback: check if Go Uploader processed it
                base, _ = os.path.splitext(row.audio_temp_id)
                processed_p = os.path.join(temp_dir, f"{base}_processed.mp3")
                if os.path.exists(processed_p):
                    temp_p = processed_p
                    row.audio_temp_id = f"{base}_processed.mp3"
                else:
                    raise Exception("Audio file missing from temp.")
            audio_path = os.path.join(upload_dir, f"{unique_id}_{row.audio_temp_id}")
            shutil.move(temp_p, audio_path)
        elif row.lecture_url:
            update_progress("1/5", "🔗 جاري تحميل المحاضرة من الرابط...")
            audio_path = await asyncio.to_thread(download_audio_from_link, row.lecture_url.strip(), upload_dir, unique_id)
        
        # 2. Prepare PDF
        if row.pdf_temp_id:
            temp_p = os.path.join(temp_dir, row.pdf_temp_id)
            if os.path.exists(temp_p):
                pdf_path = os.path.join(upload_dir, f"{unique_id}_{row.pdf_temp_id}")
                shutil.move(temp_p, pdf_path)
                
                # If PPTX, immediately convert to PDF as the canonical single source of truth
                if pdf_path.lower().endswith((".pptx", ".ppt")):
                    update_progress("1/5", "📑 جاري تحويل ملف البوربوينت إلى PDF لضمان مطابقة الشرائح 100%...")
                    from app.services.pptx_converter import convert_pptx_to_pdf
                    pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, upload_dir)
                    logger.info(f"[BG_WORKER] PPTX converted immediately to PDF: {pdf_path}")

        is_document_only = (audio_path is None)

        if not audio_path and not pdf_path:
            raise Exception("No source files provided. Please upload either audio or a document.")

        is_audio_trimmed = False

        # 4. Queue and Process
        update_progress("2/5", "🎟️ في طابور المعالجة...")
        job = await queue_manager.enqueue(
            user_id=user_id,
            plan_name=plan_name,
            payload={"audio_path": audio_path, "pdf_path": pdf_path, "unique_id": unique_id, "is_audio_trimmed": is_audio_trimmed}
        )

        while job.status == JobStatus.QUEUED:
            await asyncio.sleep(4)
        
        # 5. Transcription (skip if document-only)
        if is_document_only:
            transcript = ""
        else:
            update_progress("3/5", "🎧 جاري تحويل الكلام إلى نص بدقة عالية...")
            if not audio_path:
                raise ValueError("لم يتم توفير ملف صوتي للمعالجة")
            transcript = await asyncio.to_thread(transcribe_audio, audio_path)
        
        # 6. PDF and AI
        pdf_context = []
        if pdf_path:
            update_progress("3/5", "🔍 جاري تحليل محتوى الشرائح بدقة...")
            pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
            logger.info(f"[BG_WORKER] PDF extracted {len(pages)} pages from {pdf_path}")
            pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pages]


        if is_document_only:
            update_progress("4/5", "🧠 لمّاح يخطط الشرائح ويشرحها بالعامية ويترجم المصطلحات...")
        else:
            update_progress("4/5", "🧠 لمّاح يفكّر الآن... جاري استنتاج أهم النقاط...")
        orchestrator = AIOrchestrator()
        llm = get_llm_for_user(user_id)
        annotations = await orchestrator.generate_complete_annotations(llm, transcript, pdf_context)

        # 7. Annotation and Finalize
        download_url = None
        if pdf_path:
            update_progress("5/5", "🎨 اللمسات الأخيرة! جاري تلوين الشرائح...")
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            output_pdf_path = os.path.join(base_dir, "outputs", f"{job.job_id}.pdf")
            os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
            
            from app.services.pdf_annotator import annotate_pdf
            await asyncio.to_thread(annotate_pdf, pdf_path, output_pdf_path, annotations, False)
            download_url = f"/api/v1/summarize/download/{job.job_id}.pdf"


        # Generate AI Title
        ai_title = await generate_ai_title(llm, transcript, pdf_context)

        # Save sidecar metadata for downloads
        if pdf_path:
            try:
                meta_path = os.path.join(base_dir, "outputs", f"{job.job_id}.meta.json")
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump({
                        "title": ai_title,
                        "has_audio": bool(audio_path),
                        "has_pdf": bool(pdf_path),
                        "is_prep": not bool(audio_path),
                        "type": "summarize"
                    }, mf, ensure_ascii=False)
            except Exception as meta_e:
                logger.warning(f"Failed to write sidecar meta for {job.job_id}: {meta_e}")

        update_progress("تم", "✅ المعالجة اكتملت بنجاح", is_done=True, result={
            "title": ai_title,
            "transcript": transcript,
            "annotations": annotations,
            "downloadUrl": download_url,
            "has_pdf": bool(pdf_path)
        })

        if job:
            queue_manager.mark_job_completed(job.job_id, {"status": "success"})

    except Exception as e:
        logger.error(f"BACKGROUND WORKER ERROR: {e}")
        update_progress("خطأ", f"❌ فشل: {str(e)}", is_error=True)
        refund_lecture_quota(user_id, history_id, str(e))
    finally:
        try:
            if audio_path and os.path.exists(audio_path): os.remove(audio_path)
            if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
        except: pass

@router.post("/batch-process", dependencies=[Depends(verify_phone_active)])
async def batch_process(req: BatchRequest, bg_tasks: BackgroundTasks, profile=Depends(get_user_profile)):
    """
    Triggers background processing for multiple lectures.
    """
    user_id = profile["id"]
    
    from app.services.subscription_service import get_user_subscription_info
    sub_info = get_user_subscription_info(user_id)
    quota = sub_info.get("quota_remaining", 0)
    
    if len(req.lectures) > quota:
        raise HTTPException(status_code=403, detail=f"رصيدك الحالي ({quota}) لا يكفي لمعالجة ({len(req.lectures)}) ملفات.")

    plan_name = "مجاني"
    is_free_trial = False
    if sub_info.get("active_subscription"):
        plan_name = sub_info["active_subscription"].get("subscription_plans", {}).get("name", "مجاني")

    results = []
    supabase = get_supabase()

    for row in req.lectures:
        initial_meta = {"progress_step": "0/5", "message": "بانتظار البدء...", "row_id": row.row_id}
        history_id = use_lecture_quota(user_id, metadata=initial_meta)
        
        if not history_id:
            continue
            
        supabase.table("usage_logs").update({"status": "processing"}).eq("id", str(history_id)).execute()
        
        bg_tasks.add_task(background_worker, row, user_id, plan_name, str(history_id), is_free_trial)
        results.append({"row_id": row.row_id, "history_id": str(history_id)})
    
    return {"status": "success", "jobs": results}

@router.get("/status/{history_id}")
async def get_job_status(history_id: str):
    """Pollable endpoint for background job status."""
    supabase = get_supabase()
    res = supabase.table("usage_logs").select("*").eq("id", history_id).execute()
    if not res.data or not isinstance(res.data[0], dict):
        raise HTTPException(status_code=404, detail="Job not found")
        
    log: dict[str, Any] = res.data[0]
    raw_meta = log.get("metadata", {})
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    status = str(log.get("status", ""))
    
    ui_status = status
    if status == "success" and meta.get("progress_step") != "تم":
        ui_status = "processing"
    elif status == "success" and meta.get("progress_step") == "تم":
        ui_status = "completed"
    
    return {
        "status": ui_status,
        "progress_step": meta.get("progress_step", ""),
        "message": meta.get("message", ""),
        "result": {
            "transcript": meta.get("transcript", ""),
            "annotations": meta.get("annotations", {}),
            "downloadUrl": meta.get("downloadUrl", ""),
            "chat_count": log.get("chat_count", 0)
        } if ui_status == "completed" else None,
        "error": meta.get("error", None)
    }

@router.post("/process-batch-lectures")
async def process_batch_lectures(
    request: Request,
    pdf_file: Optional[UploadFile] = File(None),
    pdf_temp_id: Optional[str] = Form(None),
    lecture_urls: Optional[str] = Form(None),   # Comma separated
    audio_temp_ids: Optional[str] = Form(None), # Comma separated
    profile=Depends(check_quota)
):
    """
    New dedicated endpoint for processing multiple links and files simultaneously.
    Provides a massive combined transcript for a single PDF.
    """
    logger.info(f"🚀 [BATCH_LECTURE] Request received: urls={lecture_urls}, temp_ids={audio_temp_ids}")
    is_free_trial = False
    unique_id = str(uuid.uuid4())[:8]
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    upload_dir_env = os.getenv("UPLOAD_DIR", "backend/uploads")
    temp_dir_env = os.getenv("TEMP_DIR", "uploader-go/uploads/temp")
    upload_dir = upload_dir_env if os.path.isabs(upload_dir_env) else os.path.normpath(os.path.join(base_dir, upload_dir_env))
    temp_dir = temp_dir_env if os.path.isabs(temp_dir_env) else os.path.normpath(os.path.join(base_dir, temp_dir_env))
    
    # Parse inputs
    urls_list = [u.strip() for u in lecture_urls.split(",")] if lecture_urls else []
    
    # Enforce YouTube links only, explicitly ban Google Drive & Zoom
    for url in urls_list:
        lower_url = url.lower()
        if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
            raise HTTPException(status_code=400, detail={
                "user_message": "🚫 عذراً، روابط Google Drive غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥"
            })
        if "zoom.us" in lower_url or "zoom.com" in lower_url:
            raise HTTPException(status_code=400, detail={
                "user_message": "🚫 عذراً، روابط Zoom غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥"
            })

    temp_ids_list = [t.strip() for t in audio_temp_ids.split(",")] if audio_temp_ids else []
    
    if not urls_list and not temp_ids_list:
        raise HTTPException(status_code=400, detail={
            "user_message": "🚫 لم يتم استلام أي تسجيلات صوتية صالحة. يرجى الانتظار حتى اكتمال رفع كافة الملفات الصوتية والمحاولة مجدداً."
        })
        
    # Handle PDF — wait up to 10 seconds for it to appear from Go Uploader shared volume
    pdf_path = None
    if pdf_temp_id:
        pdf_path = os.path.join(temp_dir, pdf_temp_id)
        logger.info(f"[DEBUG] [BATCH] Looking for PDF temp file: {pdf_path}")
        max_pdf_wait = 10
        pdf_waited = 0
        while not os.path.exists(pdf_path) and pdf_waited < max_pdf_wait:
            await asyncio.sleep(1)
            pdf_waited += 1
        if os.path.exists(pdf_path):
            logger.info(f"[DEBUG] [BATCH] Found PDF: {pdf_path} ({os.path.getsize(pdf_path)} bytes)")
        else:
            logger.warning(f"[WARN] [BATCH] PDF not found after {max_pdf_wait}s: {pdf_path} — continuing without.")
            pdf_path = None
    elif pdf_file:
        os.makedirs(upload_dir, exist_ok=True)
        pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_file.filename}")
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(pdf_file.file, buffer)

    async def event_stream():
        nonlocal pdf_path
        audio_paths = []
        try:
            # 0. Flush initial headers
            yield ":" + " " * 1024 + "\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🚀 جاري استقبال الملفات المتعددة وبدء الدمج...'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.1)

            # 1. Download URLs (if any) and append to audio_paths
            if urls_list:
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🔗 جاري تحميل المحاضرات من الروابط...'}, ensure_ascii=False)}\n\n"
                for idx, url in enumerate(urls_list):
                    try:
                        dl_path = await asyncio.to_thread(download_audio_from_link, url, upload_dir, f"{unique_id}_{idx}")
                        audio_paths.append(dl_path)
                    except Exception as e:
                        logger.error(f"Failed to download audio from {url}: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                        return

            # 2. Resolve temp IDs (if any) and append to audio_paths
            if temp_ids_list:
                for idx, temp_id in enumerate(temp_ids_list):
                    temp_path = os.path.join(temp_dir, temp_id)
                    base, _ = os.path.splitext(temp_id)
                    processed_p = os.path.join(temp_dir, f"{base}_processed.mp3")
                    
                    # Sync check
                    max_wait = 45
                    waited = 0
                    while not os.path.exists(temp_path) and not os.path.exists(processed_p) and waited < max_wait:
                        await asyncio.sleep(1)
                        waited += 1
                        
                    if os.path.exists(processed_p):
                        audio_paths.append(processed_p)
                    elif os.path.exists(temp_path):
                        audio_paths.append(temp_path)
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'الملف المؤقت {temp_id} غير موجود.'}, ensure_ascii=False)}\n\n"
                        return

            if not audio_paths:
                yield f"data: {json.dumps({'type': 'error', 'message': 'لم يتم العثور على أي ملف صوتي أو فشل تحميل الروابط.'}, ensure_ascii=False)}\n\n"
                return

            # 3. Calculate total duration and early quota check
            total_seconds = 0.0
            import subprocess
            import math
            
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📏 جاري حساب مدة المحاضرات وحصتك المتبقية...'}, ensure_ascii=False)}\n\n"
            
            audio_durations = []
            for path in audio_paths:
                duration_seconds = 0.0
                try:
                    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
                    duration_str = (await asyncio.to_thread(subprocess.check_output, cmd)).decode().strip()
                    duration_seconds = float(duration_str)
                except Exception as e:
                    logger.warning(f"ffprobe failed for {path}: {e}. Falling back to file size calculation.")
                    if os.path.exists(path):
                        duration_seconds = os.path.getsize(path) / (128000 / 8)
                
                audio_durations.append(duration_seconds)
                total_seconds += duration_seconds

            is_free_trial = False

            calculated_cost = math.ceil(total_seconds / 60)
            if calculated_cost < 1:
                calculated_cost = 1

            # Early Quota Check
            from app.services.subscription_service import get_user_subscription_info
            sub_info = get_user_subscription_info(profile["id"])
            remaining_quota = sub_info.get("quota_remaining", 0)
            
            if remaining_quota < calculated_cost:
                yield f"data: {json.dumps({'type': 'error', 'message': f'🚫 رصيدك الحالي ({remaining_quota} دقيقة) لا يكفي لتلخيص هذه المحاضرات (المطلوب: {calculated_cost} دقيقة). يرجى شحن رصيدك أو ترقية اشتراكك.'}, ensure_ascii=False)}\n\n"
                return

            is_audio_trimmed = False

            # 4. Queueing
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🎟️ تم حجز مكان لك في طابور المعالجة...'}, ensure_ascii=False)}\n\n"
            
            plan_name = "مجاني"
            _has_plan = profile.get("current_plan_id") and profile.get("current_plan_id") != "free"
            if _has_plan:
                from app.services.subscription_service import get_plan_by_id
                plan = get_plan_by_id(profile["current_plan_id"])
                plan_name = plan.get("name", "مجاني") if plan else "مجاني"
            
            representative_path = audio_paths[0] if audio_paths else None
            job = await queue_manager.enqueue(
                user_id=profile["id"],
                plan_name=plan_name,
                payload={
                    "audio_path": representative_path,
                    "pdf_path": pdf_path,
                    "unique_id": unique_id,
                    "is_audio_trimmed": is_audio_trimmed
                }
            )

            # Wait & Stream queue position
            queue_messages = [
                "📋 أنت الآن في الطابور... نجهز لك لمّاح 🚀",
                "⚙️ جاري فحص جودة الملف الصوتي وتهيئته للمعالجة...",
                "🛡️ يتم الآن تأمين مساحة معالجة خاصة لملفك...",
                "⏳ ثوانٍ قليلة وسيبدأ لمّاح بالعمل على محاضراتك...",
                "🌟 هل تعلم؟ المشتركون يحصلون على معالجة فورية بدون انتظار!"
            ]
            msg_idx = 0
            
            while job.status == JobStatus.QUEUED:
                status = queue_manager.get_job_status(job.job_id)
                current_msg = queue_messages[msg_idx % len(queue_messages)]
                pos = status['position'] if (status and 'position' in status) else 1
                yield f"data: {json.dumps({'type': 'queued', 'position': pos, 'message': f'#{pos} | {current_msg}'}, ensure_ascii=False)}\n\n"
                msg_idx += 1
                await asyncio.sleep(4)
            
            if job.status == JobStatus.CANCELLED:
                yield f"data: {json.dumps({'type': 'error', 'message': 'تم إلغاء المهمة.'}, ensure_ascii=False)}\n\n"
                return

            # 5. Transcribe MULTIPLE sources sequentially
            # Calculate dynamic realistic estimates based on actual audio duration & inputs
            file_estimates = []
            for d in audio_durations:
                # ~4.5% of audio duration + 10s per file overhead
                f_est = max(14, min(70, int(d * 0.045) + 10))
                file_estimates.append(f_est)
            if not file_estimates:
                file_estimates = [22] * max(1, len(audio_paths))

            est_audio_total = sum(file_estimates)
            est_slides_sec = 6 if pdf_path else 0
            est_ai_sec = max(25, min(80, 20 + len(audio_paths) * 6 + (20 if pdf_path else 0)))
            est_pdf_sec = 20 if pdf_path else 0
            total_batch_sec = est_audio_total + est_slides_sec + est_ai_sec + est_pdf_sec
            
            yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'progress_percent': 6, 'time_estimate': total_batch_sec, 'message': '🎧 1/5 جاري تفريغ الصوت آلياً... نعدك بدقة عالية وسرعة فائقة!'}, ensure_ascii=False)}\n\n"
            
            transcripts = []
            for idx, path in enumerate(audio_paths):
                f_est = file_estimates[idx] if idx < len(file_estimates) else 22
                rem_files_est = sum(file_estimates[idx+1:]) if (idx + 1 < len(file_estimates)) else 0
                
                import queue
                progress_q = queue.Queue()
                def audio_progress_cb(msg):
                    progress_q.put(msg)
                    
                transcription_task = asyncio.create_task(asyncio.to_thread(transcribe_audio, path, audio_progress_cb))
                
                last_audio_msg = f'جاري تفريغ المحاضرة #{idx + 1} من {len(audio_paths)}...'
                file_start = asyncio.get_event_loop().time()
                while not transcription_task.done():
                    try:
                        while True:
                            last_audio_msg = progress_q.get_nowait()
                    except queue.Empty:
                        pass
                    
                    elapsed_f = asyncio.get_event_loop().time() - file_start
                    cur_file_rem = max(2, int(f_est - elapsed_f))
                    cur_total_rem = rem_files_est + cur_file_rem + est_slides_sec + est_ai_sec + est_pdf_sec
                    
                    completed_files = idx
                    f_progress = min(1.0, elapsed_f / max(1, f_est))
                    overall_audio_ratio = (completed_files + f_progress) / max(1, len(audio_paths))
                    pct = max(6, min(62, int(6 + overall_audio_ratio * 56)))
                    
                    yield f": ping\n\n"
                    yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'progress_percent': pct, 'time_estimate': cur_total_rem, 'message': f'🎧 1/5 المحاضرة #{idx + 1} من {len(audio_paths)}: {last_audio_msg}'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(1.2)
                    
                t = transcription_task.result()
                if t:
                    if idx < len(urls_list):
                        transcripts.append(f"--- [محاضرة رابط {idx+1}] ---\n{t}\n")
                    else:
                        file_idx = idx - len(urls_list) + 1
                        transcripts.append(f"--- [ملف صوتي {file_idx}] ---\n{t}\n")

            if not transcripts:
                yield f"data: {json.dumps({'type': 'error', 'message': 'لم يتم تفريغ أي نص من المحاضرات المرفوعة.'}, ensure_ascii=False)}\n\n"
                return

            transcript = "\n\n".join(transcripts)
            yield f"data: {json.dumps({'type': 'transcript', 'text': transcript}, ensure_ascii=False)}\n\n"
            
            # 6. Document Handling
            pdf_pages = []
            pdf_context = []
            if pdf_path:
                cur_rem = est_ai_sec + est_pdf_sec + 5
                if pdf_path.lower().endswith((".pptx", ".ppt")):
                    yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'progress_percent': 64, 'time_estimate': cur_rem, 'message': '📑 2/5 جاري تحويل ملف البوربوينت إلى PDF لضمان المطابقة 100%...'}, ensure_ascii=False)}\n\n"
                    from app.services.pptx_converter import convert_pptx_to_pdf
                    pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, os.path.dirname(pdf_path))
                yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'progress_percent': 68, 'time_estimate': cur_rem - 3, 'message': '🔍 2/5 جاري قراءة محتوى الشابتر المرفق بدقة هندسية...'}, ensure_ascii=False)}\n\n"
                pdf_pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                pdf_context = [f"--- Page {p['page_number']} ---\n{p['text']}" for p in pdf_pages[:150]]
            
            # 7. AI Annotations
            orchestrator = AIOrchestrator()
            llm = get_llm_for_user(profile["id"])
            anno_task = asyncio.create_task(orchestrator.generate_complete_annotations(llm, transcript, pdf_context))
            
            num_slides = len(pdf_context) if pdf_context else 0
            est_ai_sec = max(25, min(80, 20 + int(num_slides * 0.7)))
            
            AI_TIMEOUT_SECONDS = 900  # 15 minutes for massive batch processing
            ai_start = asyncio.get_event_loop().time()
            while not anno_task.done():
                elapsed = asyncio.get_event_loop().time() - ai_start
                if elapsed >= AI_TIMEOUT_SECONDS:
                    break
                ai_rem = max(3, int(est_ai_sec - elapsed))
                total_ai_rem = ai_rem + est_pdf_sec
                ratio = min(1.0, elapsed / max(1, est_ai_sec))
                pct = min(88, 70 + int(ratio * 18))
                yield f": ping\n\n"
                yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'progress_percent': pct, 'time_estimate': total_ai_rem, 'message': f'🧠 4/5 لمّاح يطابق المفاهيم والأسئلة المتوقعة... ({int(elapsed)}s)'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1.2)
            
            if not anno_task.done():
                anno_task.cancel()
                annotations: dict[str, Any] = {"highlights": [], "sticky_notes": [], "slide_explanations": [], "mcqs": [], "flashcards": []}
            else:
                annotations = await anno_task

            # Generate AI Title
            ai_title = await generate_ai_title(llm, transcript, pdf_context)
            
            # Deduct quota + save history
            history_meta = {
                "title": ai_title,
                "transcript": transcript,
                "slides_text": "\n\n".join(pdf_context) if pdf_context else "",
                "annotations": annotations,
                "job_id": unique_id,
                "has_pdf": bool(pdf_path)
            }
            log_id = use_lecture_quota(profile["id"], metadata=history_meta, cost=calculated_cost)

            # 8. Generate Output PDF with real slide throughput progress
            download_url = None
            if pdf_path and annotations.get("slide_explanations"):
                base_dir_out = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                output_pdf_path = os.path.join(base_dir_out, "outputs", f"{unique_id}.pdf")
                os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)

                import queue
                pdf_progress_q = queue.Queue()
                def on_pdf_progress(cur, total):
                    pdf_progress_q.put((cur, total))

                from app.services.pdf_annotator import annotate_pdf
                annotate_task = asyncio.create_task(asyncio.to_thread(
                    annotate_pdf, pdf_path, output_pdf_path, annotations, False, on_pdf_progress
                ))

                cur_slide = 1
                total_slides = max(1, len(pdf_pages) if pdf_pages else 20)
                pdf_start = asyncio.get_event_loop().time()

                init_pdf_evt = json.dumps({
                    'type': 'progress',
                    'step': '5/5',
                    'progress_percent': 89,
                    'time_estimate': max(6, int(total_slides * 0.7)),
                    'message': f'🎨 5/5 جاري تجهيز ملفك المخطط مع كامل الملاحظات: شريحة 1 من {total_slides}...'
                }, ensure_ascii=False)
                yield f"data: {init_pdf_evt}\n\n"

                while not annotate_task.done():
                    try:
                        while True:
                            cur_slide, total_slides = pdf_progress_q.get_nowait()
                    except queue.Empty:
                        pass
                    
                    elapsed = asyncio.get_event_loop().time() - pdf_start
                    slides_rem = max(0, total_slides - cur_slide)
                    if cur_slide > 1:
                        avg_per_slide = elapsed / cur_slide
                        pdf_rem = max(1, int(slides_rem * avg_per_slide))
                    else:
                        pdf_rem = max(2, int(slides_rem * 0.45))
                    
                    slide_ratio = cur_slide / max(1, total_slides)
                    pdf_pct = min(99, 89 + int(slide_ratio * 10))

                    yield f": ping\n\n"
                    pdf_evt = json.dumps({
                        'type': 'progress',
                        'step': '5/5',
                        'progress_percent': pdf_pct,
                        'time_estimate': pdf_rem,
                        'message': f'🎨 5/5 جاري تجهيز ملفك المخطط مع كامل الملاحظات: شريحة {cur_slide} من {total_slides}...'
                    }, ensure_ascii=False)
                    yield f"data: {pdf_evt}\n\n"
                    await asyncio.sleep(1.2)

                await annotate_task
                download_url = f"/api/v1/summarize/download/{unique_id}.pdf"
                try:
                    meta_path = os.path.join(base_dir_out, "outputs", f"{unique_id}.meta.json")
                    with open(meta_path, "w", encoding="utf-8") as mf:
                        json.dump({
                            "title": ai_title,
                            "has_audio": True,
                            "has_pdf": bool(pdf_path),
                            "is_prep": False,
                            "type": "summarize"
                        }, mf, ensure_ascii=False)
                except Exception as meta_e:
                    logger.warning(f"Failed to write sidecar meta for {unique_id}: {meta_e}")

            # Final 100% progress event before metadata
            yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'progress_percent': 100, 'time_estimate': 0, 'message': '🎉 اكتملت المعالجة بنجاح! جاري تحضير دفترك...'}, ensure_ascii=False)}\n\n"

            # Emit events matching what the frontend stream reader expects (metadata + done)
            _done_history_id = log_id if (log_id and isinstance(log_id, str)) else unique_id
            _meta_msg = json.dumps({'type': 'metadata', 'transcript': transcript, 'annotations': annotations}, ensure_ascii=False)
            yield f"data: {_meta_msg}\n\n"
            _done_msg = json.dumps({'type': 'done', 'download_url': download_url, 'history_id': _done_history_id, 'title': ai_title}, ensure_ascii=False)
            yield f"data: {_done_msg}\n\n"

        except Exception as e:
            logger.error(f"BATCH STREAM ERROR: {e}\n{traceback.format_exc()}")
            _err = json.dumps({'type': 'error', 'message': f'حدث خطأ في معالجة الحزمة: {str(e)}'}, ensure_ascii=False)
            yield f"data: {_err}\n\n"
        finally:
            # Clean up all temporary files
            try:
                for path in audio_paths:
                    if os.path.exists(path):
                        os.remove(path)
                if pdf_path and os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception as cleanup_err:
                logger.error(f"[CLEANUP ERROR] Failed to clean up files in batch: {cleanup_err}")

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-store",
        "Connection": "keep-alive",
        "Alt-Svc": "clear",
        "X-Content-Type-Options": "nosniff",
    })

@router.post("/process-lecture")
async def process_lecture(
    request: Request,
    audio_file: Optional[UploadFile] = File(None),
    pdf_file: Optional[UploadFile] = File(None),
    audio_temp_id: Optional[str] = Form(None),
    pdf_temp_id: Optional[str] = Form(None),
    lecture_url: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    profile=Depends(check_quota)
):
    """
    Lecture processing endpoint.
    Returns a StreamingResponse immediately to provide real-time background updates.
    """
    logger.info(f"🚀 [PROCESS_LECTURE] Request received: audio_temp_id={audio_temp_id}, pdf_temp_id={pdf_temp_id}, url={lecture_url}")
    is_free_trial = False
    
    if lecture_url:
        lower_url = lecture_url.strip().lower()
        if "drive.google.com" in lower_url or "google.com/drive" in lower_url:
            raise HTTPException(status_code=400, detail={
                "user_message": "🚫 عذراً، روابط Google Drive غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥"
            })
        if "zoom.us" in lower_url or "zoom.com" in lower_url:
            raise HTTPException(status_code=400, detail={
                "user_message": "🚫 عذراً، روابط Zoom غير مدعومة لأسباب أمنية. يرجى استخدام روابط يوتيوب فقط! 🎥"
            })

    unique_id = str(uuid.uuid4())[:8]
    # Project Root is 5 levels up
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    upload_dir_env = os.getenv("UPLOAD_DIR", "backend/uploads")
    temp_dir_env = os.getenv("TEMP_DIR", "uploader-go/uploads/temp")
    upload_dir = upload_dir_env if os.path.isabs(upload_dir_env) else os.path.normpath(os.path.join(base_dir, upload_dir_env))
    temp_dir = temp_dir_env if os.path.isabs(temp_dir_env) else os.path.normpath(os.path.join(base_dir, temp_dir_env))
    
    # 1. Sync with Go Uploader (Wait for FFmpeg to finish)
    audio_path = None
    pdf_path = None
    
    if audio_temp_id:
        temp_path = os.path.join(temp_dir, audio_temp_id)
        base, _ = os.path.splitext(audio_temp_id)
        processed_p = os.path.join(temp_dir, f"{base}_processed.mp3")
        
        logger.info(f"[DEBUG] Looking for audio temp file: {audio_temp_id}")
        logger.info(f"[DEBUG] temp_dir resolved to: {temp_dir}")
        
        # Wait up to 15 seconds for the file to appear (shared volume sync delay)
        max_wait_seconds = 15
        waited = 0
        while not os.path.exists(processed_p) and not os.path.exists(temp_path) and waited < max_wait_seconds:
            await asyncio.sleep(1)
            waited += 1
            
        if os.path.exists(processed_p):
            logger.info(f"[DEBUG] Found Go-processed audio: {processed_p}")
            audio_path = processed_p
        elif os.path.exists(temp_path):
            logger.info(f"[DEBUG] Found original audio temp file: {temp_path}")
            audio_path = temp_path
        else:
            logger.error(f"[ERROR] Temp audio file NOT found after {max_wait_seconds}s. Checked: {temp_path} and {processed_p}")
            raise HTTPException(status_code=400, detail="temp_file_missing")
            
    elif audio_file:
        os.makedirs(upload_dir, exist_ok=True)
        audio_path = os.path.join(upload_dir, f"{unique_id}_{audio_file.filename}")
        with open(audio_path, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)
    elif lecture_url:
        pass
    elif not (pdf_temp_id or pdf_file):
        # No audio, no URL, and no PDF → reject
        raise HTTPException(status_code=400, detail="Missing audio source or document file")

    # Handle PDF — wait up to 10 seconds for it to appear from Go Uploader shared volume
    if pdf_temp_id:
        pdf_path = os.path.join(temp_dir, pdf_temp_id)
        logger.info(f"[DEBUG] Looking for PDF temp file: {pdf_path}")
        max_pdf_wait = 10
        pdf_waited = 0
        while not os.path.exists(pdf_path) and pdf_waited < max_pdf_wait:
            await asyncio.sleep(1)
            pdf_waited += 1
        if os.path.exists(pdf_path):
            logger.info(f"[DEBUG] Found PDF temp file: {pdf_path} (size: {os.path.getsize(pdf_path)} bytes)")
        else:
            if not (audio_path or audio_file or lecture_url):
                logger.error(f"[ERROR] Temp PDF file NOT found for document-only mode. Checked: {pdf_path}")
                raise HTTPException(status_code=400, detail="temp_file_missing")
            logger.warning(f"[WARN] PDF temp file not found after {max_pdf_wait}s: {pdf_path} — continuing without slides.")
            pdf_path = None
    elif pdf_file:
        os.makedirs(upload_dir, exist_ok=True)
        pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_file.filename}")
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(pdf_file.file, buffer)

    async def event_stream():
        nonlocal audio_path, pdf_path
        actual_pdf_path: Optional[str] = None
        base_dir_out = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        try:
            # 0. Flush initial headers & Padding
            padding = ":" + " " * 1024 + "\n\n"
            yield padding
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🚀 بدأنا العمل! جاري تحضير المحرك...'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.1)

            # Detect if this is a document-only mode (no audio, no URL)
            is_document_only = (audio_path is None) and (not lecture_url)

            # Block URL processing for free trial users — send upgrade message instead
            # 1. Finalize Audio Path (Download if URL)
            if lecture_url and not audio_path:
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🔗 جاري تحميل المحاضرة من الرابط...'}, ensure_ascii=False)}\n\n"
                audio_path = await asyncio.to_thread(download_audio_from_link, lecture_url.strip(), upload_dir, unique_id)
                is_document_only = False
            elif not is_document_only:
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📥 تم استلام وتجهيز الملفات بنجاح.'}, ensure_ascii=False)}\n\n"
            
            if pdf_path:
                if is_document_only:
                    yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📄 تم استلام الملف! جاري التحليل الذكي للوثيقة...'}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📑 تم تحليل ملفات العرض المرفقة.'}, ensure_ascii=False)}\n\n"
            elif is_document_only:
                yield f"data: {json.dumps({'type': 'error', 'message': 'يرجى إرفاق ملف PDF أو PPTX أو ملف صوتي للمعالجة.'}, ensure_ascii=False)}\n\n"
                return

            # 2. Quota Check (dynamic by actual duration or page count)
            is_audio_trimmed = False
            duration_seconds = 0
            import math

            # Convert PPTX to PDF immediately as the single source of truth for quota, AI, and annotation
            if pdf_path and (pdf_path.lower().endswith(".pptx") or pdf_path.lower().endswith(".ppt")):
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📑 جاري تحويل ملف البوربوينت إلى PDF لضمان مطابقة الشرائح 100%...'}, ensure_ascii=False)}\n\n"
                from app.services.pptx_converter import convert_pptx_to_pdf
                temp_pdf_dir = os.path.dirname(pdf_path)
                pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, temp_pdf_dir)
                logger.info(f"[PROCESS_LECTURE] PPTX converted immediately to PDF: {pdf_path}")

            if is_document_only:
                # Calculate cost based on PDF page count
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📏 جاري حساب حجم الوثيقة وتكلفة المعالجة...'}, ensure_ascii=False)}\n\n"
                try:
                    if pdf_path:
                        pages_cost = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                        num_pages = len(pages_cost)
                    else:
                        num_pages = 10

                    # 1 minute per 2 slides/pages, minimum 3 minutes
                    calculated_cost = max(3, math.ceil(num_pages / 2))
                except Exception:
                    calculated_cost = 5
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📏 جاري فحص مدة المحاضرة وحساب الحصة...'}, ensure_ascii=False)}\n\n"
                try:
                    import subprocess
                    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
                    raw_out = await asyncio.to_thread(subprocess.check_output, cmd)
                    duration_str = raw_out.decode().strip() if isinstance(raw_out, bytes) else str(raw_out).strip()
                    duration_seconds = float(duration_str)
                except Exception:
                    duration_seconds = (os.path.getsize(audio_path) / (128000 / 8)) if (audio_path and os.path.exists(audio_path)) else 60.0

                calculated_cost = math.ceil(duration_seconds / 60)
                if calculated_cost < 1:
                    calculated_cost = 1

            # Early Quota Check
            from app.services.subscription_service import get_user_subscription_info
            sub_info = get_user_subscription_info(profile["id"])
            remaining_quota = sub_info.get("quota_remaining", 0)
            
            if remaining_quota < calculated_cost:
                yield f"data: {json.dumps({'type': 'error', 'message': f'🚫 رصيدك الحالي ({remaining_quota} دقيقة) لا يكفي لتلخيص هذه المحاضرة (المطلوب: {calculated_cost} دقيقة). يرجى شحن رصيدك أو ترقية اشتراكك.'}, ensure_ascii=False)}\n\n"
                return

            # 3. Queue Job
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🎟️ تم حجز مكان لك في طابور المعالجة...'}, ensure_ascii=False)}\n\n"
            
            # Get the plan name to determine priority in queue
            plan_name = "مشترك"
            if profile.get("current_plan_id"):
                from app.services.subscription_service import get_plan_by_id
                plan = get_plan_by_id(profile["current_plan_id"])
                plan_name = plan.get("name", "مشترك") if plan else "مشترك"
            
            job = await queue_manager.enqueue(
                user_id=profile["id"],
                plan_name=plan_name,
                payload={
                    "audio_path": audio_path,
                    "pdf_path": pdf_path,
                    "unique_id": unique_id,
                    "is_audio_trimmed": is_audio_trimmed
                }
            )

            # 4. Wait & Stream with dynamic queue messages
            queue_messages = [
                "📋 أنت الآن في الطابور... نجهز لك لمّاح 🚀",
                "⚙️ جاري فحص جودة الملف الصوتي وتهيئته للمعالجة...",
                "🛡️ يتم الآن تأمين مساحة معالجة خاصة لملفك...",
                "⏳ ثوانٍ قليلة وسيبدأ لمّاح بالعمل على محاضرتك...",
                "🌟 هل تعلم؟ المشتركون يحصلون على معالجة فورية بدون انتظار!"
            ]
            msg_idx = 0
            
            while job.status == JobStatus.QUEUED:
                status = queue_manager.get_job_status(job.job_id)
                current_msg = queue_messages[msg_idx % len(queue_messages)]
                pos = status['position'] if (status and 'position' in status) else 1
                yield f"data: {json.dumps({'type': 'queued', 'position': pos, 'message': f'#{pos} | {current_msg}'}, ensure_ascii=False)}\n\n"
                msg_idx += 1
                await asyncio.sleep(4)
            
            if job.status == JobStatus.CANCELLED:
                yield f"data: {json.dumps({'type': 'error', 'message': 'تم إلغاء المهمة.'}, ensure_ascii=False)}\n\n"
                return

            # 1. Transcribe (skip in document-only mode)
            est_audio_sec = max(16, min(65, int(duration_seconds * 0.045) + 10)) if not is_document_only else 0
            est_slides_sec = 4 if pdf_path else 0
            est_ai_sec = 25 if not is_document_only else 18
            est_pdf_sec = 18 if pdf_path else 0
            total_single_sec = est_audio_sec + est_slides_sec + est_ai_sec + est_pdf_sec

            if is_document_only:
                transcript = ""
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'progress_percent': 15, 'time_estimate': total_single_sec, 'message': '📄 1/5 وضع الوثيقة فقط: سيتم توليد الملخص مباشرة من الملف...'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'progress_percent': 8, 'time_estimate': total_single_sec, 'message': '🎧 1/5 جاري تفريغ الصوت آلياً... نعدك بدقة عالية وسرعة فائقة!'}, ensure_ascii=False)}\n\n"
                if not audio_path:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'لم يتم العثور على مسار الملف الصوتي للتفريغ.'}, ensure_ascii=False)}\n\n"
                    return
                
                import queue
                progress_q = queue.Queue()
                def audio_progress_cb(msg):
                    progress_q.put(msg)
                    
                transcription_task = asyncio.create_task(asyncio.to_thread(transcribe_audio, audio_path, audio_progress_cb))
                
                last_audio_msg = 'لمّاح يستمع بتركيز... جاري تحويل الكلام لنص بدقة عالية!'
                audio_start = asyncio.get_event_loop().time()
                while not transcription_task.done():
                    try:
                        while True:
                            last_audio_msg = progress_q.get_nowait()
                    except queue.Empty:
                        pass
                    
                    audio_elapsed = asyncio.get_event_loop().time() - audio_start
                    rem_audio = max(2, int(est_audio_sec - audio_elapsed))
                    cur_rem = rem_audio + est_slides_sec + est_ai_sec + est_pdf_sec
                    pct = max(10, min(50, int((audio_elapsed / max(1, est_audio_sec)) * 40) + 10))
                    
                    # Ping every 1.2 seconds
                    yield f": ping\n\n"
                    yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'progress_percent': pct, 'time_estimate': cur_rem, 'message': f'🎧 1/5 لمّاح يستمع... {last_audio_msg}'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(1.2)
                    
                transcript = transcription_task.result()
                yield f"data: {json.dumps({'type': 'transcript', 'text': transcript}, ensure_ascii=False)}\n\n"
            
            # 2. Document Handling
            pdf_pages = []
            pdf_context = []
            if pdf_path:
                max_pages = 150
                doc_rem = est_ai_sec + est_pdf_sec + 3
                yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'progress_percent': 54, 'time_estimate': doc_rem, 'message': '🔍 2/5 جاري قراءة محتوى الشرائح بدقة هندسية...'}, ensure_ascii=False)}\n\n"
                pdf_pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                logger.info(f"[PROCESS_LECTURE] PDF extracted {len(pdf_pages)} pages from {pdf_path}")
                pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pdf_pages[:max_pages]]

                if not pdf_context:
                    logger.warning(f"[PROCESS_LECTURE] pdf_context is EMPTY after extraction — file may be image-based or corrupted: {pdf_path}")

            
            # 3. AI Annotations
            orchestrator = AIOrchestrator()
            llm = get_llm_for_user(profile["id"])
            anno_task = asyncio.create_task(orchestrator.generate_complete_annotations(llm, transcript, pdf_context))
            
            num_slides = len(pdf_context) if pdf_context else 0
            est_ai_sec = max(22, min(75, 16 + int(num_slides * 0.6)))
            
            AI_TIMEOUT_SECONDS = 600  # 10 minutes deadline for large lectures
            ai_start = asyncio.get_event_loop().time()
            while not anno_task.done():
                elapsed = asyncio.get_event_loop().time() - ai_start
                if elapsed >= AI_TIMEOUT_SECONDS:
                    break
                ai_rem = max(3, int(est_ai_sec - elapsed))
                total_ai_rem = ai_rem + est_pdf_sec
                ratio = min(1.0, elapsed / max(1, est_ai_sec))
                pct = min(88, 58 + int(ratio * 30))
                yield f": ping\n\n"
                yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'progress_percent': pct, 'time_estimate': total_ai_rem, 'message': f'🧠 4/5 لمّاح يستنتج الأفكار والأسئلة المتوقعة بسرعة فائقة... ({int(elapsed)}s)'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1.2)
            
            if not anno_task.done():
                anno_task.cancel()
                logger.warning("[ORCHESTRATOR] AI timeout after 10 minutes — returning empty annotations.")
                annotations: dict[str, Any] = {"highlights": [], "sticky_notes": [], "slide_explanations": [], "mcqs": [], "flashcards": []}
            else:
                annotations = await anno_task

            # 4. Final PDF with live slide-by-slide progress
            download_url = None
            if pdf_path:
                output_pdf_path = os.path.join(base_dir_out, "outputs", f"{job.job_id}.pdf")
                os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
                
                import queue
                pdf_progress_q = queue.Queue()
                def on_pdf_progress(cur, total):
                    pdf_progress_q.put((cur, total))

                from app.services.pdf_annotator import annotate_pdf
                annotate_task = asyncio.create_task(asyncio.to_thread(
                    annotate_pdf, pdf_path, output_pdf_path, annotations, False, on_pdf_progress
                ))

                cur_slide = 1
                total_slides = max(1, len(pdf_pages) if pdf_pages else 20)
                pdf_start = asyncio.get_event_loop().time()

                init_pdf_evt = json.dumps({
                    'type': 'progress',
                    'step': '5/5',
                    'progress_percent': 89,
                    'time_estimate': max(6, int(total_slides * 0.7)),
                    'message': f'🎨 5/5 جاري تجهيز ملفك المخطط مع كامل الملاحظات: شريحة 1 من {total_slides}...'
                }, ensure_ascii=False)
                yield f"data: {init_pdf_evt}\n\n"

                while not annotate_task.done():
                    try:
                        while True:
                            cur_slide, total_slides = pdf_progress_q.get_nowait()
                    except queue.Empty:
                        pass

                    elapsed = asyncio.get_event_loop().time() - pdf_start
                    slides_rem = max(0, total_slides - cur_slide)
                    if cur_slide > 1:
                        avg_per_slide = elapsed / cur_slide
                        pdf_rem = max(1, int(slides_rem * avg_per_slide))
                    else:
                        pdf_rem = max(2, int(slides_rem * 0.45))

                    slide_ratio = cur_slide / max(1, total_slides)
                    pdf_pct = min(99, 89 + int(slide_ratio * 10))

                    yield f": ping\n\n"
                    pdf_evt = json.dumps({
                        'type': 'progress',
                        'step': '5/5',
                        'progress_percent': pdf_pct,
                        'time_estimate': pdf_rem,
                        'message': f'🎨 5/5 جاري تجهيز ملفك المخطط مع كامل الملاحظات: شريحة {cur_slide} من {total_slides}...'
                    }, ensure_ascii=False)
                    yield f"data: {pdf_evt}\n\n"
                    await asyncio.sleep(1.2)

                await annotate_task
                download_url = f"/api/v1/summarize/download/{job.job_id}.pdf"

            # Final 100% progress event before metadata
            yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'progress_percent': 100, 'time_estimate': 0, 'message': '🎉 اكتملت المعالجة بنجاح! جاري تحضير دفترك...'}, ensure_ascii=False)}\n\n"
 
            # 5. Save History & Finalize
            # Generate AI Title
            ai_title = await generate_ai_title(llm, transcript, pdf_context)
            
            history_meta = {
                "title": ai_title,
                "transcript": transcript,
                "slides_text": "\n\n".join(pdf_context) if pdf_context else "",
                "annotations": annotations,
                "job_id": job.job_id,
                "has_pdf": bool(pdf_path),
                "is_prep": is_document_only,
                "has_audio": not is_document_only
            }
            log_id = use_lecture_quota(profile["id"], metadata=history_meta, cost=calculated_cost)

            # Save sidecar metadata for downloads
            if pdf_path:
                try:
                    meta_path = os.path.join(base_dir_out, "outputs", f"{job.job_id}.meta.json")
                    with open(meta_path, "w", encoding="utf-8") as mf:
                        json.dump({
                            "title": ai_title,
                            "has_audio": not is_document_only,
                            "has_pdf": bool(pdf_path),
                            "is_prep": is_document_only,
                            "type": "summarize"
                        }, mf, ensure_ascii=False)
                except Exception as meta_e:
                    logger.warning(f"Failed to write sidecar meta for {job.job_id}: {meta_e}")
            
            _done_history_id = log_id if (log_id and isinstance(log_id, str)) else job.job_id
            yield f"data: {json.dumps({'type': 'metadata', 'annotations': annotations}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'download_url': download_url, 'history_id': _done_history_id, 'title': ai_title, 'annotations': annotations}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"STREAM ERROR: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'❌ فشل: {str(e)}'}, ensure_ascii=False)}\n\n"
        finally:
            # Cleanup
            try:
                if audio_path and os.path.exists(audio_path): os.remove(audio_path)
                if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
                if 'actual_pdf_path' in locals() and actual_pdf_path and actual_pdf_path != pdf_path and os.path.exists(actual_pdf_path):
                    os.remove(actual_pdf_path)
            except: pass

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-store",
        "Connection": "keep-alive",
        "Alt-Svc": "clear",
        "X-Content-Type-Options": "nosniff",
    })

@router.get("/download/{job_id}.pdf")
async def download_annotated_pdf(
    job_id: str,
    title: Optional[str] = Query(None),
    mode: Optional[str] = Query(None),
    is_prep: Optional[bool] = Query(None),
    download: bool = Query(False)
):
    """Secure PDF download checking both backend and project root outputs.
    Includes Path Traversal protection and accurate Arabic filename metadata:
    - [اسم المادة] - تحضير سلايدات - لمّاح.pdf (عند رفع الملف فقط بدون فويسات)
    - [اسم المادة] - مخططة فويسات - لمّاح.pdf (عند دمج الفويسات مع الملف)
    """
    if not re.match(r'^[a-zA-Z0-9_\-]+$', job_id) or os.path.basename(job_id) != job_id:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    routes_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(routes_dir)))
    base_dir = os.path.dirname(backend_dir)
    
    path_1 = os.path.join(backend_dir, "outputs", f"{job_id}.pdf")
    path_2 = os.path.join(base_dir, "outputs", f"{job_id}.pdf")
    
    resolved_path = None
    if os.path.exists(path_1):
        resolved_path = path_1
    elif os.path.exists(path_2):
        resolved_path = path_2
        
    if not resolved_path:
        logger.error(f"[DOWNLOAD] File not found for job_id={job_id}. Checked paths: {path_1} and {path_2}")
        raise HTTPException(status_code=404, detail="File not found")

    # Determine title and prep status
    resolved_title = title
    resolved_is_prep = is_prep
    if resolved_is_prep is None and mode is not None:
        resolved_is_prep = mode.lower() in ["prep", "slides", "document", "document_only"]

    # 1. Check sidecar metadata file (.meta.json)
    meta_path_1 = os.path.join(backend_dir, "outputs", f"{job_id}.meta.json")
    meta_path_2 = os.path.join(base_dir, "outputs", f"{job_id}.meta.json")
    for mp in [meta_path_1, meta_path_2]:
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
                logger.warning(f"Error reading sidecar {mp}: {meta_err}")

    # 2. Check Supabase usage logs if title or mode still unresolved
    if not resolved_title or resolved_is_prep is None:
        try:
            from app.services.subscription_service import get_supabase
            supabase = get_supabase()
            res = supabase.table("usage_logs").select("metadata").eq("metadata->>job_id", job_id).execute()
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
            logger.warning(f"Supabase lookup failed for {job_id}: {sb_err}")

    from app.services.filename_helper import format_arabic_download_filename
    arabic_filename = format_arabic_download_filename(
        title=resolved_title,
        is_notebook=False,
        is_prep=bool(resolved_is_prep)
    )

    disposition = "attachment" if download else "inline"
    return FileResponse(
        resolved_path,
        media_type="application/pdf",
        filename=arabic_filename,
        content_disposition_type=disposition
    )


@router.post("/youtube-to-mp3")
async def youtube_to_mp3(
    url: str = Form(...),
    bg_tasks: BackgroundTasks = BackgroundTasks(),
    profile=Depends(get_user_profile)
):
    """
    Independent endpoint to download YouTube audio as an MP3 file directly.
    """
    logger.info(f"📥 [YOUTUBE_TO_MP3] Download requested for URL: {url} by user {profile['id']}")
    
    unique_id = str(uuid.uuid4())[:8]
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    upload_dir_env = os.getenv("UPLOAD_DIR", "backend/uploads")
    upload_dir = upload_dir_env if os.path.isabs(upload_dir_env) else os.path.normpath(os.path.join(base_dir, upload_dir_env))
    
    # Validation
    lower_url = url.lower()
    if not ("youtube.com" in lower_url or "youtu.be" in lower_url):
        raise HTTPException(
            status_code=400,
            detail={"user_message": "🚫 يرجى إدخال رابط يوتيوب (YouTube) صالح فقط!"}
        )

    try:
        from app.services.youtube_downloader_service import download_youtube_audio
        
        # Download YouTube audio
        mp3_path = await asyncio.to_thread(download_youtube_audio, url.strip(), upload_dir, unique_id)
        
        if not mp3_path or not os.path.exists(mp3_path):
            raise Exception("فشل تنزيل ملف الصوت من الرابط.")
            
        filename = os.path.basename(mp3_path)
        
        def remove_file(path: str):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"🗑️ [YOUTUBE_TO_MP3] Temp file deleted: {path}")
            except Exception as e:
                logger.error(f"Error removing temp file: {e}")
                
        bg_tasks.add_task(remove_file, mp3_path)
        
        from fastapi.responses import FileResponse
        return FileResponse(
            path=mp3_path,
            filename=filename,
            media_type="audio/mpeg"
        )
        
    except Exception as e:
        logger.error(f"❌ [YOUTUBE_TO_MP3] Failed: {e}")
        error_msg = str(e)
        if "فشل جلب المحاضرة" in error_msg or "يوتيوب يمنع" in error_msg:
            raise HTTPException(status_code=400, detail={"user_message": error_msg})
        raise HTTPException(
            status_code=500,
            detail={"user_message": f"فشل تحويل وتنزيل المقطع الصوتي: {error_msg}"}
        )


@router.post("/upload-temp")
async def upload_temp(
    file: UploadFile = File(...),
    user=Depends(verify_token)
):
    """
    Temporary file upload endpoint (Python fallback for Go Uploader).
    Saves file to temp directory and returns a temp_id for later processing.
    """
    logger.info(f"📥 [UPLOAD_TEMP] Uploading: {file.filename}")
    # Project Root is 5 levels up
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    temp_dir_env = os.getenv("TEMP_DIR", "uploader-go/uploads/temp")
    temp_dir = temp_dir_env if os.path.isabs(temp_dir_env) else os.path.normpath(os.path.join(base_dir, temp_dir_env))
    os.makedirs(temp_dir, exist_ok=True)
    
    # Validate extension
    allowed_ext = {".mp3", ".wav", ".m4a", ".pdf", ".ogg", ".flac", ".pptx"}
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="File extension not supported")
    
    # Generate unique filename
    saved_filename = f"{uuid.uuid4()}{ext}"
    final_path = os.path.join(temp_dir, saved_filename)
    
    # Save file
    try:
        with open(final_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        logger.error(f"Error saving temp file: {e}")
        raise HTTPException(status_code=500, detail="Error saving file")
    
    logger.info(f"✅ Temp file saved: {saved_filename} ({os.path.getsize(final_path) / (1024*1024):.1f} MB)")
    
    return {
        "temp_id": saved_filename,
        "filename": file.filename,
        "status": "Processing"
    }


@router.get("/debug-env", dependencies=[Depends(verify_admin)])
async def debug_env():
    """
    Temporary diagnostic endpoint — shows TEMP_DIR, UPLOAD_DIR, and their contents.
    Helps verify file paths and shared volume access on production.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    temp_dir_env = os.getenv("TEMP_DIR", "uploader-go/uploads/temp")
    upload_dir_env = os.getenv("UPLOAD_DIR", "backend/uploads")
    temp_dir = temp_dir_env if os.path.isabs(temp_dir_env) else os.path.normpath(os.path.join(base_dir, temp_dir_env))
    upload_dir = upload_dir_env if os.path.isabs(upload_dir_env) else os.path.normpath(os.path.join(base_dir, upload_dir_env))

    def list_dir_safe(path):
        if not os.path.exists(path):
            return {"exists": False, "path": path}
        try:
            files = os.listdir(path)
            files_info = []
            for f in files[:20]:
                fp = os.path.join(path, f)
                files_info.append({
                    "name": f,
                    "size_kb": round(os.path.getsize(fp) / 1024, 1) if os.path.isfile(fp) else None,
                    "modified_ago_s": round(os.path.getmtime(fp))
                })
            return {"exists": True, "path": path, "count": len(files), "files": files_info}
        except Exception as e:
            return {"exists": True, "path": path, "error": str(e)}

    # Font path resolution and diagnostics
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # backend_dir is app/ (i.e. /app/app inside Docker)
    amiri_reg_path = os.path.join(backend_dir, "static", "fonts", "Amiri-Regular.ttf")
    amiri_bold_path = os.path.join(backend_dir, "static", "fonts", "Amiri-Bold.ttf")
    
    reg_exists = os.path.exists(amiri_reg_path)
    bold_exists = os.path.exists(amiri_bold_path)
    
    font_test_status = "Not tested"
    if reg_exists:
        try:
            import fitz  # type: ignore
            doc = fitz.open()
            page = doc.new_page()
            page.insert_font(fontname="arfont", fontfile=amiri_reg_path)
            font_test_status = "Success"
        except Exception as e:
            font_test_status = f"Failed: {str(e)}"

    return {
        "python_file": __file__,
        "base_dir": base_dir,
        "env": {
            "TEMP_DIR": os.getenv("TEMP_DIR", "NOT SET"),
            "UPLOAD_DIR": os.getenv("UPLOAD_DIR", "NOT SET"),
        },
        "temp_dir": list_dir_safe(temp_dir),
        "upload_dir": list_dir_safe(upload_dir),
        "pptx_available": __import__('importlib').util.find_spec('pptx') is not None,
        "pymupdf_available": __import__('importlib').util.find_spec('fitz') is not None,
        "font_info": {
            "reg_path": amiri_reg_path,
            "reg_exists": reg_exists,
            "bold_path": amiri_bold_path,
            "bold_exists": bold_exists,
            "test_status": font_test_status
        }
    }


@router.post("/convert-pptx-stream")
async def convert_pptx_stream(file: UploadFile = File(...)):
    """
    In-memory, ephemeral PowerPoint to PDF converter for Focus Study.
    Zero server disk storage: consumes PPTX bytes from stream, synthesizes
    PDF in RAM, and immediately streams back PDF binary.
    """
    import io
    from app.services.pptx_converter import convert_pptx_bytes_to_pdf_bytes

    filename = file.filename or "presentation.pptx"
    if not (filename.lower().endswith(".pptx") or filename.lower().endswith(".ppt")):
        raise HTTPException(
            status_code=400,
            detail={"user_message": "يرجى رفع ملف عرض تقديمي بصيغة PowerPoint (.pptx)"}
        )

    try:
        pptx_bytes = await file.read()
        if len(pptx_bytes) == 0:
            raise HTTPException(
                status_code=400,
                detail={"user_message": "الملف المرفوع فارغ."}
            )

        pdf_bytes = await asyncio.to_thread(convert_pptx_bytes_to_pdf_bytes, pptx_bytes)
        
        base_name = os.path.splitext(filename)[0]
        safe_filename = f"{base_name}.pdf"

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_filename}"',
                "Content-Length": str(len(pdf_bytes)),
                "Access-Control-Expose-Headers": "Content-Disposition, Content-Length"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PPTX Stream Conversion Error]: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"user_message": f"تعذر تحويل ملف PowerPoint: {str(e)}"}
        )

