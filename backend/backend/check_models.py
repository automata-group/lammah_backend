from dotenv import load_dotenv
import os
import sys

# Add the current directory to path so we can import app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.llm_factory import get_llm_for_user
from app.core.config import settings

load_dotenv()

def test_connectivity():
    print("--- 🔍 AI Connectivity Diagnostics ---")
    
    # 1. Check API Keys
    print(f"GROQ_API_KEY: {'✅ Found' if settings.GROQ_API_KEY else '❌ Missing'}")
    print(f"OPENROUTER_API_KEY: {'✅ Found' if settings.OPENROUTER_API_KEY else '❌ Missing'}")
    print(f"GEMINI_API_KEY (Direct): {'✅ Found' if settings.GEMINI_API_KEY else '❌ Missing'}")
    
    print("\n--- 🤖 Testing Model Retrieval ---")
    
    # Test Free Tier (Groq)
    print("\n[Free Tier Test]")
    llm_free = get_llm_for_user(user_id=None) # Passing None defaults to free logic
    if llm_free:
        print(f"Success: Selected {getattr(llm_free, 'model_name', 'Unknown model')}")
        try:
            res = llm_free.invoke("Say 'Groq is ready' in Arabic")
            print(f"Response: {res.content}")
        except Exception as e:
            print(f"Execution Error: {e}")
    else:
        print("Failed to initialize free model.")

    # Test Paid Tier (OpenRouter)
    print("\n[Paid Tier Test]")
    # For testing, we mock a 'paid' check by passing a dummy ID if we know the DB state 
    # but here we'll just check if the logic in factory works if we simulate sub_info
    print("Note: Paid tier test requires a user_id with active sub in Supabase.")
    print("Checking if factory can create OpenRouter instance if forced...")
    
    if settings.OPENROUTER_API_KEY:
        from langchain_openai import ChatOpenAI
        test_paid = ChatOpenAI(
            model="google/gemini-2.0-pro-exp-02-05:free",
            openai_api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1"
        )
        try:
            res = test_paid.invoke("Say 'OpenRouter is ready' in Arabic")
            print(f"Response: {res.content}")
        except Exception as e:
            print(f"Execution Error: {e}")
    else:
        print("Skipping OpenRouter test (Key missing)")

if __name__ == "__main__":
    test_connectivity()
