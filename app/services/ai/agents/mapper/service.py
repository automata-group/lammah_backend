from app.services.ai.agents.base_agent import BaseAgent

class MapperService(BaseAgent):
    def __init__(self):
        super().__init__("annotation_agent.md")

    async def process(self, llm, transcript: str, pdf_text: str):
        input_data = f"PDF Context:\n{pdf_text}\n\nTranscript:\n{transcript}"
        return await self.run_with_retry(llm, input_data)
