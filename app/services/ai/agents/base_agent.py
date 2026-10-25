import json
import logging
import re
import asyncio
from typing import Dict, Any, Union, List
from langchain_core.prompts import ChatPromptTemplate
from pathlib import Path
from json_repair import repair_json

logger = logging.getLogger(__name__)

class BaseAgent:
    def __init__(self, prompt_filename: str):
        self.prompt_path = Path(__file__).parent.parent / "prompts" / prompt_filename
        self.prompt_template = self._load_prompt()
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", self.prompt_template),
            ("human", "{input}")
        ])

    def _load_prompt(self) -> str:
        if not self.prompt_path.exists():
            logger.error(f"Prompt file not found: {self.prompt_path}")
            return ""
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
            # Escape curly braces so Langchain ChatPromptTemplate doesn't confuse JSON examples with variables
            return template.replace("{", "{{").replace("}", "}}")

    def _sanitize_html_in_json(self, j_str: str) -> str:
        """Fix all malformed <span style=...> variants and unescaped quotes in JSON strings."""
        # Normalize background-color styles with optional escaping
        j_str = re.sub(
            r'style=\\?"background-color(?::\s*#[0-9A-Fa-f]+)?\\?"',
            r'style=\\"background-color: #FFFF00\\"',
            j_str
        )
        # Fix unescaped HTML attribute quotes: attr="val" -> attr=\"val\"
        j_str = re.sub(
            r'(?<!\\)="([^"]*)"((?=>|\s|/))',
            lambda m: '=\\"' + m.group(1) + '\\"' + m.group(2),
            j_str
        )
        return j_str
    
    def _extract_json(self, content: str) -> Union[Dict, List, None]:
        """Extracts JSON with maximum resilience using json-repair."""
        try:
            # 1. Sanitize malformed HTML span tags (which json-repair might treat as valid strings but we want formatting)
            content = self._sanitize_html_in_json(content)
            
            # 2. Use industry-standard json-repair to handle unescaped quotes, extra data, missing brackets, etc.
            extracted = repair_json(content, return_objects=True)
            
            if extracted:
                return extracted
            return None
        except Exception as e:
            logger.error(f"JSON Parsing Error (json-repair): {e} | Content Snippet: {content[:100]}")
            return None

    async def run_with_retry(self, llm, input_data: str, retries: int = 2) -> Union[Dict, List, Any]:
        """Runs the agent with an exponential backoff retry logic."""
        chain = self.prompt | llm
        
        last_error = None
        for attempt in range(retries + 1):
            try:
                # Enforce a 180-second timeout per LLM request (handles large batches safely)
                response = await asyncio.wait_for(chain.ainvoke({"input": input_data}), timeout=180.0)
                extracted = self._extract_json(response.content)
                if extracted is not None:
                    logger.info(f"Successfully extracted JSON keys: {list(extracted.keys()) if isinstance(extracted, dict) else type(extracted)}")
                    return extracted
                logger.warning(f"Attempt {attempt+1}: Failed to extract valid JSON.")
            except asyncio.TimeoutError:
                logger.error(f"Attempt {attempt+1}: LLM invocation timed out after 180s")
                await asyncio.sleep(1 * (attempt + 1))
            except Exception as e:
                last_error = e
                err_str = str(e)
                is_rate_limited = "429" in err_str or "rate" in err_str.lower()
                sleep_time = (4 * (attempt + 1)) if is_rate_limited else (1 * (attempt + 1))
                if is_rate_limited:
                    logger.warning(f"⚠️ [RATE_LIMIT] 429 detected on attempt {attempt+1}. Backing off for {sleep_time}s...")
                else:
                    logger.error(f"Attempt {attempt+1}: LLM invocation failed: {type(e).__name__}: {e}")
                await asyncio.sleep(sleep_time)
        
        logger.error(f"All {retries+1} attempts failed. Last error: {type(last_error).__name__ if last_error else 'None'}: {last_error}. Returning empty state.")
        return {}
