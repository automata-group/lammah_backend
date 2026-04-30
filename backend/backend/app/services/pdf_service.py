import fitz  # PyMuPDF
import re

def extract_pages_from_pdf(file_path: str) -> list[dict]:
    """
    Extracts text page by page using PyMuPDF.
    Returns a list of dicts: [{"page_number": 0, "text": "..."}]
    """
    pages_data = []
    
    try:
        doc = fitz.open(file_path)
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text("text")
            
            # Clean up excessive newlines or weird spaces
            text = re.sub(r'\n+', '\n', text).strip()
            
            if text:
                pages_data.append({
                    "page_number": page_num, # 0-indexed
                    "text": text
                })
        doc.close()
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        
    return pages_data
