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
    
    # Try Windows MS Office COM Object first (for Local Development)
    if os.name == 'nt':
        try:
            import comtypes.client
            import pythoncom
            # Initialize COM in this thread
            pythoncom.CoInitialize()
            
            powerpoint = comtypes.client.CreateObject("Powerpoint.Application")
            # 32 = ppSaveAsPDF
            abs_input = os.path.abspath(pptx_path)
            abs_output = os.path.abspath(pdf_path)
            
            presentation = powerpoint.Presentations.Open(abs_input, WithWindow=False)
            presentation.SaveAs(abs_output, 32)
            presentation.Close()
            powerpoint.Quit()
            
            pythoncom.CoUninitialize()
            if os.path.exists(pdf_path):
                return pdf_path
        except ImportError:
            logger.warning("comtypes not installed. Falling back to LibreOffice / soffice.")
        except Exception as e:
            logger.warning(f"Windows COM conversion failed: {e}. Falling back to soffice.")

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

