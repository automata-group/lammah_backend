import fitz
import os
import logging

logger = logging.getLogger(__name__)

# ── Font Path Resolution ───────────────────────────────────────────────────────
# Search multiple candidate paths (works in both local dev and Docker)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_FONT_DIRS = [
    # Docker: /app/app/static/fonts  (services/ -> app/ -> app/)
    os.path.join(os.path.dirname(os.path.dirname(_THIS_DIR)), "app", "static", "fonts"),
    # Docker flat: /app/static/fonts
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR))), "static", "fonts"),
    # Absolute Docker paths
    "/app/app/static/fonts",
    "/app/static/fonts",
    # Windows dev
    "C:/Windows/Fonts",
]

def _find_font(filename: str) -> str | None:
    """Return the first absolute path where `filename` exists, or None."""
    for d in _CANDIDATE_FONT_DIRS:
        fp = os.path.join(d, filename)
        if os.path.exists(fp) and os.path.getsize(fp) > 1000:
            return fp
    return None

AMIRI_REG_PATH  = _find_font("Amiri-Regular.ttf")
AMIRI_BOLD_PATH = _find_font("Amiri-Bold.ttf")
ARIAL_PATH      = _find_font("arial.ttf")

logger.info(f"[PDF_ANNOTATOR] Amiri-Regular: {AMIRI_REG_PATH}")
logger.info(f"[PDF_ANNOTATOR] Amiri-Bold:    {AMIRI_BOLD_PATH}")

def annotate_pdf(input_path: str, output_path: str, annotations: dict, is_free_trial: bool = False) -> str:
    """
    Applies highlights and text annotations to a PDF using PyMuPDF.
    annotations format:
    {
       "highlights": [{"text_to_highlight": "...", "page_number": int}],
       "sticky_notes": [{"note_text": "...", "page_number": int}]
    }
    """
    # Guard: only accept actual PDF files — PPTX must be converted first
    if not input_path or not input_path.lower().endswith(".pdf"):
        raise ValueError(f"annotate_pdf only accepts PDF files. Got: {input_path}")

    try:
        doc = fitz.open(input_path)
        if is_free_trial and len(doc) > 6:
            logger.info(f"[PDF_ANNOTATOR] Truncating PDF to first 6 pages for free trial user.")
            doc.select(range(6))
        
        # Add visual watermark to page 1 to prove modification
        if len(doc) > 0:
            doc[0].insert_text(fitz.Point(10, 20), "Smart Note Mode Active", fontsize=12, color=(0.4, 0.2, 0.8))
            
        if is_free_trial:
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                rect = page.rect
                # Insert watermark across the page
                page.insert_text(
                    fitz.Point(50, rect.height / 2),
                    "Created by Free Trial - اشترك للحصول على الملزمة كاملة وبدون علامة",
                    fontsize=20,
                    color=(0.8, 0.8, 0.8),
                    fill_opacity=0.3
                )
                
        # --- HELPER FUNCTION FOR ROBUST TEXT NORMALIZATION ---
        def normalize_text(text_val):
            import re
            """Normalize Arabic/English text for robust mapping."""
            text_val = re.sub(r'[^\w\s]', '', text_val)
            for char, replacement in [('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ة', 'ه'), ('ى', 'ا'), ('ي', 'ي')]:
                text_val = text_val.replace(char, replacement)
            return text_val.lower().strip()
        # -----------------------------------------------------
            
        # 1. Apply Highlights using Bounding Boxes Geometry
        highlights = annotations.get("highlights", [])
        for h in highlights:
            try:
                page_num = int(h.get("page_number", 1)) - 1
            except (ValueError, TypeError):
                page_num = 0
            text = h.get("text_to_highlight", "")
            
            if 0 <= page_num < len(doc) and text:
                page = doc.load_page(page_num)
                
                # Split target sentence into a set of normalized unique words (>2 chars)
                target_words = set(normalize_text(w) for w in text.split() if len(w) > 2)
                if not target_words:
                    continue
                    
                # Extract all mechanical words and coordinates from the page
                page_words = page.get_text("words")
                
                from collections import defaultdict
                block_matches = defaultdict(list)
                
                for w in page_words:
                    rect = fitz.Rect(w[:4])
                    word_norm = normalize_text(w[4])
                    block_no = w[5]
                    
                    if len(word_norm) > 2 and word_norm in target_words:
                        block_matches[block_no].append(rect)
                        
                # Identify the block/paragraph that has the highest density of our target words
                if block_matches:
                    best_block = max(block_matches.keys(), key=lambda b: len(block_matches[b]))
                    matched_rects = block_matches[best_block]
                    
                    if len(matched_rects) >= 1:
                        try:
                            # Draw a precise highlight over the exact coordinates of the matched words
                            annot = page.add_highlight_annot(matched_rects)
                            annot.set_colors(stroke=(1, 0.9, 0.2)) # Soft yellow
                            annot.update()
                        except ValueError:
                            pass
                    
        # 2. Apply Sticky Notes
        sticky_notes = annotations.get("sticky_notes", [])
        page_offsets = {}
        
        import arabic_reshaper
        from bidi.algorithm import get_display
        import re as _re
        
        def clean_text(text):
            """Strip HTML tags, markdown symbols and unescape entities before PDF insertion."""
            if not text: return ""
            # Remove <span ...>...</span> and all other HTML tags
            text = _re.sub(r'<[^>]+>', '', str(text))
            # Unescape common HTML entities
            text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
            # Remove markdown heading hashes
            text = _re.sub(r'^#{1,6}\s*', '', text, flags=_re.MULTILINE)
            # Remove markdown bold/italic stars
            text = _re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
            # Remove backtick code formatting
            text = _re.sub(r'`[^`]+`', lambda m: m.group(0).strip('`'), text)
            return text.strip()

        def format_arabic(text):
            if not text: return ""
            return get_display(arabic_reshaper.reshape(str(text)))

        def wrap_arabic_text(text, max_chars=80):
            """Custom word wrapper that applies bidi to each line individually for correct RTL rendering."""
            lines = []
            for paragraph in str(text).split('\n'):
                words = paragraph.split()
                current_line = []
                current_len = 0
                for word in words:
                    if current_len + len(word) > max_chars and current_line:
                        lines.append(" ".join(current_line))
                        current_line = [word]
                        current_len = len(word)
                    else:
                        current_line.append(word)
                        current_len += len(word) + 1
                if current_line:
                    lines.append(" ".join(current_line))
            
            # Apply Arabic reshaping to each line separately so RTL flow is maintained
            return [format_arabic(line) for line in lines]
            
        # Build a map: page_number (1-indexed) -> list of sticky note texts
        # These will be merged into the explanation page instead of overlaid on the slide
        sticky_map = {}
        for sn in sticky_notes:
            try:
                page_num = int(sn.get("page_number", 1))
            except (ValueError, TypeError):
                page_num = 1
            note_text = sn.get("note_text", "")
            if note_text:
                sticky_map.setdefault(page_num, []).append(note_text)
                
        # 3. Apply Full-Page Explanations (Pedagogical Notebook Upgrade)
        slide_explanations = annotations.get("slide_explanations", [])
        
        # ── Filter out "not covered" placeholder entries ──────────────────────
        # When the AI says the lecturer didn't discuss a slide it writes one of
        # the sentinel phrases below.  We must NOT create a yellow note page for
        # those slides — the user asked us to skip them entirely.
        _NO_CONTENT_PHRASES = [
            "الدكتور ما ذكر هالشريحة",
            "الدكتور ما ذكر",
            "لم يذكر",
            "لم يتطرق",
            "لم يشرح",
            "لا يوجد محتوى",
            "no content",
            "not mentioned",
            "not covered",
            "did not mention",
            "did not discuss",
        ]
        def _is_no_content(text: str) -> bool:
            t = text.strip().lower()
            return any(phrase.lower() in t for phrase in _NO_CONTENT_PHRASES)
        
        slide_explanations = [
            exp for exp in slide_explanations
            if not _is_no_content(exp.get("explanation", ""))
        ]
        
        if slide_explanations:
            new_doc = fitz.open()
            exp_map = {int(exp.get("page_number", 0)): exp.get("explanation", "") for exp in slide_explanations}
            
            for i in range(len(doc)):
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                
                page_notes = sticky_map.get(i + 1, [])
                has_explanation = (i + 1) in exp_map
                
                if has_explanation or page_notes:
                    exp_text = clean_text(exp_map.get(i + 1, ""))
                    page_rect = doc[i].rect
                    new_page = new_doc.new_page(width=page_rect.width, height=page_rect.height)
                    
                    # Load a font that supports Arabic (uses module-level resolved paths)
                    font_loaded = False
                    for font_candidate in [AMIRI_REG_PATH, ARIAL_PATH]:
                        if font_candidate and os.path.exists(font_candidate):
                            try:
                                new_page.insert_font(fontname="arfont", fontfile=font_candidate)
                                font_loaded = True
                                logger.info(f"[PDF_ANNOTATOR] Loaded font from: {font_candidate}")
                                break
                            except Exception as fe:
                                logger.warning(f"[PDF_ANNOTATOR] Failed to load font {font_candidate}: {fe}")

                    fontname_to_use = "arfont" if font_loaded else "helv"
                    
                    # Warm yellow-cream background
                    new_page.draw_rect(new_page.rect, color=(1.0, 1.0, 0.92), fill=(1.0, 1.0, 0.96))
                    
                    # ── HEADER ──
                    header = format_arabic(f"\u0645\u0627 \u0642\u0627\u0644\u0647 \u0627\u0644\u062f\u0643\u062a\u0648\u0631 | \u0627\u0644\u0634\u0631\u064a\u062d\u0629 {i+1}")
                    header_rect = fitz.Rect(50, 15, page_rect.width - 50, 65)
                    new_page.insert_textbox(header_rect, header, fontsize=15, color=(0.5, 0.3, 0.0), fontname=fontname_to_use, align=fitz.TEXT_ALIGN_RIGHT)
                    new_page.draw_line(fitz.Point(50, 68), fitz.Point(page_rect.width - 50, 68), color=(0.8, 0.6, 0.0), width=1.5)
                    
                    y_pos = 88
                    
                    # ── SECTION 1: Quick Notes (from Mapper) ──
                    if page_notes:
                        section_header = format_arabic("\u2714 \u0646\u0642\u0627\u0637 \u0633\u0631\u064a\u0639\u0629 \u0645\u0646 \u0643\u0644\u0627\u0645 \u0627\u0644\u062f\u0643\u062a\u0648\u0631")
                        sh_rect = fitz.Rect(50, y_pos - 5, page_rect.width - 50, y_pos + 25)
                        new_page.insert_textbox(sh_rect, section_header, fontsize=11, color=(0.2, 0.5, 0.1), fontname=fontname_to_use, align=fitz.TEXT_ALIGN_RIGHT)
                        y_pos += 26
                        
                        for note in page_notes:
                            bullet_lines = wrap_arabic_text(f"\u25cf {clean_text(note)}", max_chars=85)
                            for bline in bullet_lines:
                                if y_pos > page_rect.height - 40:
                                    break
                                b_rect = fitz.Rect(50, y_pos - 15, page_rect.width - 50, y_pos + 15)
                                new_page.insert_textbox(b_rect, bline, fontsize=11, color=(0.15, 0.4, 0.1), fontname=fontname_to_use, align=fitz.TEXT_ALIGN_RIGHT)
                                y_pos += 22
                        
                        # Divider between sections
                        if exp_text:
                            y_pos += 5
                            new_page.draw_line(fitz.Point(80, y_pos), fitz.Point(page_rect.width - 80, y_pos), color=(0.7, 0.7, 0.5), width=0.8)
                            y_pos += 10
                    
                    # ── SECTION 2: Full Explanation (from Tutor) ──
                    if exp_text:
                        if page_notes:
                            section_header2 = format_arabic("\u270f\ufe0f \u0634\u0631\u062d \u062a\u0641\u0635\u064a\u0645\u064a")
                            sh2_rect = fitz.Rect(50, y_pos - 5, page_rect.width - 50, y_pos + 25)
                            new_page.insert_textbox(sh2_rect, section_header2, fontsize=11, color=(0.3, 0.1, 0.5), fontname=fontname_to_use, align=fitz.TEXT_ALIGN_RIGHT)
                            y_pos += 26
                        
                        lines = wrap_arabic_text(exp_text, max_chars=85)
                        for line in lines:
                            if y_pos > page_rect.height - 40:
                                break
                            text_rect = fitz.Rect(50, y_pos - 15, page_rect.width - 50, y_pos + 15)
                            new_page.insert_textbox(text_rect, line, fontsize=12, color=(0.1, 0.1, 0.1), fontname=fontname_to_use, align=fitz.TEXT_ALIGN_RIGHT)
                            y_pos += 24
            
            doc = new_doc

        doc.save(output_path, garbage=4, deflate=True)
        doc.close()
        return output_path
        
    except Exception as e:
        logger.error(f"Error annotating PDF: {str(e)}")
        raise e
