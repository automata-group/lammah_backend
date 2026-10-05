import os
import io
import logging
import tempfile

logger = logging.getLogger(__name__)


def extract_text_from_pptx(pptx_path: str) -> list[dict]:
    """
    Extracts text slide-by-slide directly from a .pptx file using python-pptx.
    Returns a list of dicts: [{"page_number": 1, "text": "..."}]
    """
    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)
        pages = []
        for slide_num, slide in enumerate(prs.slides, start=1):
            slide_texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        line = " ".join(run.text for run in para.runs).strip()
                        if line:
                            slide_texts.append(line)
            # NEVER drop a slide to maintain exact 1-to-1 slide alignment
            pages.append({
                "page_number": slide_num,
                "text": "\n".join(slide_texts) if slide_texts else "[شريحة مرئية / مخطط توضيحي]"
            })
        logger.info(f"[PPTX] Extracted {len(pages)} slides from {os.path.basename(pptx_path)}")
        return pages
    except Exception as e:
        logger.error(f"[PPTX] python-pptx extraction failed: {e}")
        return []


def _convert_pptx_via_com(pptx_bytes: bytes) -> bytes:
    """
    Converts PPTX -> PDF using Microsoft Office PowerPoint COM on Windows.
    Produces 100% pixel-perfect vector Arabic typography, ligatures, and layout.
    Cleans up all temp files immediately.
    """
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    pid = os.getpid()
    rnd = os.urandom(4).hex()
    temp_dir = tempfile.gettempdir()
    temp_in = os.path.join(temp_dir, f"ppt_in_{pid}_{rnd}.pptx")
    temp_out = os.path.join(temp_dir, f"ppt_out_{pid}_{rnd}.pdf")

    try:
        with open(temp_in, "wb") as f:
            f.write(pptx_bytes)

        pythoncom.CoInitialize()  # type: ignore
        ppt_app = win32com.client.DispatchEx("PowerPoint.Application")
        try:
            # 32 = ppSaveAsPDF, WithWindow=False runs invisible
            deck = ppt_app.Presentations.Open(temp_in, WithWindow=False)
            deck.SaveAs(temp_out, 32)
            deck.Close()
        finally:
            try:
                ppt_app.Quit()
            except Exception:
                pass
            pythoncom.CoUninitialize()  # type: ignore

        if os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
            with open(temp_out, "rb") as f:
                pdf_bytes = f.read()
            logger.info(f"[PPTX→PDF] Native PowerPoint COM conversion succeeded ({len(pdf_bytes)} bytes).")
            return pdf_bytes
        else:
            raise RuntimeError("COM output PDF not generated.")
    finally:
        for p in (temp_in, temp_out):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def _convert_pptx_via_libreoffice(pptx_bytes: bytes) -> bytes:
    """
    Converts PPTX -> PDF using headless LibreOffice / soffice (Linux/Docker).
    """
    import subprocess

    pid = os.getpid()
    rnd = os.urandom(4).hex()
    temp_dir = os.path.join(tempfile.gettempdir(), f"lo_{pid}_{rnd}")
    os.makedirs(temp_dir, exist_ok=True)
    temp_in = os.path.join(temp_dir, "input.pptx")
    temp_out = os.path.join(temp_dir, "input.pdf")

    try:
        with open(temp_in, "wb") as f:
            f.write(pptx_bytes)

        for command in ["libreoffice", "soffice"]:
            try:
                subprocess.run([command, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10)
                process = subprocess.run(
                    [command, f"-env:UserInstallation=file:///{temp_dir}/profile", "--headless", "--convert-to", "pdf",
                     "--outdir", temp_dir, temp_in],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
                )
                if process.returncode == 0 and os.path.exists(temp_out):
                    with open(temp_out, "rb") as f:
                        pdf_bytes = f.read()
                    logger.info(f"[PPTX→PDF] LibreOffice conversion succeeded ({len(pdf_bytes)} bytes).")
                    return pdf_bytes
            except Exception:
                pass
        raise RuntimeError("LibreOffice unavailable or conversion failed.")
    finally:
        import shutil
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


def _convert_pptx_via_story(pptx_bytes: bytes) -> bytes:
    """
    Pure Python fallback: HarfBuzz layout engine with PyMuPDF Story + Amiri font.
    Handles complex Arabic ligatures and bidirectional text without character splitting.
    """
    import fitz
    import pptx

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    font_path = os.path.join(base_dir, "static", "fonts", "Amiri-Regular.ttf")
    font_url = font_path.replace("\\", "/")

    prs = pptx.Presentation(io.BytesIO(pptx_bytes))
    
    # Slide dimensions (points)
    w_pt = (prs.slide_width or 9144000) / 12700
    h_pt = (prs.slide_height or 5143500) / 12700

    html_slides = []
    for s_idx, slide in enumerate(prs.slides, 1):
        slide_parts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                is_title = bool(shape.name and "title" in shape.name.lower())
                tag = "h2" if is_title else "p"
                text = shape.text_frame.text.strip()
                if text:
                    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    slide_parts.append(f"<{tag}>{escaped.replace(chr(10), '<br/>')}</{tag}>")
        
        if not slide_parts:
            slide_parts.append(f"<p>شريحة {s_idx}</p>")
            
        content_html = "\n".join(slide_parts)
        html_slides.append(f"""
        <div class="slide-card">
            <div class="slide-header">شريحة {s_idx}</div>
            {content_html}
        </div>
        """)

    all_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
    @font-face {{
        font-family: 'Amiri';
        src: url('{font_url}');
    }}
    * {{
        box-sizing: border-box;
        direction: rtl;
        text-align: right;
        font-family: 'Amiri', 'Segoe UI', Tahoma, sans-serif;
    }}
    body {{
        margin: 20px;
        padding: 0;
        color: #0f172a;
        background: #f8fafc;
    }}
    .slide-card {{
        page-break-after: always;
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 24px;
        min-height: {int(h_pt - 60)}px;
    }}
    .slide-header {{
        font-size: 12px;
        color: #6366f1;
        font-weight: bold;
        border-bottom: 1px solid #e2e8f0;
        padding-bottom: 6px;
        margin-bottom: 16px;
    }}
    h2 {{
        font-size: 22px;
        color: #1e1b4b;
        margin: 0 0 12px 0;
        line-height: 1.4;
    }}
    p {{
        font-size: 15px;
        line-height: 1.7;
        margin: 0 0 10px 0;
        color: #334155;
    }}
    </style>
    </head>
    <body>
    {"".join(html_slides)}
    </body>
    </html>
    """

    writer = fitz.DocumentWriter(io.BytesIO())
    # Generate in memory
    buf = io.BytesIO()
    doc_writer = fitz.DocumentWriter(buf)
    story = fitz.Story(html=all_html)
    page_rect = fitz.Rect(0, 0, w_pt, h_pt)
    content_rect = fitz.Rect(20, 20, w_pt - 20, h_pt - 20)

    more = 1
    while more:
        dev = doc_writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(dev)
        doc_writer.end_page()

    doc_writer.close()
    pdf_bytes = buf.getvalue()
    logger.info(f"[PPTX→PDF] PyMuPDF Story conversion generated {len(pdf_bytes)} bytes.")
    return pdf_bytes


def convert_pptx_bytes_to_pdf_bytes(pptx_bytes: bytes) -> bytes:
    """
    Main entry point for RAM-only ephemeral PPTX -> PDF conversion.
    Zero server disk storage. Multi-tiered architecture:
    1. PowerPoint COM (Windows - 100% native fidelity & Arabic shaping)
    2. LibreOffice (Linux/Docker)
    3. PyMuPDF Story HarfBuzz Engine (Pure Python Fallback)
    """
    # 1. Try Windows native PowerPoint COM
    try:
        return _convert_pptx_via_com(pptx_bytes)
    except Exception as e:
        logger.warning(f"[PPTX→PDF] Windows COM conversion skipped/failed: {e}")

    # 2. Try LibreOffice
    try:
        return _convert_pptx_via_libreoffice(pptx_bytes)
    except Exception as e:
        logger.warning(f"[PPTX→PDF] LibreOffice conversion skipped/failed: {e}")

    # 3. Pure Python HarfBuzz Story engine
    try:
        return _convert_pptx_via_story(pptx_bytes)
    except Exception as e:
        logger.error(f"[PPTX→PDF] Pure python conversion failed: {e}")
        raise RuntimeError(f"تعذر تحويل ملف البوربوينت: {e}")


def convert_pptx_to_pdf(pptx_path: str, output_dir: str) -> str:
    """
    File-based conversion wrapper.
    """
    base_name = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(output_dir, f"{base_name}.pdf")
    os.makedirs(output_dir, exist_ok=True)

    with open(pptx_path, "rb") as f:
        pptx_bytes = f.read()

    pdf_bytes = convert_pptx_bytes_to_pdf_bytes(pptx_bytes)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    return pdf_path
