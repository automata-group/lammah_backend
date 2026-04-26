from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from app.core.config import settings
from app.services.subscription_service import get_user_subscription_info
import logging

logger = logging.getLogger(__name__)

def get_llm_for_user(user_id: Optional[str] = None, streaming: bool = False):
    """
    Returns the appropriate ChatModel instance based on the user's subscription.
    - Paid: Gemini via OpenRouter
    - Free: Llama 3 on Groq
    """
    
    # 1. Determine Plan (Default to free if no user_id)
    is_paid = False
    if user_id:
        try:
            sub_info = get_user_subscription_info(user_id)
            if sub_info.get("active_subscription"):
                is_paid = True
        except Exception as e:
            logger.error(f"Error checking subscription for LLM selection: {e}")
    
    # 2. Select Model and Provider
    if is_paid:
        # Paid Tier: OpenRouter (Gemini)
        if settings.OPENROUTER_API_KEY:
            logger.info(f"Using OpenRouter (MiniMax) for paid user {user_id}")
            return ChatOpenAI(
                model="minimax/minimax-m2.7", # Or user's preferred model
                openai_api_key=settings.OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
                temperature=0.3,
                streaming=streaming,
                default_headers={
                    "HTTP-Referer": "https://lammah.com", # Required by OpenRouter
                    "X-Title": "Lammah Platform"
                }
            )
        else:
            logger.warning("OPENROUTER_API_KEY missing. Falling back to Groq.")
            
    # Free Tier: OpenRouter (MiniMax M2.1)
    if settings.OPENROUTER_API_KEY:
        logger.info(f"Using OpenRouter (MiniMax M2.1) for free user {user_id}")
        return ChatOpenAI(
            model="minimax/minimax-m2.1",
            openai_api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.2,
            streaming=streaming,
            default_headers={
                "HTTP-Referer": "https://lammah.com",
                "X-Title": "Lammah Platform"
            }
        )
    elif settings.GROQ_API_KEY:
        logger.info(f"Using Groq (llama-3.1-8b-instant) for free user {user_id}")
        return ChatOpenAI(
            model="llama-3.1-8b-instant",
            openai_api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            temperature=0.2,
            streaming=streaming
        )

    # Fallback: Gemini
    if settings.GEMINI_API_KEY:
        logger.info("Falling back to Gemini")
        return ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0.2,
            streaming=streaming
        )

    logger.error("No AI Model API keys configured!")
    return None
