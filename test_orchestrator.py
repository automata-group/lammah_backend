import asyncio
import os
import sys

# Get the absolute path of the directory containing this script
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from app.core.config import settings
from app.services.ai.orchestrator import AIOrchestrator
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_orchestrator():
    # Use Gemini 
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0.2
    )

    transcript = "مرحبا بكم في هذه المحاضرة. اليوم سنتحدث عن الذكاء الاصطناعي. ركزوا هنا لأن هذا مهم جدا للامتحان."
    pdf_context = ["الذكاء الاصطناعي هو تقنية حديثة.", "مكونات الذكاء الاصطناعي: تعلم الآلة والشبكات العصبية."]

    orchestrator = AIOrchestrator()
    print("Starting Orchestrator Test...")
    results = await orchestrator.generate_complete_annotations(llm, transcript, pdf_context)
    
    print("\n\n--- RESULTS ---")
    print(f"Highlights: {len(results.get('highlights', []))}")
    print(f"Sticky Notes: {len(results.get('sticky_notes', []))}")
    print(f"Focus Points: {len(results.get('focus_points', []))}")
    print(f"Slide Explanations: {len(results.get('slide_explanations', []))}")
    print(f"MCQs: {len(results.get('mcqs', []))}")
    print(f"Flashcards: {len(results.get('flashcards', []))}")

if __name__ == "__main__":
    asyncio.run(test_orchestrator())
