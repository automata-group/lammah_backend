import json
from app.services.ai.agents.base_agent import BaseAgent

class ExaminerService(BaseAgent):
    def __init__(self):
        super().__init__("assessment_agent.md")

    async def process(self, llm, transcript_segment: str, explanations: list):
        input_data = (
            f"Detailed Explanations:\n{json.dumps(explanations, ensure_ascii=False)}\n\n"
            f"Transcript Segment:\n{transcript_segment}"
        )
        return await self.run_with_retry(llm, input_data)
