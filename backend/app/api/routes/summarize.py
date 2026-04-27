from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from fastapi.responses import StreamingResponse
from typing import Optional
from app.services.audio_service import transcribe_audio
from app.services.pdf_service import extract_pages_from_pdf
from app.services.rag_service import process_and_store_document, query_relevant_context
from app.services.summarizer_service import generate_smart_annotations
from app.services.subscription_service import use_lecture_quota, log_usage_failure
from app.core.auth_guard import check_quota, verify_phone_active
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
@router.post("/process-lecture", dependencies=[Depends(verify_phone_active)])
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
        
    is_free_trial_user = profile.get("current_plan_id") is None or profile.get("current_plan_id") == "free" or profile.get("quota_remaining", 0) <= 1
    is_audio_trimmed = False
    
    if is_free_trial_user:
        try:
            import subprocess
            result = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1", audio_path
            ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            duration = float(result.stdout)
            if duration > 15 * 60:
                ext = os.path.splitext(audio_path)[1]
                trimmed_path = audio_path + "_trimmed" + ext
                subprocess.run([
                    "ffmpeg", "-y", "-i", audio_path, "-t", "900", "-c", "copy", trimmed_path
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.replace(trimmed_path, audio_path)
                is_audio_trimmed = True
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Failed to check audio duration: {e}. Falling back to file size check.")
            file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
            if file_size_mb > 15.0:
                try:
                    ext = os.path.splitext(audio_path)[1]
                    trimmed_path = audio_path + "_trimmed" + ext
                    subprocess.run([
                        "ffmpeg", "-y", "-i", audio_path, "-t", "900", "-c", "copy", trimmed_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.replace(trimmed_path, audio_path)
                    is_audio_trimmed = True
                except Exception as trim_e:
                    logger.warning(f"Failed to trim audio by size fallback: {trim_e}")
            
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
                "pdf_filename": pdf_file.filename if pdf_file else None,
                "is_audio_trimmed": is_audio_trimmed
            },
            plan_name=plan_name
        )
    except ValueError as e:
        raise HTTPException(status_code=429, detail={"user_message": str(e)})
    
    # Return SSE stream that follows the job
    async def event_stream():
        payload = job.payload
        is_free_trial_queue = profile.get("current_plan_id") is None or profile.get("current_plan_id") == "free" or profile.get("quota_remaining", 0) <= 1
        base_msg = "أنت في طابور الانتظار لمعالجة المحاضرة... 🚀 اشترك الآن لتخطي الطابور ومعالجة محاضراتك فوراً وفي ثوانٍ!" if is_free_trial_queue else None
        
        if payload.get("is_audio_trimmed"):
            queue_msg = "⚠️ تم قص المحاضرة لمعالجة أول 15 دقيقة فقط (الحد المجاني). اشترك لمعالجة محاضراتك كاملة! \n\n" + (base_msg or "أنت في طابور الانتظار...")
        else:
            queue_msg = base_msg
            
        initial_queued_data = {'type': 'queued', 'job_id': job.job_id, 'position': job.position, 'estimated_wait': job.estimated_wait_seconds}
        if queue_msg: initial_queued_data['message'] = queue_msg
        yield f"data: {json.dumps(initial_queued_data, ensure_ascii=False)}\n\n"
        
        # Wait for job to start processing
        while job.status == JobStatus.QUEUED:
            status = queue_manager.get_job_status(job.job_id)
            update_data = {'type': 'queue_update', 'position': status['position'], 'estimated_wait': status['estimated_wait_seconds']}
            if queue_msg: update_data['message'] = queue_msg
            yield f"data: {json.dumps(update_data, ensure_ascii=False)}\n\n"
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
            
            annotations = generate_smart_annotations(transcript=transcript, pdf_context=pdf_context, user_id=profile["id"])
            
            # Free trial limit: restrict generated content as an upsell
            is_free_trial = profile.get("current_plan_id") is None or profile.get("current_plan_id") == "free" or profile.get("quota_remaining", 0) <= 1
            if is_free_trial:
                if isinstance(annotations.get("slide_explanations"), list):
                    full_slides = annotations["slide_explanations"]
                    annotations["slide_explanations"] = full_slides[:2]
                    annotations["slides_locked"] = True
                    annotations["total_slides"] = len(full_slides)
                    logger.info(f"[FREE_TRIAL] Limited slide_explanations from {len(full_slides)} to 2 slides")
                
                if isinstance(annotations.get("study_plan"), list) and len(annotations["study_plan"]) > 1:
                    annotations["study_plan"] = annotations["study_plan"][:1]
                    annotations["study_plan"].append("... (اشترك في الباقة المدفوعة لرؤية باقي خطة الدراسة المخصصة لك)")
                
                if isinstance(annotations.get("expected_questions"), list) and len(annotations["expected_questions"]) > 1:
                    annotations["expected_questions"] = annotations["expected_questions"][:1]
                    annotations["expected_questions"].append({
                        "question": "🔒 اشترك في الباقة المدفوعة لرؤية باقي الأسئلة المتوقعة واختبار نفسك!",
                        "answer": "الاشتراك يمنحك وصول كامل لجميع أسئلة المحاضرة وأجوبتها النموذجية."
                    })
                
                if isinstance(annotations.get("general_summary"), str):
                    summary_words = annotations["general_summary"].split()
                    if len(summary_words) > 30:
                        annotations["general_summary"] = " ".join(summary_words[:30]) + " ... \n\n(🔒 اشترك لقراءة الملخص الشامل للمحاضرة)"
                        
                if isinstance(transcript, str):
                    paragraphs = [p for p in transcript.split('\n') if p.strip()]
                    first_paragraph = paragraphs[0] if paragraphs else ""
                    transcript = first_paragraph + "\n\n🔒 تم إخفاء باقي تفريغ المحاضرة... اشترك لتقرأ كل حرف قاله الدكتور"
            
            if payload.get("is_audio_trimmed"):
                annotations["is_audio_trimmed"] = True
                
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
                    annotations=annotations,
                    is_free_trial=is_free_trial
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
            # 6. Final Step: Deduct quota ONLY after everything is successful
            # This ensures that if any error occurred above, the user doesn't lose their trial/credit.
            log_id = use_lecture_quota(profile["id"], metadata=history_meta)
            
            job.status = JobStatus.COMPLETED
            yield f"data: {json.dumps({'type': 'done', 'download_url': download_url, 'history_id': log_id})}\n\n"
            logger.info(f"[Queue:{job.job_id[:8]}] [SUCCESS] اكتملت المعالجة بنجاح")
            
        except Exception as e:
            logger.error(f"[Queue:{job.job_id[:8]}] [FAILURE] فشل: {str(e)}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error = str(e)
            
            # Log the failure but do NOT deduct quota
            log_usage_failure(profile["id"], str(e))
            
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
async def download_annotated_pdf(job_id: str, profile=Depends(get_user_profile)):
    """Download the final annotated PDF with security checks."""
    import re
    
    # 1. Validate Job ID format (UUID)
    if not re.match(r'^[a-f0-9\-]{36}$', job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    
    # 2. Prevent path traversal
    safe_name = f"{job_id}.pdf"
    file_path = os.path.join("outputs", safe_name)
    resolved_path = os.path.abspath(file_path)
    base_dir = os.path.abspath("outputs")
    
    if not resolved_path.startswith(base_dir):
        logger.warning(f"🚫 Path traversal attempt by {profile['id']}: {job_id}")
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="File not found or expired")
    
    # 3. Verify ownership
    # We check if this job_id exists in usage_logs for this user
    from app.db.supabase_client import get_supabase
    supabase = get_supabase()
    usage_check = supabase.table("usage_logs")\
        .select("id")\
        .eq("user_id", profile["id"])\
        .eq("metadata->>job_id", job_id)\
        .execute()
    
    if not usage_check.data:
        # Also check current queue if not finished yet
        job = queue_manager.get_job(job_id)
        if not job or job.user_id != profile["id"]:
            logger.warning(f"🚫 Unauthorized download attempt: User {profile['id']} -> Job {job_id}")
            raise HTTPException(status_code=403, detail="Access denied: You don't own this file")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=resolved_path, 
        media_type="application/pdf", 
        headers={"Content-Disposition": f'inline; filename="Smart_Annotated_{str(job_id)[:8]}.pdf"'}
    )





# ====== مسار حالة الطابور ======
@router.get("/queue-stats")
async def get_queue_stats(profile=Depends(get_user_profile)):
    """Get queue statistics (Authenticated users only)."""
    return queue_manager.get_queue_stats()


@router.get("/job/{job_id}")
async def get_job_status(job_id: str, profile=Depends(get_user_profile)):
    """Get status of a specific job with ownership check."""
    status = queue_manager.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail={"user_message": "المهمة غير موجودة."})
    
    # Ownership Check
    job = queue_manager.get_job(job_id)
    if job and job.user_id != profile["id"]:
        logger.warning(f"🚫 Unauthorized job status check: User {profile['id']} tried to access job {job_id}")
        raise HTTPException(status_code=403, detail={"user_message": "ليس لديك صلاحية لمشاهدة هذه المهمة."})
        
    return status
