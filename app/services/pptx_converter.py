import os
import subprocess
import logging

logger = logging.getLogger(__name__)

def convert_pptx_to_pdf(pptx_path: str, output_dir: str) -> str:
    """
    Converts a PowerPoint file (.pptx) to a PDF file.
    Uses MS Office COM on Windows (if available), or LibreOffice on Linux (for Production VPS).
    """
    base_name = os.path.splitext(os.path.basename(pptx_path))[0]
    pdf_path = os.path.join(output_dir, f"{base_name}.pdf")
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Try LibreOffice / Soffice FIRST if available in PATH (Much more stable for servers)
    try:
        command = "soffice" if os.name == 'nt' else "libreoffice"
        # Check if command exists
        subprocess.run([command, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        logger.info(f"Using {command} for stable PPTX conversion...")
        process = subprocess.run([command, "--headless", "--convert-to", "pdf", "--outdir", output_dir, pptx_path], 
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        
        if process.returncode == 0 and os.path.exists(pdf_path):
            return pdf_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("LibreOffice/Soffice not found or failed. Falling back to MS Office COM...")

    # Fallback to MS Office COM on Windows
    if os.name == 'nt':
        try:
            import comtypes.client
            import pythoncom
            pythoncom.CoInitialize()
            
            # Start PowerPoint
            powerpoint = comtypes.client.CreateObject("Powerpoint.Application")
            abs_input = os.path.abspath(pptx_path)
            abs_output = os.path.abspath(pdf_path)
            
            try:
                presentation = powerpoint.Presentations.Open(abs_input, WithWindow=False)
                presentation.SaveAs(abs_output, 32)
                presentation.Close()
                logger.info("Conversion successful via MS Office COM.")
            finally:
                powerpoint.Quit()
            
            pythoncom.CoUninitialize()
            if os.path.exists(pdf_path):
                return pdf_path
        except Exception as e:
            logger.error(f"Windows COM conversion failed: {e}")

    # Try LibreOffice (Standard for Production Linux VPS)
    try:
        # On Windows, libreoffice is usually called 'soffice' in system PATH
        command = "soffice" if os.name == 'nt' else "libreoffice"
        process = subprocess.run([command, "--headless", "--convert-to", "pdf", "--outdir", output_dir, pptx_path], 
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if process.returncode == 0 and os.path.exists(pdf_path):
            return pdf_path
        else:
            logger.error(f"LibreOffice conversion failed with code {process.returncode}: {process.stderr.decode()}")
            raise Exception("LibreOffice command failed.")
            
    except Exception as e:
        logger.error(f"PPTX to PDF conversion failed completely: {e}")
        raise RuntimeError("فشل تحويل الباوربوينت إلى PDF. يرجى التأكد من أن LibreOffice مثبت على الخادم، أو تحويله يدوياً قبل الرفع.")

