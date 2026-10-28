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


def slice_pdf_pages(input_pdf_path: str, output_pdf_path: str, start_page: int | None = None, end_page: int | None = None) -> str:
    """
    Slices a PDF file to keep only pages between start_page and end_page (1-indexed inclusive).
    If start_page and end_page are not specified, returns input_pdf_path unmodified.
    Works for converted PPTX (where 1 slide = 1 page) and native PDFs.
    """
    if not start_page and not end_page:
        return input_pdf_path
        
    try:
        import fitz
        doc = fitz.open(input_pdf_path)
        total_pages = len(doc)
        if total_pages == 0:
            doc.close()
            return input_pdf_path
            
        s = int(start_page) if start_page and int(start_page) > 0 else 1
        e = int(end_page) if end_page and int(end_page) > 0 else total_pages
        
        # If user entered start > end, swap them gracefully
        if s > e:
            s, e = e, s
            
        # Clamp between 1 and total_pages
        s = max(1, min(s, total_pages))
        e = max(s, min(e, total_pages))
        
        # If the range covers all pages, no slicing needed
        if s == 1 and e == total_pages:
            doc.close()
            return input_pdf_path
            
        # 1-indexed to 0-indexed for PyMuPDF
        from_idx = s - 1
        to_idx = e - 1
        
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=from_idx, to_page=to_idx)
        new_doc.save(output_pdf_path)
        new_doc.close()
        doc.close()
        logger.info(f"✂️ [SLICE_PDF] Sliced PDF from {total_pages} pages down to {e - s + 1} pages (p.{s} to p.{e}): {output_pdf_path}")
        return output_pdf_path
    except Exception as err:
        logger.error(f"❌ [SLICE_PDF ERROR] Failed to slice PDF: {err}")
        return input_pdf_path
