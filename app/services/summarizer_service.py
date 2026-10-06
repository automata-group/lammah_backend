import os
import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from langchain_core.prompts import ChatPromptTemplate
from app.services.ai.factory import get_llm_for_user

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "ai" / "prompts"

def _load_prompt(filename: str) -> str:
    """Loads prompt content from a markdown file."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        logger.error(f"Prompt file not found: {path}")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, preserving sentence boundaries."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_period = max(truncated.rfind('.'), truncated.rfind('،'), truncated.rfind('\n'))
    if last_period > max_chars * 0.8:
        truncated = truncated[:last_period + 1]
    return truncated + "\n[... تم اختصار النص لأسباب تقنية ...]\n"

def _repair_json(j_str: str) -> str:
    """Aggressively repairs common LLM JSON formatting errors."""
    j_str = re.sub(r'("\s*:\s*[^"]*")\s*("\s*)', r'\1, \2', j_str)
    j_str = re.sub(r'}\s*\n*\s*{', '}, {', j_str)
    j_str = re.sub(r']\s*\n*\s*\[', '], [', j_str)
    j_str = re.sub(r',\s*([\]}])', r'\1', j_str)
    j_str = re.sub(r'([^\s,\[{])\s*\n*\s*"(?=[^"]*":)', r'\1, "', j_str)
    j_str = re.sub(r'[\x00-\x1F]+', ' ', j_str)
    return j_str

def _extract_json(content: str):
    """Safely extracts JSON object or array from LLM response."""
    # Find both possible start markers
    start_obj = content.find('{')
    start_list = content.find('[')
    
    # Determine which comes first
    if start_obj == -1 and start_list == -1:
        return {}
    
    if start_obj != -1 and (start_list == -1 or start_obj < start_list):
        start_idx = start_obj
        end_idx = content.rfind('}')
    else:
        start_idx = start_list
        end_idx = content.rfind(']')
    
    if end_idx == -1:
        return {}
    
    clean_json = content[start_idx:end_idx+1]
    try:
        return json.loads(_repair_json(clean_json), strict=False)
    except Exception as e:
        logger.error(f"Failed to parse JSON: {e}")
        return {}

async def generate_smart_annotations(transcript: str, pdf_context: List[str], user_id: str = None) -> Dict[str, Any]:
    """
    High-level entry point that calls the AI Orchestrator micro-services.
    """
    from app.services.ai.orchestrator import AIOrchestrator
    
    llm = get_llm_for_user(user_id)
    if not llm:
        return {"highlights": [], "sticky_notes": []}

    # Context Truncation Logic (Remains here as a guard: 25k chars ~ 20 min speech, 20k chars ~ 20 slides)
    MAX_TRANSCRIPT_CHARS = 25000
    MAX_PDF_CONTEXT_CHARS = 20000
    
    from app.services.subscription_service import get_user_subscription_info
    try:
        sub_info = get_user_subscription_info(user_id)
        if sub_info.get("active_subscription"):
            MAX_TRANSCRIPT_CHARS = 250000
            MAX_PDF_CONTEXT_CHARS = 150000
    except Exception: pass

    transcript_truncated = _truncate_text(transcript, MAX_TRANSCRIPT_CHARS)
    
    # Truncate PDF context too (was missing - caused token overflow for free users)
    pdf_text_joined = "\n".join(pdf_context)
    pdf_text_truncated = _truncate_text(pdf_text_joined, MAX_PDF_CONTEXT_CHARS)
    truncated_pdf_context = [pdf_text_truncated] if pdf_text_truncated else []
    
    orchestrator = AIOrchestrator()
    return await orchestrator.generate_complete_annotations(llm, transcript_truncated, truncated_pdf_context)

def extract_contextual_glossary(transcript: str, user_id: str = None) -> dict:
    """Legacy/Helper for specific glossary extraction."""
    llm = get_llm_for_user(user_id)
    if not llm: return {}
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Extract difficult English terms and provide contextual Arabic explanations. Return ONLY JSON: {\"term\": \"explanation\"}"),
        ("human", "Transcript:\n{transcript}")
    ])
    try:
        result = (prompt | llm).invoke({"transcript": transcript})
        return _extract_json(result.content)
    except: return {}

def detect_emphasis(transcript: str, user_id: str = None) -> list:
    """Legacy/Helper for specific emphasis detection."""
    llm = get_llm_for_user(user_id)
    if not llm: return []
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Find emphasized phrases. Return ONLY JSON: [{\"text\": \"...\", \"reason\": \"...\"}]"),
        ("human", "{transcript}")
    ])
    try:
        result = (prompt | llm).invoke({"transcript": transcript})
        data = _extract_json(result.content)
        return data if isinstance(data, list) else []
    except: return []
