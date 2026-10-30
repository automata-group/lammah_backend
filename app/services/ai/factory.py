from typing import Optional, List, Any
from langchain_openai import ChatOpenAI
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

_LLM_CACHE: dict[str, Any] = {}

def get_llm_for_user(user_id: Optional[str] = None, streaming: bool = False):
    """
    Returns an LLM client with automatic resilient fallbacks:
    1. Primary: OpenRouter (Google Gemini 2.5 Flash) - Highly available, production-grade, no free-tier caps
    2. Fallback 1: Direct Google Gemini (with max_retries=0 to failover instantly without 4-minute retry loops)
    3. Fallback 2: Groq LLaMA 3.3 70B via OpenAI-compatible endpoint
    """
    cache_key = f"streaming_{streaming}"
    if cache_key in _LLM_CACHE:
        return _LLM_CACHE[cache_key]

    primary_llm = None
    fallbacks: List[Any] = []

    # 1. Primary: OpenRouter (Gemini 2.5 Flash)
    if settings.OPENROUTER_API_KEY:
        try:
            openrouter_llm = ChatOpenAI(
                model="google/gemini-2.5-flash",
                openai_api_key=settings.OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
                temperature=0.2,
                streaming=streaming,
                request_timeout=60,
                max_retries=1,
                default_headers={
                    "HTTP-Referer": "https://lammah.site",
                    "X-Title": "Lammah Platform"
                }
            )
            primary_llm = openrouter_llm
        except Exception as e:
            logger.warning(f"[LLM] Failed to initialize OpenRouter: {e}")

    # 2. Direct Google Gemini (with max_retries=0 so it fails over instantly on 429 quota exhaustion)
    if settings.GEMINI_API_KEY:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            gemini_llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=settings.GEMINI_API_KEY,
                temperature=0.2,
                streaming=streaming,
                max_retries=0
            )
            if primary_llm is None:
                primary_llm = gemini_llm
            else:
                fallbacks.append(gemini_llm)
        except Exception as e:
            logger.warning(f"[LLM] Failed to initialize direct Gemini: {e}")

    # 3. Fallback 2: Groq LLaMA 3.3 70B
    if settings.GROQ_API_KEY:
        try:
            groq_llm = ChatOpenAI(
                model="llama-3.3-70b-versatile",
                openai_api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
                temperature=0.2,
                streaming=streaming,
                request_timeout=60,
                max_retries=1
            )
            if primary_llm is None:
                primary_llm = groq_llm
            else:
                fallbacks.append(groq_llm)
        except Exception as e:
            logger.warning(f"[LLM] Failed to initialize Groq fallback: {e}")

    if primary_llm is None:
        logger.error("[LLM] No AI API keys configured (GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY)!")
        raise RuntimeError("No AI API keys configured. Please configure GEMINI_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY.")

    final_llm = primary_llm.with_fallbacks(fallbacks) if fallbacks else primary_llm
    _LLM_CACHE[cache_key] = final_llm

    if fallbacks:
        logger.info(f"[LLM] Returning primary LLM with {len(fallbacks)} configured fallback provider(s) for user {user_id}")
    else:
        logger.info(f"[LLM] Returning primary LLM for user {user_id}")
    return final_llm
