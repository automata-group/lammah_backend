from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.rag_service import process_and_store_document, query_relevant_context
from app.services.summarizer_service import generate_smart_annotations
from app.services.subscription_service import use_lecture_quota, log_usage_failure
from app.core.auth_guard import check_quota, verify_phone_active, get_user_profile
from app.core.queue_manager import queue_manager, JobStatus, JobPriority
from app.core.logger import logger
import shutil
import os
import uuid
import traceback
import json
import asyncio
import re

router = APIRouter()

@router.post("/upload-temp")
async def upload_temp(file: UploadFile = File(...)):
    """
    Pre-upload endpoint to save files before the main processing starts.
    Returns a unique ID that can be used later.
    """
    temp_dir = "uploads/temp"
    os.makedirs(temp_dir, exist_ok=True)
    
    unique_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1]
    temp_filename = f"{unique_id}{ext}"
    temp_path = os.path.join(temp_dir, temp_filename)
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return {"temp_id": temp_filename, "filename": file.filename}

@router.post("/process-lecture", dependencies=[Depends(verify_phone_active)])
async def process_lecture(
    request: Request,
    audio_file: Optional[UploadFile] = File(None),
    pdf_file: Optional[UploadFile] = File(None),
    audio_temp_id: Optional[str] = None,
    pdf_temp_id: Optional[str] = None,
    profile=Depends(check_quota)
):
    """
    Lecture processing endpoint.
    Returns a StreamingResponse immediately to provide real-time background updates.
    """
    unique_id = str(uuid.uuid4())[:8]

    async def event_stream():
        try:
            # 0. Flush initial headers & Padding
            padding = ":" + " " * 1024 + "\n\n"
            yield padding
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🚀 بدأنا العمل! جاري تحضير المحرك...'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.1)

            # 1. Save Files
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📥 جاري استلام الملفات وتحضيرها...'}, ensure_ascii=False)}\n\n"
            upload_dir = "uploads"
            temp_dir = "uploads/temp"
            os.makedirs(upload_dir, exist_ok=True)
            
            # Handle Audio (either direct upload or pre-uploaded temp id)
            if audio_temp_id:
                temp_path = os.path.join(temp_dir, audio_temp_id)
                if not os.path.exists(temp_path):
                    raise HTTPException(status_code=400, detail="Audio temp file not found")
                audio_path = os.path.join(upload_dir, f"{unique_id}_{audio_temp_id}")
                shutil.move(temp_path, audio_path)
            elif audio_file:
                audio_path = os.path.join(upload_dir, f"{unique_id}_{audio_file.filename}")
                with open(audio_path, "wb") as buffer:
                    shutil.copyfileobj(audio_file.file, buffer)
            else:
                raise HTTPException(status_code=400, detail="Missing audio file")
            
            # Handle PDF
            pdf_path = None
            if pdf_temp_id:
                temp_path = os.path.join(temp_dir, pdf_temp_id)
                if os.path.exists(temp_path):
                    pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_temp_id}")
                    shutil.move(temp_path, pdf_path)
            elif pdf_file:
                pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_file.filename}")
                with open(pdf_path, "wb") as buffer:
                    shutil.copyfileobj(pdf_file.file, buffer)
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📑 تم حفظ البوربوينت/PDF بنجاح.'}, ensure_ascii=False)}\n\n"

            # 2. Quota Check & Trim
            is_free_trial = profile.get("current_plan_id") is None or profile.get("current_plan_id") == "free"
            is_audio_trimmed = False
            duration_seconds = 0
            
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '📏 جاري فحص مدة المحاضرة وحساب الحصة...'}, ensure_ascii=False)}\n\n"
            
            try:
                import subprocess
                cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
                duration_str = (await asyncio.to_thread(subprocess.check_output, cmd)).decode().strip()
                duration_seconds = float(duration_str)
            except Exception:
                duration_seconds = os.path.getsize(audio_path) / (128000 / 8)

            if is_free_trial and duration_seconds > 15 * 60:
                yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '✂️ النسخة المجانية: جاري قص أول 15 دقيقة من المحاضرة...'}, ensure_ascii=False)}\n\n"
                ext = os.path.splitext(audio_path)[1]
                trimmed_path = audio_path + "_trimmed" + ext
                trim_cmd = ["ffmpeg", "-y", "-i", audio_path, "-t", "900", "-c", "copy", trimmed_path]
                await asyncio.to_thread(subprocess.run, trim_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.replace(trimmed_path, audio_path)
                is_audio_trimmed = True

            # 3. Queue Job
            yield f"data: {json.dumps({'type': 'progress', 'step': '0/5', 'message': '🎟️ تم حجز مكان لك في طابور المعالجة...'}, ensure_ascii=False)}\n\n"
            
            # Get the plan name to determine priority in queue
            plan_name = "مجاني"
            if not is_free_trial and profile.get("current_plan_id"):
                from app.services.subscription_service import get_plan_by_id
                plan = get_plan_by_id(profile["current_plan_id"])
                plan_name = plan.get("name", "مجاني") if plan else "مجاني"
            
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

            # 1. Transcribe
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
                    yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '📑 2/5 اكتشفنا ملف بوربوينت! جاري تحويله لملف PDF فائق الجودة...'}, ensure_ascii=False)}\n\n"
                    from app.services.pptx_converter import convert_pptx_to_pdf
                    pdf_path = await asyncio.to_thread(convert_pptx_to_pdf, pdf_path, os.path.dirname(pdf_path))
                
                yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'message': '🔍 2/5 جاري قراءة محتوى الشرائح بدقة هندسية...'}, ensure_ascii=False)}\n\n"
                pdf_pages = await asyncio.to_thread(extract_pages_from_pdf, pdf_path)
                
                # Full context for MiniMax/Gemini
                max_pages = pdf_pages[:150]
                pdf_context = [f"--- Page {p['page_number']} ---\n{p['text']}" for p in max_pages]
            
            # 3. AI Annotations
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'time_estimate': 120, 'message': '🧠 4/5 لمّاح يعمل الآن! جاري استنتاج أهم النقاط والأسئلة المتوقعة...'}, ensure_ascii=False)}\n\n"
            
            anno_task = asyncio.create_task(asyncio.to_thread(generate_smart_annotations, transcript, pdf_context, profile["id"]))
            while not anno_task.done():
                yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'message': '🧠 4/5 لمّاح يفكّر الآن... جاري استنتاج أهم النقاط والأسئلة المتوقعة بكل ذكاء...'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(4)
                
            annotations = anno_task.result()
            
            # Apply Trial Limits
            if is_free_trial:
                if isinstance(annotations.get("slide_explanations"), list):
                    annotations["slide_explanations"] = annotations["slide_explanations"][:2]
                    annotations["slides_locked"] = True
                if isinstance(annotations.get("expected_questions"), list):
                    annotations["expected_questions"] = annotations["expected_questions"][:2]

            # 4. Final PDF
            download_url = None
            if pdf_path:
                yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'message': '🎨 5/5 اللمسات الأخيرة! جاري تلوين الشرائح وإضافة الملاحظات الذكية...'}, ensure_ascii=False)}\n\n"
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                output_pdf_path = os.path.join(base_dir, "outputs", f"{job.job_id}.pdf")
                os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
                
                from app.services.pdf_annotator import annotate_pdf
                await asyncio.to_thread(annotate_pdf, pdf_path, output_pdf_path, annotations, is_free_trial)
                download_url = f"/api/v1/summarize/download/{job.job_id}.pdf"

            # 5. Save History & Finalize
            history_meta = {"transcript": transcript, "annotations": annotations, "job_id": job.job_id}
            log_id = use_lecture_quota(profile["id"], metadata=history_meta)
            
            yield f"data: {json.dumps({'type': 'metadata', 'transcript': transcript, 'annotations': annotations}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'download_url': download_url, 'history_id': log_id}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"STREAM ERROR: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'❌ فشل: {str(e)}'}, ensure_ascii=False)}\n\n"
        finally:
            # Cleanup
            try:
                if os.path.exists(audio_path): os.remove(audio_path)
                if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
            except: pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@router.get("/download/{job_id}.pdf")
async def download_annotated_pdf(job_id: str):
    """Secure PDF download."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    resolved_path = os.path.join(base_dir, "outputs", f"{job_id}.pdf")
    if not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="File not found")
    from fastapi.responses import FileResponse
    return FileResponse(resolved_path, media_type="application/pdf")
