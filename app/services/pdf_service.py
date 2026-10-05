import fitz  # PyMuPDF
import re
import base64
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

def _describe_sparse_visual_slide(page: fitz.Page) -> str:
    """Uses Gemini 2.5 Flash via OpenRouter to read text, diagrams, and flowcharts on visual slides."""
    if not settings.OPENROUTER_API_KEY:
        return ""
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        
        llm = ChatOpenAI(
            model="google/gemini-2.5-flash",
            openai_api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.1,
            request_timeout=35,
            max_retries=1
        )
        msg = HumanMessage(content=[
            {"type": "text", "text": "Extract all text, stages, phases, arrows, workflows, labels, and roles from this slide diagram. Return clean structured text:"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ])
        res = llm.invoke([msg])
        content = str(res.content).strip()
        logger.info(f"[PDF_SERVICE] Visual diagram transcribed ({len(content)} chars)")
        return content
    except Exception as e:
        logger.warning(f"[PDF_SERVICE] Vision extraction failed on slide: {e}")
        return ""

def extract_pages_from_pdf(file_path: str) -> list[dict]:
    """
    Extracts text page by page using PyMuPDF.
    For sparse visual/diagram slides, enriches text with visual transcription.
    Returns a list of dicts: [{"page_number": 1, "text": "..."}]
    """
    pages_data = []
    
    try:
        doc = fitz.open(file_path)
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text("text")
            
            # Clean up excessive newlines or weird spaces
            text = re.sub(r'\n+', '\n', text).strip()
            
            # If text is very sparse (< 60 chars) and page has images or diagrams, run vision transcription
            if len(text) < 60 and (len(page.get_images()) > 0 or len(page.get_drawings()) > 4):
                visual_text = _describe_sparse_visual_slide(page)
                if visual_text:
                    if text:
                        text = f"{text}\n\n[محتوى المخطط التوضيحي المرئي]:\n{visual_text}"
                    else:
                        text = f"[مخطط توضيحي مرئي]:\n{visual_text}"

            # NEVER drop a page to maintain exact 1-to-1 slide alignment with annotations
            pages_data.append({
                "page_number": page_num + 1,  # 1-indexed to match PDF slide numbers
                "text": text if text else "[شريحة مرئية / مخطط توضيحي]"
            })
        doc.close()
    except Exception as e:
        logger.error(f"Error extracting PDF: {e}")
        
    return pages_data
