import fitz
import os
import logging

logger = logging.getLogger(__name__)

def annotate_pdf(input_path: str, output_path: str, annotations: dict, is_free_trial: bool = False) -> str:
    """
    Applies highlights and text annotations to a PDF using PyMuPDF.
    annotations format:
    {
       "highlights": [{"text_to_highlight": "...", "page_number": int}],
       "sticky_notes": [{"note_text": "...", "page_number": int}]
    }
    """
    try:
        doc = fitz.open(input_path)
        
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
        # To avoid stacking notes on exactly the same pixel, we stagger the Y coordinate
        page_offsets = {}
        
        for sn in sticky_notes:
            try:
                page_num = int(sn.get("page_number", 1)) - 1
            except (ValueError, TypeError):
                page_num = 0
            note_text = sn.get("note_text", "")
            
            if 0 <= page_num < len(doc) and note_text:
                page = doc.load_page(page_num)
                
                offset_y = page_offsets.get(page_num, 40)
                
                # Add sticky note as a VISIBLE text box (FreeText) so browsers don't hide it
                rect = fitz.Rect(10, offset_y, 250, offset_y + 80)
                # Ensure the text is visible even if the user doesn't click it
                annot = page.add_freetext_annot(rect, f"شرح المحاضر: {note_text}", fontsize=10, fill_color=(1, 0.9, 0.9), text_color=(0.8, 0, 0))
                annot.update()
                
                # Increase offset for the next note on the same page
                page_offsets[page_num] = offset_y + 90
                
        doc.save(output_path, garbage=4, deflate=True)
        doc.close()
        return output_path
        
    except Exception as e:
        logger.error(f"Error annotating PDF: {str(e)}")
        raise e
