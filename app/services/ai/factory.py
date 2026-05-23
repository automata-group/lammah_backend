from typing import Optional
from langchain_openai import ChatOpenAI
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def get_llm_for_user(user_id: Optional[str] = None, streaming: bool = False):
    """
    Returns MiniMax M2.7 via OpenRouter for all users.
    Gemini and Groq fallbacks have been removed — MiniMax M2.7 is the only model.
    """
    if settings.OPENROUTER_API_KEY:
        logger.info(f"[LLM] Using MiniMax M2.7 via OpenRouter for user {user_id}")
        return ChatOpenAI(
            model="minimax/minimax-m2.7",
            openai_api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.2,
            streaming=streaming,
            request_timeout=90,
            max_retries=1,
            default_headers={
                "HTTP-Referer": "https://lammah.site",
                "X-Title": "Lammah Platform"
            }
        )

    logger.error("[LLM] OPENROUTER_API_KEY is not configured! Cannot return any LLM.")
    raise RuntimeError("OPENROUTER_API_KEY is not configured. Please set it in your .env file.")
