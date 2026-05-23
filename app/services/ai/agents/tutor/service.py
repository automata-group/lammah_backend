import json
from app.services.ai.agents.base_agent import BaseAgent

class TutorService(BaseAgent):
    def __init__(self):
        super().__init__("tutor_agent.md")

    async def process(self, llm, transcript: str, slides_batch: list, mapping_context: str):
        slides_text = ""
        for s in slides_batch:
            slides_text += f"\n--- Slide {s['page_number']} ---\n{s['text']}\n"

        input_data = (
            f"=== PDF Slide Batch (process EACH slide below) ===\n{slides_text}\n\n"
            f"=== Full Lecture Transcript (search this for content matching each slide) ===\n{transcript}\n\n"
            f"=== Mapping Context (hints about what professor emphasized) ===\n{mapping_context}\n\n"
            f"INSTRUCTIONS:\n"
            f"- For each slide in {[s['page_number'] for s in slides_batch]}, find transcript segments where the professor discusses concepts FROM THAT SLIDE's text.\n"
            f"- Match by keywords/concepts — do NOT assign content sequentially.\n"
            f"- If a slide's concepts are NOT in the transcript at all → write exactly: (الدكتور ما ذكر هالشريحة بالتفصيل.)\n"
            f"- Return JSON with slide_explanations for ALL slides in the batch."
        )
        return await self.run_with_retry(llm, input_data)
