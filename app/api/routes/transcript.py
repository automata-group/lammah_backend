from fastapi import APIRouter, File, UploadFile, Depends, Request
from fastapi.responses import StreamingResponse
from app.services.audio_service import transcribe_audio
from app.core.auth_guard import check_quota, verify_phone_active
from app.core.logger import logger
import os
import uuid
import json
import asyncio
import shutil

from pydantic import BaseModel
from fastapi.responses import StreamingResponse, FileResponse
import arabic_reshaper
from bidi.algorithm import get_display
from fpdf import FPDF
import io

router = APIRouter()

class PDFRequest(BaseModel):
    transcript: str
    title: str = "تفريغ محاضرة لمّاح"

@router.post("/export-pdf")
async def export_transcript_pdf(data: PDFRequest):
    try:
        # 1. Reshape Arabic text
        reshaped_text = arabic_reshaper.reshape(data.transcript)
        bidi_text = get_display(reshaped_text)
        reshaped_title = arabic_reshaper.reshape(data.title)
        bidi_title = get_display(reshaped_title)

        # 2. Create PDF
        pdf = FPDF()
        pdf.add_page()
        
        # Note: We need a font that supports Arabic. 
        # For now, we'll check for a common path or skip if not found
        # In production, a .ttf file must be provided.
        font_path = "app/static/fonts/Amiri-Regular.ttf"
        if os.path.exists(font_path):
            pdf.add_font("Amiri", "", font_path)
            pdf.set_font("Amiri", size=16)
        else:
            pdf.set_font("Arial", size=16) # Fallback (might not show Arabic correctly)

        # Title
        pdf.cell(200, 10, txt=bidi_title, ln=True, align='C')
        pdf.ln(10)
        
        # Content
        pdf.set_font_size(12)
        pdf.multi_cell(0, 10, txt=bidi_text, align='R')
        
        # Output to buffer
        pdf_output = io.BytesIO()
        pdf_content = pdf.output()
        pdf_output.write(pdf_content)
        pdf_output.seek(0)
        
        return StreamingResponse(
            pdf_output, 
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=transcript.pdf"}
        )
    except Exception as e:
        logger.error(f"PDF Export failed: {e}")
        return {"error": str(e)}

@router.post("/only-transcript", dependencies=[Depends(verify_phone_active)])
async def get_only_transcript(
    request: Request,
    audio_file: UploadFile = File(...),
    profile=Depends(check_quota)
):
    unique_id = str(uuid.uuid4())[:8]

    async def event_stream():
        audio_path = None
        msg_queue = asyncio.Queue()
        
        try:
            yield ":" + " " * 1024 + "\n\n"
            await msg_queue.put("🚀 بدأنا عملية معالجة لمّاح السريعة...")

            async def run_transcription():
                try:
                    await msg_queue.put("📥 جاري استلام وحفظ الملف في خوادم لمّاح...")
                    upload_dir = "uploads/transcripts"
                    os.makedirs(upload_dir, exist_ok=True)
                    path = os.path.join(upload_dir, f"{unique_id}_{audio_file.filename}")
                    
                    with open(path, "wb") as buffer:
                        shutil.copyfileobj(audio_file.file, buffer)

                    def sync_callback(msg):
                        loop.call_soon_threadsafe(msg_queue.put_nowait, msg)

                    result = await asyncio.to_thread(transcribe_audio, path, progress_callback=sync_callback)
                    
                    # Send metadata first (required by frontend)
                    await msg_queue.put({"type": "metadata", "transcript": result})
                    # Then send done
                    await msg_queue.put({"type": "done"})
                    return path
                except Exception as e:
                    await msg_queue.put({"type": "error", "message": str(e)})
                    return path

            loop = asyncio.get_event_loop()
            transcription_task = asyncio.create_task(run_transcription())

            while True:
                msg = await msg_queue.get()
                
                if isinstance(msg, dict):
                    if msg.get("type") == "metadata":
                        yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    elif msg.get("type") == "done":
                        yield f"data: {json.dumps({'type': 'done', 'message': '✅ اكتمل التفريغ!'}, ensure_ascii=False)}\n\n"
                        break
                    elif msg.get("type") == "error":
                        err_data = {"type": "error", "message": f"❌ خطأ: {msg['message']}"}
                        yield f"data: {json.dumps(err_data, ensure_ascii=False)}\n\n"
                        break
                else:
                    prog_data = {"type": "progress", "message": msg}
                    yield f"data: {json.dumps(prog_data, ensure_ascii=False)}\n\n"

            audio_path = await transcription_task

        except Exception as e:
            err_final = {"type": "error", "message": f"❌ عطل مفاجئ: {str(e)}"}
            yield f"data: {json.dumps(err_final, ensure_ascii=False)}\n\n"
        finally:
            if audio_path and os.path.exists(audio_path):
                try: os.remove(audio_path)
                except: pass

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-store",
        "Connection": "keep-alive",
        "Alt-Svc": "clear",
        "X-Content-Type-Options": "nosniff",
    })
