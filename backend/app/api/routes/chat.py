import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import google.generativeai as genai
from app.core.config import settings
from app.core.auth_guard import get_user_profile
from app.services.rag_service import query_relevant_context
from app.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    history_id: int | str
    question: str
    chat_history: Optional[List[ChatMessage]] = []

@router.post("/")
async def chat_with_lecture(request: ChatRequest, user: dict = Depends(get_user_profile)):
    """
    Takes a question about a specific lecture (history_id), retrieves the PDF context via RAG
    and the Audio transcript from Supabase, and queries Gemini 2.5 Flash.
    """
    try:
        supabase = get_supabase()
        log_result = supabase.table("usage_logs").select("*").eq("id", request.history_id).eq("user_id", user["id"]).single().execute()
        
        if not log_result.data:
            raise HTTPException(status_code=404, detail="Lecture not found or unauthorized")
            
        metadata = log_result.data.get("metadata", {})
        transcript = metadata.get("transcript", "")
        document_id = metadata.get("document_id", None)
        
        if not transcript and not document_id:
            raise HTTPException(status_code=400, detail="Lecture data is missing (no transcript or pdf)")

        # Retrieve Context from ChromaDB
        rag_context_snippets = []
        if document_id:
            rag_context_snippets = query_relevant_context(request.question, n_results=4, document_id=document_id)
            
        rag_text = "\n".join(rag_context_snippets) if rag_context_snippets else "لا يوجد سياق من الشرائح المرئية."

        # Build System Instruction
        system_instruction = (
            "أنت مساعد دراسي ذكي وخبير في الشرح الأكاديمي. مهمتك الإجابة على أسئلة الطالب بدقة وتوسع وتوضيح.\n"
            f"--- النص الصوتي (تفريغ الدكتور) ---\n{transcript}\n"
            f"--- محتوى الشرائح ذي الصلة المباشرة ---\n{rag_text}\n"
            "---\n"
            "التعليمات:\n"
            "- أجب باللغة العربية الواضحة والمبسطة وبأسلوب أكاديمي مشجع.\n"
            "- إجابتك ليست محصورة في المحاضرة فقط؛ يحق لك الإجابة على أي سؤال بناءً على معرفتك الشاملة خارج المنهج إذا لزم الأمر.\n"
            "- إذا كان السؤال يتعلق بجزئية تم شرحها في المحاضرة، اشرحها من عندك أولاً وبشكل وافٍ، ثم أضف فقرة تقول: 'وقد ذكر الدكتور في المحاضرة بخصوص هذه الجزئية كذا وكذا...'.\n"
            "- كن مباشراً ومنظّماً واستخدم النقاط (Bullet points) لتسهيل الفهم.\n"
        )

        model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system_instruction)

        # Build Conversation History List
        gemini_history = []
        for msg in (request.chat_history or []):
            # map 'user' or 'model' to gemini format
            gemini_role = "user" if msg.role == "user" else "model"
            gemini_history.append({"role": gemini_role, "parts": [msg.content]})

        gemini_history.append({"role": "user", "parts": [request.question]})

        def generate():
            try:
                response = model.generate_content(
                    contents=gemini_history,
                    stream=True,
                    generation_config=genai.types.GenerationConfig(temperature=0.3)
                )
                for chunk in response:
                    if chunk.text:
                        yield chunk.text
            except Exception as e:
                logger.error(f"Chat generation stream error: {str(e)}", exc_info=True)
                yield f"\n\n[عذراً، حدث خطأ أثناء توليد الرد: {str(e)}]"

        return StreamingResponse(generate(), media_type="text/plain")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat API error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
