import os
import logging

logger = logging.getLogger(__name__)


def extract_text_from_pptx(pptx_path: str) -> list[dict]:
    """
    Extracts text slide-by-slide directly from a .pptx file using python-pptx.
    Returns a list of dicts: [{"page_number": 1, "text": "..."}]

    This is the primary extraction method — no LibreOffice required.
    Works reliably in any Docker/Linux/Windows environment.
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
            if slide_texts:
                pages.append({
                    "page_number": slide_num,
                    "text": "\n".join(slide_texts)
                })
        logger.info(f"[PPTX] Extracted {len(pages)} slides from {os.path.basename(pptx_path)}")
        return pages
    except Exception as e:
        logger.error(f"[PPTX] python-pptx extraction failed: {e}")
        return []


def convert_pptx_to_pdf(pptx_path: str, output_dir: str) -> str:
    """
    Attempts to convert PPTX → PDF using LibreOffice (headless).
    Falls back gracefully if LibreOffice is unavailable.

    NOTE: If conversion fails, callers should use extract_text_from_pptx() instead.
    """
    import subprocess

    base_name = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(output_dir, f"{base_name}.pdf")
    os.makedirs(output_dir, exist_ok=True)

    # Try LibreOffice (works on Linux/Docker if installed)
    for command in ["libreoffice", "soffice"]:
        try:
            # Verify command exists first
            subprocess.run([command, "--version"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           check=True, timeout=10)

            # Run conversion
            process = subprocess.run(
                [command, "-env:UserInstallation=file:///tmp/libreoffice-profile", "--headless", "--convert-to", "pdf",
                 "--outdir", output_dir, pptx_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90
            )

            if process.returncode == 0 and os.path.exists(pdf_path):
                logger.info(f"[PPTX→PDF] Converted successfully via {command}: {pdf_path}")
                return pdf_path
            else:
                logger.warning(f"[PPTX→PDF] {command} returned code {process.returncode}: {process.stderr.decode()[:300]}")

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning(f"[PPTX→PDF] {command} not available or timed out.")

    # If all conversion attempts failed, raise so callers can use fallback
    raise RuntimeError(
        f"[PPTX→PDF] LibreOffice unavailable. Use extract_text_from_pptx() for text extraction."
    )
