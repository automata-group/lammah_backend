import json
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.core.config import settings
from app.core.auth_guard import get_user_profile, verify_phone_active
from app.services.ai.rag import query_relevant_context
from app.services.ai.factory import get_llm_for_user
from app.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    history_id: int | str
    question: str
    chat_history: Optional[List[ChatMessage]] = []

@router.post("/", dependencies=[Depends(verify_phone_active)])
async def chat_with_lecture(request: ChatRequest, user: dict = Depends(get_user_profile)):
    """
    Takes a question about a specific lecture (history_id), retrieves the PDF context via RAG
    and the Audio transcript from Supabase, and queries the LLM with sliding window memory and plan limits.
    """
    try:
        supabase = get_supabase()
        is_demo_lecture = request.history_id == "4198557f-5b1b-48ac-91a8-d8129de8ca0b"
        
        if is_demo_lecture:
            log_result = supabase.table("usage_logs").select("*").eq("id", request.history_id).single().execute()
        else:
            log_result = supabase.table("usage_logs").select("*").eq("id", request.history_id).eq("user_id", user["id"]).single().execute()
        
        if not log_result.data:
            raise HTTPException(status_code=404, detail="Lecture not found or unauthorized")

        current_count = log_result.data.get("chat_count", 0) or 0

        # 1. Plan Verification & Limits
        if is_demo_lecture:
            plan_name = "شهري - برو"
            limit = 100
            total_used = 0
        else:
            from datetime import datetime, timezone
            sub_check = supabase.table("user_subscriptions") \
                .select("*, subscription_plans(*)") \
                .eq("user_id", user["id"]) \
                .eq("status", "active") \
                .gte("expires_at", datetime.now(timezone.utc).isoformat()) \
                .limit(1) \
                .execute()
            
            if not sub_check.data:
                raise HTTPException(status_code=403, detail={
                    "user_message": "هذه الميزة متاحة للمشتركين فقط. قم بالترقية للاستمتاع بالمحادثة الذكية مع محاضراتك!",
                    "type": "upgrade_required"
                })
                
            subscription = sub_check.data[0]
            plan_name = subscription["subscription_plans"]["name"]
            
            # Determine plan tier and message limit
            limit = 5  # Default/Free/Student
            if any(term in plan_name for term in ["الذهبية", "الفصل الدراسي", "برو", "VIP", "Elite", "Pro", "النخبة"]):
                limit = 100
            elif any(term in plan_name for term in ["الميدتيرم", "التفوق", "متقدم", "Advanced", "Premium"]):
                limit = 25
            elif any(term in plan_name for term in ["الإنقاذ", "الأساسية", "الاساسية", "أساسي", "اساسي", "Basic", "Essential", "Emergency"]):
                limit = 5
                
            # Calculate total chat count across all user's lectures
            total_chats = supabase.table("usage_logs").select("chat_count").eq("user_id", user["id"]).execute()
            total_used = sum(item.get("chat_count", 0) or 0 for item in total_chats.data) if total_chats.data else 0
            
            if total_used >= limit:
                raise HTTPException(status_code=403, detail={
                    "user_message": f"لقد وصلت للحد الأقصى الإجمالي للمحادثات المتاح لحسابك ({limit} رسالة) حسب باقتك ({plan_name}). يمكنك الترقية للحصول على حدود أعلى!",
                    "type": "limit_reached",
                    "limit": limit
                })

        # 2. Context Retrieval (RAG)
        metadata = log_result.data.get("metadata", {})
        transcript = metadata.get("transcript", "")
        document_id = metadata.get("document_id", None)
        
        if not transcript and not document_id:
            raise HTTPException(status_code=400, detail="Lecture data is missing (no transcript or pdf)")

        rag_context_snippets = []
        if document_id:
            # RAG to pull only relevant sections
            rag_context_snippets = query_relevant_context(request.question, n_results=4, document_id=document_id)
            
        rag_text = "\n".join(rag_context_snippets) if rag_context_snippets else "لا يوجد سياق إضافي من الشرائح."

        # 3. Sliding Window Memory (Last 4 turns = 8 messages)
        full_history = request.chat_history or []
        window_size = 8
        history_window = full_history[-window_size:] if len(full_history) > window_size else full_history

        # 4. System Prompt Optimization (Externalized to chat_agent.md)
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent.parent / "services" / "ai" / "prompts" / "chat_agent.md"
        
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_template = f.read()
            
        max_chars = 50000 if any(term in plan_name for term in ["متقدم", "VIP", "برو", "Advanced", "Pro"]) else 3000
        
        system_instruction = prompt_template.format(
            transcript=transcript[:max_chars],
            rag_text=rag_text
        )

        model = get_llm_for_user(user_id=user["id"], streaming=True)
        if not model:
            raise HTTPException(status_code=500, detail="AI Model not configured")

        # Build Messages
        messages = [SystemMessage(content=system_instruction)]
        for msg in history_window:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            else:
                messages.append(AIMessage(content=msg.content))
        
        messages.append(HumanMessage(content=request.question))

        async def generate():
            try:
                # Increment count in DB only for non-demo lectures
                if request.history_id != "4198557f-5b1b-48ac-91a8-d8129de8ca0b":
                    supabase.table("usage_logs").update({"chat_count": current_count + 1}).eq("id", request.history_id).execute()
                
                async for chunk in model.astream(messages):
                    if chunk.content:
                        yield chunk.content
            except Exception as e:
                logger.error(f"Chat error: {str(e)}")
                yield f"\n\n[خطأ: {str(e)}]"

        return StreamingResponse(generate(), media_type="text/plain")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat API error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
