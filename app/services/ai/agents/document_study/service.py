import json
import logging
from typing import Dict, Any, List
from app.services.ai.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class DocumentStudyService(BaseAgent):
    """
    Dedicated agent for Self-Study & Document Annotation mode.
    Handles PDF/PPTX uploads without audio transcripts.
    Batches slides efficiently to minimize token consumption and API calls,
    while generating multi-color highlights, vocabulary translations,
    friendly colloquial Arabic explanations, exam tips, and study assessments in a single pass.
    """
    def __init__(self):
        super().__init__("document_study_agent.md")

    async def process_batch(self, llm, slides_batch: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Processes a batch of slides (typically 4-6 slides).
        Returns structured annotations including multi-color highlights,
        explanations, vocabulary, exam tips, MCQs, and flashcards.
        """
        slides_text = ""
        for s in slides_batch:
            page_num = s.get("page_number", 1)
            raw_text = s.get("text", "").strip()
            # Trim excessive whitespace while keeping all meaningful text
            cleaned_text = "\n".join(line.strip() for line in raw_text.splitlines() if line.strip())
            slides_text += f"\n--- الشريحة {page_num} ---\n{cleaned_text}\n"

        batch_page_numbers = [s.get('page_number') for s in slides_batch]

        # OpenAI & Anthropic Prompt Caching Architecture:
        # Placing the static instructions first ensures the prompt prefix matches across all slide batches
        # of the document. Only the slide payload at the end is dynamic, enabling high-rate KV caching.
        input_data = (
            f"تعليمات التنفيذ الذكي:\n"
            f"1. لكل شريحة في الحزمة، حدد عبارات من نص الشريحة حرفياً لتظليلها (highlights) بألوان (yellow, green, cyan, pink).\n"
            f"2. أسلوب الشرح الإلزامي في student_explanation: اشرح حصراً باللهجة الفلسطينية الجامعية الودودة والمحبوبة (مثل: 'يا بطل، هاي الشريحة بتدخلنا في أول وأهم مرحلة وهي الـ Requirements (المتطلبات)... عشان نضمن... زي ما إنت شايف... شو الفكرة هون؟' - ويمنع منعاً باتاً استخدام أي لهجة خليجية أو مفردات مثل 'هذي' أو 'وش' أو 'الدافور')، مع التطبيق الإلزامي لأسلوب 'النص المُطعّم بالإنجليزية الامتحانية' (Enriched Bilingual Injection) بحيث يدخل المصطلح الإنجليزي الحرفي من السلايد في قلب كل جملة (مثل: مرحلة الـ Requirements (المتطلبات)، نوصل لـ Shared View (رؤية مشتركة)، بصيغة Textual format زي الـ User stories، بصيغة Diagrammatic زي الـ Use case diagrams) لكي تألف عين الطالب خيارات الامتحان الإنجليزية فوراً!\n"
            f"3. استخرج وترجم كل المصطلحات الإنجليزية وضعها في قائمة vocabulary.\n"
            f"4. حدد فخ الامتحان وسؤال متوقع لكل شريحة في exam_tip بالصيغة الثنائية.\n"
            f"5. أرجع كائن JSON حصراً حسب الصيغة المطلوبة.\n\n"
            f"=== حزمة الشرائح للتحليل والتخطيط والشرح الذكي (أرقام الشرائح: {batch_page_numbers}) ===\n"
            f"{slides_text}"
        )

        result = await self.run_with_retry(llm, input_data)
        if not isinstance(result, dict):
            logger.warning("[DOCUMENT_STUDY] Received non-dict response from LLM, returning empty fallback.")
            return {"slides": [], "batch_mcqs": [], "batch_flashcards": []}
            
        return result
