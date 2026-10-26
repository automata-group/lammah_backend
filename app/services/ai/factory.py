from typing import Optional, List, Any
from langchain_openai import ChatOpenAI
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

_LLM_CACHE: dict[str, Any] = {}

def get_llm_for_user(user_id: Optional[str] = None, streaming: bool = False):
    """
    Returns an LLM client with automatic resilient fallbacks:
    1. Primary: Google Gemini direct via ChatGoogleGenerativeAI (if GEMINI_API_KEY configured)
       - 40% faster, lower cost, direct context caching
    2. Fallback 1: Google Gemini 2.5 Flash via OpenRouter (if OPENROUTER_API_KEY configured)
    3. Fallback 2: Groq LLaMA 3.3 70B via OpenAI-compatible endpoint (if GROQ_API_KEY configured)
    
    Caches LLM client instances to reuse underlying HTTP connection pools (eliminating TLS handshakes)
    per OpenAI & Anthropic latency optimization guidelines.
    """
    cache_key = f"streaming_{streaming}"
    if cache_key in _LLM_CACHE:
        return _LLM_CACHE[cache_key]

    primary_llm = None
    fallbacks: List[Any] = []

    # 1. Primary: Direct Google Gemini (أسرع بنسبة 40% وأقل تكلفة مع دعم مباشر للـ Context Caching)
    if settings.GEMINI_API_KEY:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            primary_llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=settings.GEMINI_API_KEY,
                temperature=0.2,
                streaming=streaming,
                max_retries=2
            )
        except Exception as e:
            logger.warning(f"[LLM] Failed to initialize direct Gemini: {e}")

    # 2. Fallback 1: OpenRouter (طبقة احتياطية أولى أو رئيسية في حال عدم توفر مفتاح Gemini المباشر)
    if settings.OPENROUTER_API_KEY:
        openrouter_llm = ChatOpenAI(
            model="google/gemini-2.5-flash",
            openai_api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.2,
            streaming=streaming,
            request_timeout=120,
            max_retries=2,
            default_headers={
                "HTTP-Referer": "https://lammah.site",
                "X-Title": "Lammah Platform"
            }
        )
        if primary_llm is None:
            primary_llm = openrouter_llm
        else:
            fallbacks.append(openrouter_llm)

    # 3. Fallback 2: Groq via OpenAI client
    if settings.GROQ_API_KEY:
        try:
            groq_llm = ChatOpenAI(
                model="llama-3.3-70b-versatile",
                openai_api_key=settings.GROQ_API_KEY,
                base_url="https://api.groq.com/openai/v1",
                temperature=0.2,
                streaming=streaming,
                request_timeout=120,
                max_retries=2
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
