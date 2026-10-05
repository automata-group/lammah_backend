import re
from typing import Optional

def sanitize_subject_title(raw_title: Optional[str]) -> str:
    """
    Sanitizes raw AI title or course name into a clean, concise subject name
    safe for filesystems across Windows, macOS, and Linux.
    """
    if not raw_title:
        return "المادة الدراسية"
    
    t = str(raw_title).strip()
    
    # 1. Strip forbidden filesystem characters and control characters
    t = re.sub(r'[\\/*?:"<>|`\']', ' ', t)
    
    # 2. Normalize whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    
    # 3. Strip trailing or redundant brand tags
    t = re.sub(r'(\s*-\s*لمّاح)+$', '', t)
    t = re.sub(r'(\s*-\s*Lammah)+$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'(\s*-\s*(مخططة|دفتر|تحضير|فويسات|سلايدات).*)+$', '', t)
    
    # 4. Strip redundant leading keywords
    t = re.sub(r'^(دفتر|محاضرة|تلخيص|ملخص|شرح|مراجعة|دليل)\s+', '', t).strip()
    
    # 5. Check if title ended up empty or purely generic
    generic_words = {"بدون عنوان", "سلايدات", "دراسي", "ملزمة", "ملزمة دراسية", "محاضرة جامعية", "untitled"}
    if not t or t.lower() in generic_words:
        return "المادة الدراسية"
        
    return t

def format_arabic_download_filename(
    title: Optional[str],
    is_notebook: bool,
    is_prep: bool
) -> str:
    """
    Builds the exact detailed Arabic filename required:
    - Slides with voice: [اسم المادة] - مخططة فويسات - لمّاح.pdf
    - Slides only (prep): [اسم المادة] - تحضير سلايدات - لمّاح.pdf
    - Notebook with voice: [اسم المادة] - دفتر مخطط فويسات - لمّاح.pdf
    - Notebook slides only (prep): [اسم المادة] - دفتر تحضير سلايدات - لمّاح.pdf
    """
    subject = sanitize_subject_title(title)
    
    if is_notebook:
        detail = "دفتر تحضير سلايدات" if is_prep else "دفتر مخطط فويسات"
    else:
        detail = "تحضير سلايدات" if is_prep else "مخططة فويسات"
        
    return f"{subject} - {detail} - لمّاح.pdf"
