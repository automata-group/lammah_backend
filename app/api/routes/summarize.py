from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Request, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
import shutil
import os
import uuid
import traceback
import json
import asyncio
import subprocess
import re
from pydantic import BaseModel
from typing import List, Optional
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.ai.orchestrator import AIOrchestrator
from app.services.subscription_service import use_lecture_quota, log_usage_failure, get_supabase, refund_lecture_quota
from app.services.link_ingest_service import download_audio_from_link
from app.core.auth_guard import check_quota, verify_phone_active, get_user_profile, verify_token
from app.core.queue_manager import queue_manager, JobStatus
from app.core.logger import logger
from app.services.ai.factory import get_llm_for_user

router = APIRouter()

async def generate_ai_title(llm, transcript: str) -> str:
    try:
        sample_text = transcript[:4000] if transcript else ""
        if not sample_text:
            return f"محاضرة {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        title_prompt = (
            "أنت خبير تلخيص وعنونة محاضرات. بناءً على هذا النص المفرغ من المحاضرة، "
            "استنتج عنواناً باللغة العربية يكون موجزاً ومحترفاً جداً ولا يتجاوز 3 إلى 5 كلمات للمحاضرة. "
            "لا تضع أي مقدمات، جمل تمهيدية، علامات تنصيص، أو نقطة في النهاية. "
            "أعطني العنوان مباشرة فقط.\n\n"
            f"نص المحاضرة:\n{sample_text}"
        )
        title_response = await asyncio.to_thread(llm.invoke, title_prompt)
        ai_title = title_response.content.strip().replace('"', '').replace('«', '').replace('»', '')
        ai_title = re.sub(r'^(العنوان:|العنوان المقترح:|عنوان المحاضرة:|العنوان هو:)\s*', '', ai_title, flags=re.IGNORECASE).strip()
        if len(ai_title) > 1:
            return ai_title
    except Exception as e:
        logger.error(f"Error generating AI title: {e}")
    from datetime import datetime
    return f"محاضرة {datetime.now().strftime('%Y-%m-%d %H:%M')}"

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
    def update_progress(step: str, message: str, is_error: bool = False, is_done: bool = False, result: dict = None):
        try:
            # Re-fetch current metadata to avoid overwriting other fields
            res = supabase.table("usage_logs").select("metadata").eq("id", history_id).execute()
            meta = res.data[0].get("metadata", {}) if res.data else {}
            
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

        if not audio_path: raise Exception("No audio source provided.")

        # No trimming — all users get full audio processing
        is_audio_trimmed = False

        # 4. Queue and Process
        update_progress("2/5", "🎟️ في طابور المعالجة...")
        job = await queue_manager.enqueue(
            user_id=user_id,
            plan_name=plan_name,
            payload={"audio_path": audio_path, "pdf_path": pdf_path, "unique_id": unique_id, "is_audio_trimmed": False}
        )

        while job.status == JobStatus.QUEUED:
            await asyncio.sleep(4)
        
        # 5. Transcription
        update_progress("3/5", "🎧 جاري تحويل الكلام إلى نص بدقة عالية...")
        transcript = await asyncio.to_thread(transcribe_audio, audio_path)
        
        # 6. PDF and AI
        pdf_context = []
        if pdf_path:
            if pdf_path.lower().endswith(".pptx"):
                update_progress("3/5", "📑 جاري استخراج محتوى شرائح البوربوينت...")
                from app.services.pptx_converter import extract_text_from_pptx
                pptx_pages = await asyncio.to_thread(extract_text_from_pptx, pdf_path)
                pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pptx_pages[:150]]
            else:
                update_progress("3/5", "🔍 جاري تحليل محتوى الشرائح...")
                pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                pdf_context = [p['text'] for p in pages[:150]]


        update_progress("4/5", "🧠 لمّاح يفكّر الآن... جاري استنتاج أهم النقاط...")
        orchestrator = AIOrchestrator()
        llm = get_llm_for_user(user_id)
        annotations = await orchestrator.generate_complete_annotations(llm, transcript, pdf_context)

        # Trial Limits
        if is_free_trial:
            if isinstance(annotations.get("slide_explanations"), list):
                annotations["slide_explanations"] = annotations["slide_explanations"][:2]
                annotations["slides_locked"] = True
            if isinstance(annotations.get("expected_questions"), list):
                annotations["expected_questions"] = annotations["expected_questions"][:2]
            if isinstance(annotations.get("flashcards"), list):
                annotations["flashcards"] = annotations["flashcards"][:2]

        # 7. Annotation and Finalize
        download_url = None
        if pdf_path:
            update_progress("5/5", "🎨 اللمسات الأخيرة! جاري تلوين الشرائح...")
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            output_pdf_path = os.path.join(base_dir, "outputs", f"{job.job_id}.pdf")
            os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
            
            annotatable_pdf_path = pdf_path
            if pdf_path.lower().endswith(".pptx"):
                try:
                    from app.services.pptx_converter import convert_pptx_to_pdf
                    annotatable_pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, os.path.dirname(pdf_path))
                except Exception as pptx_err:
                    logger.error(f"Failed to convert PPTX to PDF for annotation: {pptx_err}")
                    raise Exception("فشل تحويل ملف البوربوينت إلى PDF للرسم والتلوين. يرجى رفع ملف PDF مباشرة.")
            
            from app.services.pdf_annotator import annotate_pdf
            await asyncio.to_thread(annotate_pdf, annotatable_pdf_path, output_pdf_path, annotations, is_free_trial)
            download_url = f"/api/v1/summarize/download/{job.job_id}.pdf"


        # Generate AI Title
        ai_title = await generate_ai_title(llm, transcript)

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
    is_free_trial = profile.get("current_plan_id") is None or profile.get("current_plan_id") == "free"
    if sub_info.get("active_subscription"):
        plan_name = sub_info["active_subscription"].get("subscription_plans", {}).get("name", "مجاني")

    results = []
    supabase = get_supabase()

    for row in req.lectures:
        initial_meta = {"progress_step": "0/5", "message": "بانتظار البدء...", "row_id": row.row_id}
        history_id = use_lecture_quota(user_id, metadata=initial_meta)
        
        if not history_id:
            continue
            
        supabase.table("usage_logs").update({"status": "processing"}).eq("id", history_id).execute()
        
        bg_tasks.add_task(background_worker, row, user_id, plan_name, history_id, is_free_trial)
        results.append({"row_id": row.row_id, "history_id": history_id})
    
    return {"status": "success", "jobs": results}

@router.get("/status/{history_id}")
async def get_job_status(history_id: str):
    """Pollable endpoint for background job status."""
    supabase = get_supabase()
    res = supabase.table("usage_logs").select("*").eq("id", history_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Job not found")
        
    log = res.data[0]
    meta = log.get("metadata", {})
    status = log["status"]
    
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
    is_free_trial = profile.get("current_plan_id") is None or profile.get("current_plan_id") == "free"
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
        raise HTTPException(status_code=400, detail="Missing audio sources. Provide at least one URL or file.")
        
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

            # No trimming — all users get full audio processing
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
                pos = status['position']
                yield f"data: {json.dumps({'type': 'queued', 'position': pos, 'message': f'#{pos} | {current_msg}'}, ensure_ascii=False)}\n\n"
                msg_idx += 1
                await asyncio.sleep(4)
            
            if job.status == JobStatus.CANCELLED:
                yield f"data: {json.dumps({'type': 'error', 'message': 'تم إلغاء المهمة.'}, ensure_ascii=False)}\n\n"
                return

            # 5. Transcribe MULTIPLE sources sequentially
            yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'time_estimate': 180 * len(audio_paths), 'message': '🎧 1/5 جاري تفريغ الصوت آلياً... نعدك بدقة عالية!'}, ensure_ascii=False)}\n\n"
            
            transcripts = []
            for idx, path in enumerate(audio_paths):
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': f'🎧 1/5 جاري تفريغ ملف المحاضرة #{idx + 1} من {len(audio_paths)}...'}, ensure_ascii=False)}\n\n"
                
                import queue
                progress_q = queue.Queue()
                def audio_progress_cb(msg):
                    progress_q.put(msg)
                    
                transcription_task = asyncio.create_task(asyncio.to_thread(transcribe_audio, path, audio_progress_cb))
                
                last_audio_msg = f'جاري تفريغ الجزء #{idx + 1}...'
                while not transcription_task.done():
                    try:
                        while True:
                            last_audio_msg = progress_q.get_nowait()
                            yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': f'🎧 1/5 الجزء #{idx + 1}: {last_audio_msg}'}, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        pass
                    
                    yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': f'🎧 1/5 جاري التفريغ... {last_audio_msg}'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(3)
                    
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
            pdf_context = []
            if pdf_path:
                yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '🔍 2/5 جاري قراءة محتوى الشابتر المرفق بدقة هندسية...'}, ensure_ascii=False)}\n\n"
                pdf_pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                pdf_context = [f"--- Page {p['page_number']} ---\n{p['text']}" for p in pdf_pages[:150]]
            
            # 7. AI Annotations
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': '🧠 4/5 لمّاح يطابق كلام المحاضرات الضخمة مع الشابتر...'}, ensure_ascii=False)}\n\n"
            orchestrator = AIOrchestrator()
            llm = get_llm_for_user(profile["id"])
            anno_task = asyncio.create_task(orchestrator.generate_complete_annotations(llm, transcript, pdf_context))
            
            AI_TIMEOUT_SECONDS = 900  # 15 minutes for massive batch processing
            elapsed = 0
            while not anno_task.done() and elapsed < AI_TIMEOUT_SECONDS:
                yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': f'🧠 4/5 جاري ربط المفاهيم... ({elapsed}s)'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(4)
                elapsed += 4
            
            if not anno_task.done():
                anno_task.cancel()
                annotations = {"highlights": [], "sticky_notes": [], "slide_explanations": [], "mcqs": [], "flashcards": []}
            else:
                annotations = await anno_task

            # Apply free trial limits if active
            if is_free_trial:
                if isinstance(annotations.get("slide_explanations"), list):
                    annotations["slide_explanations"] = annotations["slide_explanations"][:2]
                    annotations["slides_locked"] = True
                if isinstance(annotations.get("expected_questions"), list):
                    annotations["expected_questions"] = annotations["expected_questions"][:2]
                if isinstance(annotations.get("flashcards"), list):
                    annotations["flashcards"] = annotations["flashcards"][:2]

            # Generate AI Title
            ai_title = await generate_ai_title(llm, transcript)
            
            # Deduct quota + save history
            history_meta = {
                "title": ai_title,
                "transcript": transcript,
                "annotations": annotations,
                "job_id": unique_id,
                "has_pdf": bool(pdf_path)
            }
            log_id = use_lecture_quota(profile["id"], metadata=history_meta, cost=calculated_cost)

            # 8. Generate Output PDF - using the SAME annotator as process_lecture
            download_url = None
            if pdf_path and annotations.get("slide_explanations"):
                _msg5 = json.dumps({'type': 'progress', 'step': '5/5', 'message': '🎨 5/5 جاري رسم الملزمة المدمجة وتنسيقها...'}, ensure_ascii=False)
                yield f"data: {_msg5}\n\n"
                base_dir_out = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                output_pdf_path = os.path.join(base_dir_out, "outputs", f"{unique_id}.pdf")
                os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)

                annotatable_pdf_path = pdf_path
                if pdf_path.lower().endswith(".pptx"):
                    try:
                        from app.services.pptx_converter import convert_pptx_to_pdf
                        annotatable_pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, os.path.dirname(pdf_path))
                    except Exception as pptx_err:
                        logger.error(f"Failed to convert PPTX to PDF for batch annotation: {pptx_err}")
                        yield f"data: {json.dumps({'type': 'error', 'message': 'فشل تحويل ملف البوربوينت إلى PDF للرسم والتلوين. يرجى رفع ملف PDF مباشرة.'}, ensure_ascii=False)}\n\n"
                        return

                from app.services.pdf_annotator import annotate_pdf
                await asyncio.to_thread(annotate_pdf, annotatable_pdf_path, output_pdf_path, annotations, is_free_trial)
                download_url = f"/api/v1/summarize/download/{unique_id}.pdf"


            # Emit events matching what the frontend stream reader expects (metadata + done)
            _meta_msg = json.dumps({'type': 'metadata', 'transcript': transcript, 'annotations': annotations}, ensure_ascii=False)
            yield f"data: {_meta_msg}\n\n"
            _done_msg = json.dumps({'type': 'done', 'download_url': download_url, 'history_id': log_id, 'title': ai_title}, ensure_ascii=False)
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
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
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
            logger.warning(f"[WARN] PDF temp file not found after {max_pdf_wait}s: {pdf_path} — continuing without slides.")
            pdf_path = None
    elif pdf_file:
        os.makedirs(upload_dir, exist_ok=True)
        pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_file.filename}")
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(pdf_file.file, buffer)

    async def event_stream():
        nonlocal audio_path, pdf_path
        try:
            # 0. Flush initial headers & Padding
            padding = ":" + " " * 1024 + "\n\n"
            yield padding
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🚀 بدأنا العمل! جاري تحضير المحرك...'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.1)

            # Detect if this is a document-only mode (no audio, no URL)
            is_document_only = (audio_path is None) and (not lecture_url)

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

            if is_document_only:
                # Calculate cost based on PDF page count
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📏 جاري حساب حجم الوثيقة وتكلفة المعالجة...'}, ensure_ascii=False)}\n\n"
                try:
                    if pdf_path and pdf_path.lower().endswith(".pptx"):
                        from app.services.pptx_converter import extract_text_from_pptx
                        pptx_pages_cost = await asyncio.to_thread(extract_text_from_pptx, pdf_path)
                        num_pages = len(pptx_pages_cost)
                    elif pdf_path:
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
                    duration_str = (await asyncio.to_thread(subprocess.check_output, cmd)).decode().strip()
                    duration_seconds = float(duration_str)
                except Exception:
                    duration_seconds = os.path.getsize(audio_path) / (128000 / 8)

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
                pos = status['position']
                yield f"data: {json.dumps({'type': 'queued', 'position': pos, 'message': f'#{pos} | {current_msg}'}, ensure_ascii=False)}\n\n"
                msg_idx += 1
                await asyncio.sleep(4)
            
            if job.status == JobStatus.CANCELLED:
                yield f"data: {json.dumps({'type': 'error', 'message': 'تم إلغاء المهمة.'}, ensure_ascii=False)}\n\n"
                return

            # 1. Transcribe (skip in document-only mode)
            if is_document_only:
                transcript = ""
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': '📄 1/5 وضع الوثيقة فقط: سيتم توليد الملخص مباشرة من الملف...'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'time_estimate': 180, 'message': '🎧 1/5 جاري تفريغ الصوت آلياً... نعدك بدقة عالية!'}, ensure_ascii=False)}\n\n"
                
                import queue
                progress_q = queue.Queue()
                def audio_progress_cb(msg):
                    progress_q.put(msg)
                    
                transcription_task = asyncio.create_task(asyncio.to_thread(transcribe_audio, audio_path, audio_progress_cb))
                
                last_audio_msg = 'لمّاح يستمع بتركيز... جاري تحويل الكلام لنص بدقة عالية!'
                while not transcription_task.done():
                    try:
                        while True:
                            last_audio_msg = progress_q.get_nowait()
                            yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': f'🎧 1/5 لمّاح يستمع... {last_audio_msg}'}, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        pass
                    
                    # Ping every 3 seconds to keep connection alive for huge files (2.5+ hours)
                    yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'message': f'🎧 1/5 لمّاح يعمل... {last_audio_msg}'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(3)
                    
                transcript = transcription_task.result()
                yield f"data: {json.dumps({'type': 'transcript', 'text': transcript}, ensure_ascii=False)}\n\n"
            
            # 2. Document Handling
            pdf_context = []
            if pdf_path:
                if pdf_path.lower().endswith(".pptx"):
                    yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '📑 2/5 اكتشفنا ملف بوربوينت! جاري استخراج محتوى الشرائح...'}, ensure_ascii=False)}\n\n"
                    from app.services.pptx_converter import extract_text_from_pptx
                    pptx_pages = await asyncio.to_thread(extract_text_from_pptx, pdf_path)
                    logger.info(f"[PROCESS_LECTURE] PPTX extracted {len(pptx_pages)} slides from {pdf_path}")
                    pdf_context = [f"--- الشريحة {p['page_number']} ---\n{p['text']}" for p in pptx_pages[:150]]
                else:
                    yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '🔍 2/5 جاري قراءة محتوى الشرائح بدقة هندسية...'}, ensure_ascii=False)}\n\n"
                    pdf_pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                    logger.info(f"[PROCESS_LECTURE] PDF extracted {len(pdf_pages)} pages from {pdf_path}")
                    pdf_context = [f"--- Page {p['page_number']} ---\n{p['text']}" for p in pdf_pages[:150]]

                if not pdf_context:
                    logger.warning(f"[PROCESS_LECTURE] pdf_context is EMPTY after extraction — file may be image-based or corrupted: {pdf_path}")

            
            # 3. AI Annotations
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'time_estimate': 120, 'message': '🧠 4/5 لمّاح يعمل الآن! جاري استنتاج أهم النقاط والأسئلة المتوقعة...'}, ensure_ascii=False)}\n\n"
            
            orchestrator = AIOrchestrator()
            llm = get_llm_for_user(profile["id"])
            anno_task = asyncio.create_task(orchestrator.generate_complete_annotations(llm, transcript, pdf_context))
            
            AI_TIMEOUT_SECONDS = 600  # 10 minutes deadline for large lectures
            elapsed = 0
            while not anno_task.done() and elapsed < AI_TIMEOUT_SECONDS:
                yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': f'🧠 4/5 لمّاح يفكّر... ({elapsed}s)'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(4)
                elapsed += 4
            
            if not anno_task.done():
                anno_task.cancel()
                logger.warning("[ORCHESTRATOR] AI timeout after 10 minutes — returning empty annotations.")
                annotations = {"highlights": [], "sticky_notes": [], "slide_explanations": [], "mcqs": [], "flashcards": []}
            else:
                annotations = await anno_task
            

            # 4. Final PDF
            download_url = None
            if pdf_path:
                yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'message': '🎨 5/5 اللمسات الأخيرة! جاري تلوين الشرائح وإضافة الملاحظات الذكية...'}, ensure_ascii=False)}\n\n"
                
                actual_pdf_path = pdf_path
                if pdf_path.lower().endswith(".pptx"):
                    try:
                        from app.services.pptx_converter import convert_pptx_to_pdf
                        temp_pdf_dir = os.path.dirname(pdf_path)
                        actual_pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, temp_pdf_dir)
                        logger.info(f"[PROCESS_LECTURE] PPTX converted successfully to PDF: {actual_pdf_path}")
                    except Exception as ce:
                        logger.error(f"[PROCESS_LECTURE] Failed to convert PPTX to PDF for annotation: {ce}")
                        actual_pdf_path = None
                
                if actual_pdf_path:
                    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                    output_pdf_path = os.path.join(base_dir, "outputs", f"{job.job_id}.pdf")
                    os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
                    
                    from app.services.pdf_annotator import annotate_pdf
                    await asyncio.to_thread(annotate_pdf, actual_pdf_path, output_pdf_path, annotations, False)
                    download_url = f"/api/v1/summarize/download/{job.job_id}.pdf"
 
            # 5. Save History & Finalize
            # Generate AI Title
            ai_title = await generate_ai_title(llm, transcript)
            
            history_meta = {
                "title": ai_title,
                "transcript": transcript,
                "annotations": annotations,
                "job_id": job.job_id,
                "has_pdf": bool(pdf_path)
            }
            log_id = use_lecture_quota(profile["id"], metadata=history_meta, cost=calculated_cost)
            
            yield f"data: {json.dumps({'type': 'metadata', 'annotations': annotations}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'download_url': download_url, 'history_id': log_id, 'title': ai_title, 'annotations': annotations}, ensure_ascii=False)}\n\n"

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
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    })

@router.get("/download/{job_id}.pdf")
async def download_annotated_pdf(job_id: str):
    """Secure PDF download checking both backend and project root outputs."""
    routes_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(routes_dir)))
    
    path_1 = os.path.join(backend_dir, "outputs", f"{job_id}.pdf")
    path_2 = os.path.join(os.path.dirname(backend_dir), "outputs", f"{job_id}.pdf")
    
    resolved_path = None
    if os.path.exists(path_1):
        resolved_path = path_1
    elif os.path.exists(path_2):
        resolved_path = path_2
        
    if not resolved_path:
        logger.error(f"[DOWNLOAD] File not found for job_id={job_id}. Checked paths: {path_1} and {path_2}")
        raise HTTPException(status_code=404, detail="File not found")
        
    from fastapi.responses import FileResponse
    return FileResponse(resolved_path, media_type="application/pdf")


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


@router.get("/debug-env")
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
            import fitz
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
