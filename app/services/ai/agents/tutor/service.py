import json
from app.services.ai.agents.base_agent import BaseAgent

class TutorService(BaseAgent):
    def __init__(self):
        super().__init__("tutor_agent.md")

    async def process(self, llm, transcript: str, slides_batch: list, mapping_context: str):
        slides_text = ""
        for s in slides_batch:
            slides_text += f"\n--- Slide {s['page_number']} ---\n{s['text']}\n"

        batch_page_numbers = [s['page_number'] for s in slides_batch]

        # OpenAI & Anthropic Prompt Caching Architecture:
        # Placing the stable shared context (Transcript & Instructions) FIRST produces an identical prefix
        # across all slide batches of this lecture. Batches 2..N hit the KV cache, reducing TTFT by up to 80%
        # and slashing input token costs by 75-90%. Dynamic per-batch slide data is placed at the end.
        input_data = (
            f"=== Full Lecture Transcript (Reference Material) ===\n{transcript}\n\n"
            f"=== Instructions & Dialect Rules ===\n"
            f"- For each requested slide, find transcript segments where the professor discusses concepts FROM THAT SLIDE's text.\n"
            f"- Match by keywords/concepts — do NOT assign content sequentially.\n"
            f"- If a slide's concepts are NOT in the transcript at all → write exactly: (الدكتور ما ذكر هالشريحة بالتفصيل.)\n"
            f"- In ai_explanation: use friendly Palestinian student dialect (لهجة فلسطينية جامعية ودودة - ممنوع أي لهجة خليجية أو مفردات مثل هذي/وش/الدافور) and apply 'Enriched Bilingual Injection' — embed the exact English slide keywords inside the explanation (e.g. هاي الشريحة بتدخلنا في أول وأهم مرحلة وهي الـ Requirements (المتطلبات), Shared View, Textual format, User stories) so the student's eyes lock onto exam choices.\n"
            f"- Return JSON with slide_explanations for ALL slides in the batch.\n\n"
            f"=== Target PDF Slide Batch to Process (Slides: {batch_page_numbers}) ===\n{slides_text}\n\n"
            f"=== Mapping Context (Hints for this batch) ===\n{mapping_context}"
        )
        return await self.run_with_retry(llm, input_data)
