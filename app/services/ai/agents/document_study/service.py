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

    async def process_batch(self, llm, slides_batch: List[Dict[str, Any]], study_language: str = "ar") -> Dict[str, Any]:
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

        lang_directive = ""
        if study_language == "en":
            lang_directive = (
                "CRITICAL LANGUAGE DIRECTIVE: ENGLISH STUDY GUIDE MODE\n"
                "- The user has explicitly selected ENGLISH STUDY GUIDE mode (specifically for English literature, translation, linguistics, and international curricula).\n"
                "- Write student_explanation, title, summary_phrase, and exam_tip ENTIRELY in clear, accessible, and fluent Academic English.\n"
                "- Do NOT output Arabic prose for primary explanations.\n"
                "- For literature & texts (e.g. Robinson Crusoe, Shakespeare, poetry, linguistic theory): provide in-depth literary analysis (themes, symbolism, character analysis, rhetorical devices, and historical context).\n"
                "- In vocabulary: provide essential key terms with concise definitions.\n"
                "- In batch_mcqs & batch_flashcards: ALL questions, options, and explanations must be 100% in English.\n\n"
            )
        elif study_language == "bilingual":
            lang_directive = (
                "CRITICAL LANGUAGE DIRECTIVE: BILINGUAL STUDY MODE (DUAL ENGLISH & ARABIC)\n"
                "- In student_explanation: provide explanation in fluent academic English followed immediately by a concise Arabic conceptual translation and vocabulary anchor.\n"
                "- In exam_tip: provide the English exam trap along with its Arabic translation.\n"
                "- In batch_mcqs: English question and options with bilingual explanation.\n\n"
            )

        # OpenAI & Anthropic Prompt Caching Architecture:
        # Placing the static instructions first ensures the prompt prefix matches across all slide batches
        # of the document. Only the slide payload at the end is dynamic, enabling high-rate KV caching.
        input_data = (
            f"{lang_directive}تعليمات هامة:\n"
            f"1. التظليل الشامل والمفصل: يجب تظليل جميع المفاهيم، العناوين، القوانين، والمصطلحات الأساسية في كل شريحة (highlights) بالألوان المعتمدة (yellow, green, cyan, pink).\n"
            f"2. أسلوب الشرح في student_explanation: اشرح بأسلوب الزميل المتفوق الذكي والودود، واعتمد أسلوب 'النص المُطعّم بالإنجليزية الامتحانية' (Enriched Bilingual Injection) لدمج المصطلحات الإنجليزية الأساسية في قلب الشرح لترسيخها في ذهن الطالب للامتحان!\n"
            f"3. مفردات الشريحة في vocabulary.\n"
            f"4. فخاخ الامتحان في exam_tip.\n"
            f"5. أجب batch_mcqs: ولد من 5 إلى 8 أسئلة اختيار من متعدد (MCQs) تغطي كافة أفكار وتفاصيل الشرائح.\n"
            f"6. أجب batch_flashcards: ولد من 5 إلى 8 بطاقات استذكار سريع (Flashcards) للمفاهيم والتعاريف.\n"
            f"7. التزم بهيكل الـ JSON المطلوب بدقة.\n\n"
            f"=== حزمة الشرائح المستهدفة للدراسة والتخطيط (أرقام الشرائح: {batch_page_numbers}) ===\n"
            f"{slides_text}"
        )

        result = await self.run_with_retry(llm, input_data)
        if not isinstance(result, dict):
            logger.warning("[DOCUMENT_STUDY] Received non-dict response from LLM, returning empty fallback.")
            return {"slides": [], "batch_mcqs": [], "batch_flashcards": []}
            
        return result
