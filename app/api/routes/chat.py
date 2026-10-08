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
        
        if not log_result.data or not isinstance(log_result.data, dict):
            raise HTTPException(status_code=404, detail="Lecture not found or unauthorized")

        row_data: dict = log_result.data
        current_count = int(row_data.get("chat_count", 0) or 0)

        # 1. Plan Verification & Limits (Per-Lecture Limit)
        plan_name: str = "بدون اشتراك"
        if is_demo_lecture:
            plan_name = "محاضرة نموذجية"
            limit = 25
        else:
            from datetime import datetime, timezone
            sub_check = supabase.table("user_subscriptions") \
                .select("*, subscription_plans(*)") \
                .eq("user_id", user["id"]) \
                .eq("status", "active") \
                .gte("expires_at", datetime.now(timezone.utc).isoformat()) \
                .limit(1) \
                .execute()
            
            if sub_check.data and isinstance(sub_check.data, list) and len(sub_check.data) > 0:
                subscription = sub_check.data[0]
                sub_plans = subscription.get("subscription_plans") if isinstance(subscription, dict) else {}
                plan_name = str(sub_plans.get("name", "") if isinstance(sub_plans, dict) else "")
                
                # Determine plan tier and message limit per lecture
                if any(term in plan_name for term in ["الذهبية", "الفصل الدراسي", "برو", "VIP", "Elite", "Pro", "النخبة"]):
                    limit = 100  # باقة النخبة / الفصل الكامل: 100 سؤال لكل محاضرة
                elif any(term in plan_name for term in ["الميدتيرم", "التفوق", "متقدم", "Advanced", "Premium"]):
                    limit = 35   # باقة التفوق / الشهري: 35 سؤال لكل محاضرة
                else:
                    limit = 15   # باقة إنقاذ الكويزات / الأساسية: 15 سؤال لكل محاضرة
            else:
                # User without active subscription: limited sample questions
                plan_name = "بدون اشتراك"
                limit = 5
                
            # Check current lecture's chat count against plan limit
            if current_count >= limit:
                if not sub_check.data:
                    raise HTTPException(status_code=403, detail={
                        "user_message": f"لقد استهلكت رصيد الأسئلة المتاح لهذه المحاضرة ({limit} أسئلة). اشترك في إحدى باقات لمّاح لفتح حتى 100 سؤال لكل محاضرة ومناقشة كافة التفاصيل!",
                        "type": "upgrade_required",
                        "limit": limit
                    })
                else:
                    raise HTTPException(status_code=403, detail={
                        "user_message": f"لقد وصلت للحد الأقصى للأسئلة في هذه المحاضرة ({limit} سؤال) حسب باقتك ({plan_name}). يمكنك الترقية لباقة أعلى لزيادة الأسئلة، أو فتح محاضرة أخرى لمناقشتها!",
                        "type": "limit_reached",
                        "limit": limit
                    })

        # 2. Context Retrieval (Transcript, Slide Texts, Annotations, and RAG)
        import os
        metadata: dict = row_data.get("metadata") if isinstance(row_data.get("metadata"), dict) else {}
        transcript = str(metadata.get("transcript") or "").strip()
        slides_text = str(metadata.get("slides_text") or "").strip()
        document_id = metadata.get("document_id", None)
        annotations = metadata.get("annotations", {})

        # If slides_text is not stored directly, extract from annotations (slide_explanations, focus_points, vocabulary)
        if not slides_text and isinstance(annotations, dict):
            slide_parts = []
            explanations = annotations.get("slide_explanations", [])
            for s in explanations:
                if isinstance(s, dict):
                    p_num = s.get("page_number", "")
                    title = s.get("title", "")
                    expl = s.get("ai_explanation") or s.get("explanation") or s.get("student_explanation") or s.get("summary_phrase") or ""
                    example = s.get("example", "")
                    tip = s.get("exam_tip", "")
                    vocabs = s.get("vocabulary", [])
                    vocab_str = ", ".join([f"{v.get('term', '')}: {v.get('definition', '')}" for v in vocabs if isinstance(v, dict)]) if vocabs else ""

                    part = f"--- الشريحة {p_num}: {title} ---\n{expl}".strip()
                    if example:
                        part += f"\nمثال: {example}"
                    if tip:
                        part += f"\nنصيحة/فخ امتحاني: {tip}"
                    if vocab_str:
                        part += f"\nالمصطلحات: {vocab_str}"
                    if part:
                        slide_parts.append(part)

            focus_pts = annotations.get("focus_points", [])
            if focus_pts:
                pts_str = "\n".join([f"- ص {fp.get('page_number', '')}: {fp.get('point', '')} - {fp.get('explanation', '')}" for fp in focus_pts if isinstance(fp, dict)])
                if pts_str.strip():
                    slide_parts.append(f"--- أهم نقاط التركيز بالمادة ---\n{pts_str}")

            if slide_parts:
                slides_text = "\n\n".join(slide_parts)

        # Fallback: check if the PDF exists on disk in outputs/
        if not transcript and not slides_text and metadata.get("job_id"):
            job_id = metadata.get("job_id")
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            candidates = [
                os.path.join(base_dir, "outputs", f"{job_id}.pdf"),
                os.path.join(base_dir, "backend", "outputs", f"{job_id}.pdf"),
                f"/var/www/lammah-backend/outputs/{job_id}.pdf"
            ]
            for pdf_candidate in candidates:
                if os.path.exists(pdf_candidate):
                    try:
                        import fitz  # PyMuPDF
                        doc = fitz.open(pdf_candidate)
                        extracted = []
                        for i, page in enumerate(doc):
                            txt = page.get_text().strip()
                            if txt:
                                extracted.append(f"--- الشريحة {i+1} ---\n{txt}")
                        doc.close()
                        if extracted:
                            slides_text = "\n\n".join(extracted)
                            break
                    except Exception as e:
                        logger.warning(f"Failed to extract PDF text from {pdf_candidate}: {e}")

        # Ensure we have at least one knowledge source
        if not transcript and not slides_text and not document_id:
            raise HTTPException(status_code=400, detail="Lecture data is missing (no transcript or slides content available)")

        rag_context_snippets = []
        if document_id:
            try:
                rag_context_snippets = query_relevant_context(request.question, n_results=4, document_id=document_id)
            except Exception as e:
                logger.warning(f"RAG query error: {e}")

        rag_text = "\n".join(rag_context_snippets) if rag_context_snippets else ""

        # 3. Sliding Window Memory (Last 4 turns = 8 messages)
        full_history = request.chat_history or []
        window_size = 8
        history_window = full_history[-window_size:] if len(full_history) > window_size else full_history

        # 4. System Prompt Optimization (Externalized to chat_agent.md)
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent.parent / "services" / "ai" / "prompts" / "chat_agent.md"

        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_template = f.read()

        max_chars = 50000 if any(term in plan_name for term in ["متقدم", "VIP", "برو", "Advanced", "Pro", "النخبة", "الذهبية", "الفصل الدراسي", "Elite", "التفوق", "Premium"]) else 15000

        # Determine if lecture is document-only (slides only without audio)
        is_doc_only = bool(
            metadata.get("is_prep") is True
            or metadata.get("is_document_only") is True
            or metadata.get("has_audio") is False
            or not transcript.strip()
        )

        if is_doc_only:
            mode_directive = (
                "⚠️ [وضع دراسة السلايدات والمستندات - بدون صوت]:\n"
                "- تنبيه صارم وحاسم: هذه المادة تم رفعها كشرائح وسلايدات/مستند فقط ولا يوجد أي تسجيل صوتي أو شرح صوتي مرفق معها إطلاقاً.\n"
                "- يُمنع منعاً باتاً ذكر كلمات مثل: 'الدكتور'، 'شرح الدكتور'، 'ما قاله الدكتور'، 'أوضح الدكتور'، 'الأستاذ'، أو الإيحاء بأن هناك صوتاً تم الاستماع إليه.\n"
                "- انسب جميع إجاباتك وشروحاتك حصراً ومباشرة إلى 'الشرائح'، 'السلايدات'، أو 'المستند الدراسي'.\n"
                "- استخدم دائماً عبارات بديلة مثل: 'توضح الشريحة رقم 2...'، 'بحسب ما ورد في السلايدات...'، 'المذكور في هذا الجزء من السلايدات هو...'."
            )
        else:
            mode_directive = (
                "🎙️ [وضع المحاضرة الكاملة - تسجيل صوتي وسلايدات]:\n"
                "- تتضمن هذه المحاضرة تسجيلاً صوتياً لشرح الدكتور مدمجاً مع الشرائح والسلايدات.\n"
                "- يمكنك ربط كلام وشرح الدكتور بما هو مكتوب في الشرائح وتوضيح ما ركز عليه الدكتور في شرحه بدقة."
            )

        # Build combined context from transcript and/or slides
        context_parts = []
        if transcript:
            if is_doc_only:
                context_parts.append(f"### محتوى المادة المستخرج من السلايدات والمستند:\n{transcript[:max_chars]}")
            else:
                context_parts.append(f"### تفريغ كلام الدكتور في المحاضرة الصوتي:\n{transcript[:max_chars]}")
        if slides_text:
            context_parts.append(f"### محتوى وسلايدات المادة الدراسية (الشرائح والشروحات):\n{slides_text[:max_chars]}")

        combined_context = "\n\n".join(context_parts) if context_parts else (slides_text[:max_chars] or transcript[:max_chars])

        system_instruction = prompt_template.format(
            mode_directive=mode_directive,
            transcript=combined_context,
            rag_text=rag_text if rag_text else "اعتمد بدقة على نصوص وسلايدات المادة وشروحاتها المرفقة أعلاه للإجابة بأعلى دقة أكاديمية."
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
