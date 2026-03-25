from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.rag_service import process_and_store_document, query_relevant_context
from app.services.summarizer_service import generate_smart_annotations
from app.services.subscription_service import use_lecture_quota
from app.core.auth_guard import check_quota
from app.core.queue_manager import queue_manager, JobStatus
from app.core.logger import logger
import shutil
import os
import uuid
import traceback
import json
import asyncio
import re

router = APIRouter()


# ====== المسار الرئيسي: معالجة محاضرة مع طابور + حماية ======
@router.post("/process-lecture")
async def process_lecture(
    audio_file: UploadFile = File(...),
    pdf_file: Optional[UploadFile] = File(None),
    profile=Depends(check_quota)
):
    """Process a lecture with auth guard, quota check, and queue system."""
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    unique_id = str(uuid.uuid4())
    audio_path = os.path.join(upload_dir, f"{unique_id}_{audio_file.filename}")
    
    with open(audio_path, "wb") as buffer:
        shutil.copyfileobj(audio_file.file, buffer)
        
    pdf_path = None
    if pdf_file:
        pdf_path = os.path.join(upload_dir, f"{unique_id}_{pdf_file.filename}")
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(pdf_file.file, buffer)
    
    # Determine plan name for priority
    plan_name = ""
    if profile.get("current_plan_id"):
        from app.services.subscription_service import get_plan_by_id
        plan = get_plan_by_id(profile["current_plan_id"])
        plan_name = plan.get("name", "") if plan else ""
    
    # Enqueue the job
    try:
        job = await queue_manager.enqueue(
            user_id=profile["id"],
            payload={
                "audio_path": audio_path,
                "pdf_path": pdf_path,
                "unique_id": unique_id,
                "audio_filename": audio_file.filename,
                "pdf_filename": pdf_file.filename if pdf_file else None
            },
            plan_name=plan_name
        )
    except ValueError as e:
        raise HTTPException(status_code=429, detail={"user_message": str(e)})
    
    # Return SSE stream that follows the job
    async def event_stream():
        # Send initial queued status
        yield f"data: {json.dumps({'type': 'queued', 'job_id': job.job_id, 'position': job.position, 'estimated_wait': job.estimated_wait_seconds}, ensure_ascii=False)}\n\n"
        
        # Wait for job to start processing
        while job.status == JobStatus.QUEUED:
            status = queue_manager.get_job_status(job.job_id)
            yield f"data: {json.dumps({'type': 'queue_update', 'position': status['position'], 'estimated_wait': status['estimated_wait_seconds']}, ensure_ascii=False)}\n\n"
            await queue_manager.wait_for_update(job.job_id, timeout=5)
        
        if job.status == JobStatus.CANCELLED:
            yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
            return
        
        # Job is now processing - run the actual work
        yield f"data: {json.dumps({'type': 'processing', 'step': '1/5', 'time_estimate': 120, 'message': '1/5 أهلاً بك يا بطل! 🎧 نقوم الآن بتفريغ الصوت والتركيز مع كلام المحاضر...'}, ensure_ascii=False)}\n\n"
        
        try:
            payload = job.payload
            
            # 1. Transcribe
            logger.info(f"[Queue:{job.job_id[:8]}] [1/5] تفريغ الصوت...")
            yield f"data: {json.dumps({'type': 'progress', 'step': '1/5', 'time_estimate': 120, 'message': '1/5 أهلاً بك يا بطل! 🎧 نقوم الآن بتفريغ الصوت والتركيز مع كلام المحاضر...'}, ensure_ascii=False)}\n\n"
            
            # Force flush the SSE headers and progress message before the big synchronous block
            await asyncio.sleep(0.1)
            
            transcript = transcribe_audio(payload["audio_path"])
            
            # 2. Extract Document Context
            pdf_context = []
            document_id = None
            if payload.get("pdf_path"):
                if payload["pdf_path"].lower().endswith(".pptx"):
                    yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'time_estimate': 20, 'message': '2/5 جاري تحويل عرض الباوربوينت (PPTX) إلى صيغة مدعومة وبجودة أصلية للحفاظ على التنسيق... ⏳'}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.1)
                    from app.services.pptx_converter import convert_pptx_to_pdf
                    output_dir = os.path.dirname(payload["pdf_path"])
                    payload["pdf_path"] = convert_pptx_to_pdf(payload["pdf_path"], output_dir)

                yield f"data: {json.dumps({'type': 'progress', 'step': '2/5', 'time_estimate': 15, 'message': '2/5 عمل رائع! الصوت أصبح جاهزاً 🤓 والآن نقرأ الشرائح شريحة بشريحة (ثوانٍ ونعود لك 🗺️)'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.1)
                
                yield f"data: {json.dumps({'type': 'progress', 'step': '3/5', 'time_estimate': 40, 'message': '3/5 نحن الآن نربط كلام المحاضر بالشرائح، ونبني لك قاعدة بيانات ذكية لتسأل عن أي شيء براحتك 😎'}, ensure_ascii=False)}\n\n"
                pdf_pages = extract_pages_from_pdf(payload["pdf_path"])
                
                document_id = str(uuid.uuid4())
                from app.services.rag_service import process_and_store_document
                process_and_store_document(document_id, pdf_pages)
                
                # Bypassing lossy RAG for slides: Feeding the entire document 
                # directly into Gemini's massive context window enables perfect semantic matching
                # regardless of English/Arabic translation inconsistencies.
                max_pages = pdf_pages[:150] # Cap at 150 slides to prevent absurd token costs
                pdf_context = [f"--- Page {p['page_number']} ---\n{p['text']}" for p in max_pages]
            
            # 3. Generate Annotations (JSON)
            yield f"data: {json.dumps({'type': 'progress', 'step': '4/5', 'time_estimate': 90, 'message': '4/5 يا سلام! حان وقت السحر ✨ النظام يستخرج الآن الأسئلة المتوقعة والملاحظات المهمة بدقة...'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.1)
            
            annotations = generate_smart_annotations(transcript=transcript, pdf_context=pdf_context)
            
            # Send metadata to frontend for display or debugging
            metadata = {
                "type": "metadata",
                "transcript": transcript,
                "annotations": annotations
            }
            yield f"data: {json.dumps(metadata, ensure_ascii=False)}\n\n"
            
            # 5. Apply Annotations to PDF
            download_url = None
            if payload.get("pdf_path"):
                yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'time_estimate': 15, 'message': '5/5 اللمسات الأخيرة 🎨 نزرع الآن ألواننا وملاحظاتنا على الشرائح لتخرج لك ملزمة تفتح النفس!'}, ensure_ascii=False)}\n\n"
                
                output_dir = "outputs"
                os.makedirs(output_dir, exist_ok=True)
                output_pdf_path = os.path.join(output_dir, f"{job.job_id}.pdf")
                
                from app.services.pdf_annotator import annotate_pdf
                annotate_pdf(
                    input_path=payload["pdf_path"], 
                    output_path=output_pdf_path, 
                    annotations=annotations
                )
                
                download_url = f"/api/v1/summarize/download/{job.job_id}.pdf"
            else:
                yield f"data: {json.dumps({'type': 'progress', 'step': '5/5', 'time_estimate': 5, 'message': '5/5 انتهينا! 🎉 لعدم وجود ملف PDF، نقوم الآن بتجهيز الملخص والأسئلة لتستفيد منها مباشرة.'}, ensure_ascii=False)}\n\n"
            
            # Use quota and persist metadata to history
            history_meta = {
                "transcript": transcript,
                "annotations": annotations,
                "job_id": job.job_id,
                "document_id": document_id
            }
            log_id = use_lecture_quota(profile["id"], metadata=history_meta)
            
            job.status = JobStatus.COMPLETED
            yield f"data: {json.dumps({'type': 'done', 'download_url': download_url, 'history_id': log_id})}\n\n"
            logger.info(f"[Queue:{job.job_id[:8]}] ✅ اكتملت المعالجة بنجاح")
            
        except Exception as e:
            logger.error(f"[Queue:{job.job_id[:8]}] ❌ فشل: {str(e)}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error = str(e)
            
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower() or "Rate limit" in error_str:
                wait_match = re.search(r'try again in (\d+m[\d.]+s|\d+[\d.]*s)', error_str)
                wait_msg = f" يرجى الانتظار {wait_match.group(1)}." if wait_match else ""
                user_msg = f"⏳ تم تجاوز الحد المسموح لمحرك الذكاء الاصطناعي.{wait_msg}"
            elif "401" in error_str or "invalid_api_key" in error_str.lower():
                user_msg = "🔑 مشكلة في مفتاح API. تواصل مع الدعم."
            else:
                user_msg = "❌ حدث خطأ أثناء المعالجة. يرجى المحاولة مرة أخرى."
            
            yield f"data: {json.dumps({'type': 'error', 'message': user_msg}, ensure_ascii=False)}\n\n"
        
        finally:
            # Cleanup source files
            try:
                if os.path.exists(payload.get("audio_path", "")):
                    os.remove(payload["audio_path"])
                if payload.get("pdf_path") and os.path.exists(payload["pdf_path"]):
                    os.remove(payload["pdf_path"])
            except Exception:
                pass
    
    # Mark job as processing
    job.status = JobStatus.PROCESSING
    import time
    job.started_at = time.time()
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@router.get("/download/{job_id}.pdf")
async def download_annotated_pdf(job_id: str):
    """Download the final annotated PDF"""
    file_path = f"outputs/{job_id}.pdf"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found or expired")
    
    from fastapi.responses import FileResponse
    return FileResponse(
        path=file_path, 
        media_type="application/pdf", 
        headers={"Content-Disposition": f'inline; filename="Smart_Annotated_{str(job_id)[:8]}.pdf"'}
    )





# ====== مسار حالة الطابور ======
@router.get("/queue-stats")
async def get_queue_stats():
    """Get queue statistics."""
    return queue_manager.get_queue_stats()


@router.get("/job/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a specific job."""
    status = queue_manager.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail={"user_message": "المهمة غير موجودة."})
    return status
