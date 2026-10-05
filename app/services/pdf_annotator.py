import fitz
import os
import re
import logging
from typing import Any, List

logger = logging.getLogger(__name__)

# ── Font Path Resolution ───────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_FONT_DIRS = [
    os.path.join(os.path.dirname(os.path.dirname(_THIS_DIR)), "app", "static", "fonts"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR))), "static", "fonts"),
    "/app/app/static/fonts",
    "/app/static/fonts",
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

import arabic_reshaper
from bidi.algorithm import get_display

def clean_text(text: Any) -> str:
    """Strip HTML tags, markdown symbols, text artifacts and emojis before PDF insertion."""
    if not text:
        return ""
    t = str(text)
    # Strip textual noise & artifacts like [كبسولة] or [إضاءة]
    t = re.sub(r'\[\s*(?:كبسولة|إضاءة|هدف|مثال واقعي|تدقيق|تسجيل المحاضرة|تنبيه هام)\s*\]\s*', '', t)
    t = re.sub(r'(?:كبسولة سريعة|كبسولة|إضاءة)\s*[:\-]?\s*', '', t)
    # Map arrows to clean Arabic colon
    t = t.replace('⬅️', ' : ').replace('⬅', ' : ').replace('->', ' : ').replace('-->', ' : ')
    # Strip HTML tags
    t = re.sub(r'<[^>]+>', '', t)
    t = t.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    t = re.sub(r'^#{1,6}\s*', '', t, flags=re.MULTILINE)
    t = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', t)
    t = re.sub(r'`[^`]+`', lambda m: m.group(0).strip('`'), t)
    # Strip emojis cleanly
    t = re.sub(r'[\U00010000-\U0010ffff]', '', t)
    t = re.sub(r'[\u2600-\u27BF\uE000-\uF8FF]', '', t)
    t = re.sub(r'[\uFE00-\uFE0F]', '', t)  # variation selectors
    t = re.sub(r'[\u200B-\u200F\u202A-\u202E]', '', t)  # directional formatting artifacts
    return t.strip()

def format_arabic(text: Any) -> str:
    """Reshapes Arabic text and forces RTL base direction to prevent bidi flipping."""
    if not text:
        return ""
    clean = clean_text(text)
    reshaped = arabic_reshaper.reshape(clean)
    display_val = get_display(reshaped, base_dir='R')
    if isinstance(display_val, bytes):
        return display_val.decode("utf-8", errors="ignore")
    return display_val

def wrap_arabic_text(text: Any, max_chars: int = 54) -> list[str]:
    """Word wrapper that applies RTL base direction to each line individually."""
    lines: list[str] = []
    for paragraph in str(text).split('\n'):
        p_clean = paragraph.strip()
        if not p_clean:
            continue
        words = p_clean.split()
        current_line: list[str] = []
        current_len = 0
        for word in words:
            if current_len + len(word) > max_chars and current_line:
                raw_str = " ".join(current_line)
                lines.append(format_arabic(raw_str))
                current_line = [word]
                current_len = len(word)
            else:
                current_line.append(word)
                current_len += len(word) + 1
        if current_line:
            raw_str = " ".join(current_line)
            lines.append(format_arabic(raw_str))
    return lines

def sanitize_bullet_points(text: Any) -> list[str]:
    """Cleans repetitive robotic filler like 'الدكتور وضح أن' and returns structured bullet items."""
    if not text:
        return []
    speaker_patterns = [
        r"^\s*[-•*]?\s*الدكتور\s+(وضح|بين|شرح|قال|أكد|نبه|أشار|ذكر|تحدث عن|ركز على|يعتبر|يقول)\s+(أنه|إنه|بأنه|أن|إن|بأن|عن|إلى|على|في)?\s*",
        r"^\s*[-•*]?\s*المحاضر\s+(وضح|بين|شرح|قال|أكد|نبه|أشار|ذكر|تحدث عن|ركز على|يعتبر|يقول)\s+(أنه|إنه|بأنه|أن|إن|بأن|عن|إلى|على|في)?\s*",
        r"^\s*[-•*]?\s*تم\s+(توضيح|بيان|شرح|ذكر|التأكيد على|الإشارة إلى)\s+(أنه|إنه|بأنه|أن|إن|بأن|عن|إلى)?\s*",
        r"^\s*[-•*]?\s*(يوضح|يبين|يشرح|يؤكد|يذكر|ينبه)\s+الدكتور\s+(أنه|إنه|بأنه|أن|إن|بأن|عن|إلى)?\s*",
    ]
    raw_lines = [l.strip() for l in str(text).replace("\r\n", "\n").split("\n") if l.strip()]
    cleaned_points = []
    for line in raw_lines:
        cleaned = line
        for pat in speaker_patterns:
            cleaned = re.sub(pat, "", cleaned, flags=re.UNICODE).strip()
        cleaned = re.sub(r"^[-•*–—\d\.]+\s*", "", cleaned).strip()
        if cleaned and len(cleaned) > 3:
            cleaned_points.append(f"• {cleaned}")

    if not cleaned_points and text:
        cleaned_points = [f"• {clean_text(text)}"]

    return cleaned_points

def insert_text_auto(page, rect, lines, start_fs=10.0, min_fs=7.2, fontname="amiri_reg", color=(0.14, 0.16, 0.22), align=fitz.TEXT_ALIGN_RIGHT):
    """
    Inserts multi-line text with auto font-scaling down to min_fs and line-trimming
    fallback so that a box is NEVER completely blank in the PDF.
    """
    if not lines:
        return 0
    valid_lines = [l for l in lines if l is not None]
    if not valid_lines:
        return 0
        
    cur_lines = list(valid_lines)
    avail_h = rect.height
    calc_fs = avail_h / (max(len(cur_lines), 1) * 1.55)
    fs = min(start_fs, max(min_fs, calc_fs))
    
    text_content = "\n".join(cur_lines)
    rc = page.insert_textbox(rect, text_content, fontsize=fs, fontname=fontname, color=color, align=align)
    
    # Scale down font size if text overflows
    while rc < 0 and fs > min_fs:
        fs -= 0.3
        rc = page.insert_textbox(rect, text_content, fontsize=fs, fontname=fontname, color=color, align=align)
        
    # If still overflowing at min_fs, trim bottom lines until it fits cleanly
    while rc < 0 and len(cur_lines) > 2:
        cur_lines.pop()
        text_content = "\n".join(cur_lines)
        rc = page.insert_textbox(rect, text_content, fontsize=fs, fontname=fontname, color=color, align=align)
        
    return rc




def annotate_pdf(input_path: str, output_path: str, annotations: dict, is_free_trial: bool = False) -> str:
    """
    Applies highlights, pin badges, and modern Bento Grid companion slides to a PDF.
    """
    if not input_path or not input_path.lower().endswith(".pdf"):
        raise ValueError(f"annotate_pdf only accepts PDF files. Got: {input_path}")

    try:
        doc = fitz.open(input_path)
        if is_free_trial and len(doc) > 6:
            logger.info(f"[PDF_ANNOTATOR] Truncating PDF to first 6 pages for free trial user.")
            doc.select(range(6))
        
        # Stop words filter: never highlight trivial grammatical words in isolation
        STOP_WORDS = {
            "and", "the", "is", "in", "to", "of", "for", "with", "on", "at", "by", "from",
            "that", "this", "these", "those", "it", "its", "as", "are", "was", "were", "be",
            "been", "being", "have", "has", "had", "do", "does", "did", "you", "your", "we",
            "our", "us", "they", "them", "their", "he", "him", "his", "she", "her", "or",
            "an", "a", "not", "no", "but", "if", "so", "than", "then", "between", "into",
            "through", "during", "before", "after", "above", "below", "can", "could", "will",
            "would", "should", "shall", "may", "might", "must", "about", "against", "which",
            "who", "whom", "whose", "what", "where", "when", "why", "how", "one", "two",
            "three", "four", "first", "second", "each", "all", "both", "such", "also",
            "very", "often", "just", "only", "now", "more", "most", "some", "any", "without",
            "within", "around", "among", "along", "across", "transact", "pervasive", "like",
            "go", "there", "big", "joined", "new", "world", "lot", "way", "say", "other",
            "form", "space", "operate", "broken", "used", "creates", "both", "allows",
            "means", "call", "called", "see", "seen", "make", "making", "made", "get", "bank",
            "في", "من", "إلى", "على", "عن", "مع", "هذا", "هذه", "ذلك", "تلك", "هؤلاء",
            "التي", "الذي", "الذين", "اللواتي", "أن", "إن", "كان", "كانت", "يكون", "تكون",
            "هو", "هي", "هم", "نحن", "أنا", "أنت", "ثم", "أو", "و", "لا", "ما", "لم",
            "لن", "كل", "بعض", "غير", "قد", "لقد", "حيث", "بين", "كما", "حتى"
        }

        FORBIDDEN_SINGLE_WORDS = {
            "user", "users", "goal", "goals", "activity", "activities", "design", "designs",
            "output", "outputs", "input", "inputs", "tool", "tools", "format", "formats",
            "simple", "compact", "advantage", "advantages", "disadvantage", "disadvantages",
            "process", "processes", "system", "systems", "project", "projects", "software",
            "management", "text", "textual", "data", "overview", "chapter", "lecture",
            "slide", "slides", "first", "second", "third", "next", "types", "type",
            "item", "items", "list", "lists", "notes", "note", "view", "views", "role", "roles",
            "step", "steps", "sequence", "level", "levels", "work", "tasks", "task", "model", "models"
        }

        def extract_candidate_keyphrases(raw_text: str) -> list[str]:
            """Extracts top high-value verbatim phrases (2-5 words) or prominent technical acronyms."""
            stripped_raw = raw_text.strip().rstrip(".,:;")
            words_raw = stripped_raw.split()
            clean = re.sub(r'[^\w\s\-]', ' ', stripped_raw).strip()
            words = clean.split()
            if not words and not words_raw:
                return []

            candidates = []
            # 0. Verbatim raw text itself with original punctuation preserved (2 to 6 words)
            if 2 <= len(words_raw) <= 6 and not all(w.lower() in STOP_WORDS for w in words):
                candidates.append((300, stripped_raw))
                clean_full = " ".join(words)
                if clean_full.lower() != stripped_raw.lower():
                    candidates.append((250, clean_full))

            # 1. Multi-word phrases from raw words (preserving hyphens/internal punctuation)
            for n in range(min(5, len(words_raw)), 1, -1):
                for i in range(len(words_raw) - n + 1):
                    ngram_raw = " ".join(words_raw[i:i+n]).rstrip(".,:;")
                    ngram_clean = re.sub(r'[^\w\s\-]', '', ngram_raw).strip()
                    ngram_words = ngram_clean.split()
                    if not ngram_words or ngram_words[0].lower() in STOP_WORDS or ngram_words[-1].lower() in STOP_WORDS:
                        continue
                    if all(w.lower() in STOP_WORDS for w in ngram_words):
                        continue
                    score = 100 + sum(len(w) for w in ngram_words)
                    score += sum(25 for w in ngram_words if w[0].isupper())
                    candidates.append((score, ngram_raw))
                    if ngram_clean.lower() != ngram_raw.lower():
                        candidates.append((score - 10, ngram_clean))

            # 2. Single technical terms: ONLY acronyms (e.g. SDLC, UML) or technical terms >= 6 chars NOT in forbidden words
            for w in words:
                w_lower = w.lower()
                if w_lower in STOP_WORDS or w_lower in FORBIDDEN_SINGLE_WORDS or len(w) < 3:
                    continue
                if w.isupper() and 2 <= len(w) <= 6:
                    candidates.append((90, w))
                elif len(w) >= 6 and w[0].isupper():
                    candidates.append((60 + len(w), w))

            candidates.sort(key=lambda x: (len(x[1].split()) >= 2, x[0]), reverse=True)
            seen = set()
            unique = []
            for s, phrase in candidates:
                if phrase.lower() not in seen:
                    seen.add(phrase.lower())
                    unique.append(phrase)
            return unique

        # 1. Multi-Color Palette (Harmonious Pastel Highlighters)
        color_palette = {
            "yellow": (1.0, 0.90, 0.25),   # مفاهيم أساسية وتعاريف
            "green":  (0.40, 0.86, 0.52),   # قوانين وقواعد ومعادلات
            "cyan":   (0.35, 0.80, 0.96),   # مصطلحات إنجليزية ومفتاحية
            "blue":   (0.35, 0.80, 0.96),
            "amber":  (1.0, 0.70, 0.28),    # عناصر ومكونات محورية
            "pink":   (1.0, 0.50, 0.68),    # فخاخ الامتحانات والتنبيهات
            "purple": (0.78, 0.55, 0.92),
        }
        color_cycle = [
            (1.0, 0.90, 0.25),
            (0.35, 0.80, 0.96),
            (0.40, 0.86, 0.52),
            (1.0, 0.70, 0.28),
            (0.78, 0.55, 0.92)
        ]
        
        slide_explanations = annotations.get("slide_explanations", [])

        # Stage 1: Exhaustive Highlighting on the Target Slide
        highlights = annotations.get("highlights", [])
        applied_highlights_by_page = {}
        MAX_HIGHLIGHTS_PER_SLIDE = 15

        for h in highlights:
            try:
                base_page_num = int(h.get("page_number", 1)) - 1
            except (ValueError, TypeError):
                base_page_num = 0
            raw_text = str(h.get("text_to_highlight", "")).strip()
            color_key = str(h.get("color", "")).lower().strip()
            stroke_color = color_palette.get(color_key, None)

            if not raw_text or base_page_num < 0 or base_page_num >= len(doc):
                continue

            candidates = extract_candidate_keyphrases(raw_text)
            if not candidates:
                continue

            current_count = len(applied_highlights_by_page.get(base_page_num, []))
            if current_count >= MAX_HIGHLIGHTS_PER_SLIDE:
                continue

            page = doc.load_page(base_page_num)
            page_text = page.get_text("text").strip()
            if base_page_num == 0 or not page_text:
                continue

            for c_phrase in candidates:
                matched_rects = page.search_for(c_phrase)
                if not matched_rects:
                    continue

                valid_rects = [r for r in matched_rects if r.width < page.rect.width * 0.85 and r.height < page.rect.height * 0.20]
                if valid_rects and len(valid_rects) <= 4:
                    # Prevent duplicate identical highlights
                    already_hl = any(prev_h["phrase"].lower() == c_phrase.lower() for prev_h in applied_highlights_by_page.get(base_page_num, []))
                    if already_hl:
                        continue

                    try:
                        annot = page.add_highlight_annot(valid_rects)
                        h_idx = len(applied_highlights_by_page.get(base_page_num, []))
                        s_color = stroke_color or color_cycle[h_idx % len(color_cycle)]
                        annot.set_colors(stroke=s_color)
                        annot.update()

                        applied_highlights_by_page.setdefault(base_page_num, []).append({
                            "phrase": c_phrase,
                            "rects": valid_rects,
                            "color": s_color,
                            "raw_text": raw_text
                        })
                        break
                    except Exception:
                        pass
                    
        # 2. Extract Sticky Notes & Explanations
        sticky_notes = annotations.get("sticky_notes", [])
        sticky_map = {}
        for sn in sticky_notes:
            try:
                page_num = int(sn.get("page_number", 1))
            except (ValueError, TypeError):
                page_num = 1
            note_text = sn.get("note_text", "")
            if note_text:
                sticky_map.setdefault(page_num, []).append(note_text)
                
        # 3. Slide-to-Explanation Mapping
        slide_explanations = annotations.get("slide_explanations", [])
        _NO_CONTENT_PHRASES = [
            "الدكتور ما ذكر هالشريحة", "الدكتور ما ذكر", "لم يذكر", "لم يتطرق", "لم يشرح",
            "لا يوجد محتوى", "no content", "not mentioned", "not covered",
            "did not mention", "did not discuss", "مر المحاضر سريعا", "مرّ المحاضر سريعاً",
        ]
        def _is_no_content(text: str) -> bool:
            t = text.strip().lower()
            return any(phrase.lower() in t for phrase in _NO_CONTENT_PHRASES)
        
        exp_map = {}
        for exp in slide_explanations:
            try:
                p_num = int(exp.get("page_number", 0))
            except (ValueError, TypeError):
                continue
            exp_map[p_num] = exp

        # 4. Generate Single-Page Bento Grid Companion Slides
        if exp_map or sticky_map:
            new_doc = fitz.open()
            slide_page_map = {}
            companion_page_map = {}
            
            font_reg_candidate = AMIRI_REG_PATH or ARIAL_PATH
            font_bold_candidate = AMIRI_BOLD_PATH or AMIRI_REG_PATH or ARIAL_PATH
            
            for i in range(len(doc)):
                new_doc.insert_pdf(doc, from_page=i, to_page=i)
                actual_slide_page = len(new_doc)
                orig_page_num = i + 1
                slide_page_map[orig_page_num] = actual_slide_page

                # Cover / title slide never needs a companion explanation page
                if i == 0:
                    continue
                
                page_notes = sticky_map.get(orig_page_num, [])
                # 100% strict 1-to-1 slide alignment: Slide N ALWAYS gets Explanation N
                exp_entry = exp_map.get(orig_page_num, {})
                if not isinstance(exp_entry, dict):
                    exp_entry = {"explanation": str(exp_entry)}
                
                # Check for genuine audio explanation vs self-study notes
                raw_doctor_exp = clean_text(exp_entry.get("doctor_explanation", ""))
                has_real_audio = bool(raw_doctor_exp and not _is_no_content(raw_doctor_exp))

                # Collect self-study notes cleanly (NEVER masquerade as audio!)
                study_notes = []
                for n in page_notes:
                    cn = clean_text(n)
                    if cn and not _is_no_content(cn) and cn not in study_notes:
                        study_notes.append(cn)
                single_note = clean_text(exp_entry.get("sticky_note", ""))
                if single_note and not _is_no_content(single_note) and single_note not in study_notes:
                    study_notes.append(single_note)

                ai_exp_text = clean_text(exp_entry.get("ai_explanation", "") or exp_entry.get("student_explanation", "") or exp_entry.get("explanation", ""))
                example_text = clean_text(exp_entry.get("example", ""))
                vocab_list = exp_entry.get("vocabulary", [])
                exam_tip = clean_text(exp_entry.get("exam_tip", ""))
                slide_title = clean_text(exp_entry.get("title", "")) or f"الشريحة {orig_page_num}"
                summary_phrase = clean_text(exp_entry.get("summary_phrase", ""))

                has_ai_exp = bool(ai_exp_text and not _is_no_content(ai_exp_text))
                has_extra = bool(example_text or vocab_list or exam_tip)
                has_notes = bool(study_notes)

                if not has_real_audio and not has_ai_exp and not has_extra and not has_notes:
                    continue

                # Ensure real-world analogy is never empty to maintain balanced Bento Grid
                if not example_text or len(example_text.strip()) < 15:
                    example_text = f"💡 تشبيه تطبيقي: في بيئات المشاريع الحقيقية، تمثل مفاهيم {slide_title} حجر الزاوية لتنسيق المهام وتفادي الأخطاء، مما يضمن خروج النظام البرمجي بأعلى كفاءة وجودة تلبي متطلبات العميل."

                page_rect = doc[i].rect
                pw = page_rect.width
                ph = page_rect.height
                comp_pages_for_this_slide = []

                def create_companion_page():
                    p = new_doc.new_page(width=pw, height=ph)
                    fn_r = "amiri_reg"
                    fn_b = "amiri_bold"
                    if font_reg_candidate and os.path.exists(font_reg_candidate):
                        try:
                            p.insert_font(fontname=fn_r, fontfile=font_reg_candidate)
                        except Exception:
                            fn_r = "helv"
                    else:
                        fn_r = "helv"
                    if font_bold_candidate and os.path.exists(font_bold_candidate):
                        try:
                            p.insert_font(fontname=fn_b, fontfile=font_bold_candidate)
                        except Exception:
                            fn_b = fn_r
                    else:
                        fn_b = fn_r
                    # Modern Luxury Stationery Background (#F8FAFC)
                    p.draw_rect(p.rect, color=(0.88, 0.90, 0.94), fill=(0.975, 0.982, 0.990), width=0.8)
                    return p, fn_r, fn_b

                # Layout constants
                m_x = 24
                top_m = 12
                h_h = 66
                bot_m = 16
                gap = 12
                subh_h = 30

                # ── RENDER BENTO GRID COMPANION SLIDE ─────────────────────────
                comp_p, fn_r, fn_b = create_companion_page()
                
                # 1. Header Bar
                h_rect = fitz.Rect(m_x, top_m, pw - m_x, top_m + h_h)
                comp_p.draw_rect(h_rect, color=(0.85, 0.88, 0.93), fill=(1.0, 1.0, 1.0), radius=0.03, width=0.8)
                comp_p.draw_line(fitz.Point(h_rect.x1 - 2, h_rect.y0 + 3), fitz.Point(h_rect.x1 - 2, h_rect.y1 - 3), color=(0.12, 0.25, 0.65), width=4.5)

                # Slide pill on left of header
                pill_rect = fitz.Rect(h_rect.x0 + 12, h_rect.y0 + 14, h_rect.x0 + 110, h_rect.y1 - 14)
                comp_p.draw_rect(pill_rect, color=(0.80, 0.85, 0.93), fill=(0.93, 0.95, 0.99), radius=0.2, width=0.8)
                insert_text_auto(comp_p, fitz.Rect(pill_rect.x0 + 4, pill_rect.y0 + 7, pill_rect.x1 - 4, pill_rect.y1 - 4),
                                 [format_arabic(f"شريحة {orig_page_num}")], start_fs=9.5, min_fs=8.5, fontname=fn_b, color=(0.12, 0.25, 0.65), align=fitz.TEXT_ALIGN_CENTER)

                # Header Title and Subtitle
                title_box = fitz.Rect(pill_rect.x1 + 14, top_m + 6, h_rect.x1 - 16, top_m + 32)
                insert_text_auto(comp_p, title_box, [format_arabic(f"الشرح والتفكيك الأكاديمي  |  {slide_title}")],
                                 start_fs=12.2, min_fs=10.0, fontname=fn_b, color=(0.10, 0.16, 0.38))

                if summary_phrase:
                    sub_text = summary_phrase
                else:
                    sub_text = "تفكيك ذكي وشامل لمفاهيم ومصطلحات الشريحة"

                sub_box = fitz.Rect(pill_rect.x1 + 14, top_m + 34, h_rect.x1 - 16, top_m + 62)
                insert_text_auto(comp_p, sub_box, [format_arabic(f"الخلاصة المركزة: {sub_text}")],
                                 start_fs=9.4, min_fs=8.0, fontname=fn_r, color=(0.55, 0.30, 0.05))

                # 2. Content Geometry
                c_top = top_m + h_h + gap
                c_bot = ph - bot_m - 22
                c_h = c_bot - c_top
                uw = pw - (2 * m_x)

                has_left_col = bool(example_text or vocab_list or exam_tip)
                if has_left_col:
                    w_right = int(uw * 0.58)
                    x0_right = pw - m_x - w_right
                    x1_right = pw - m_x
                    w_left = uw - w_right - gap
                    x0_left = m_x
                    x1_left = m_x + w_left
                else:
                    w_right = uw
                    x0_right = m_x
                    x1_right = pw - m_x
                    x0_left = 0
                    x1_left = 0

                # ── RIGHT COLUMN: Main Core Card (Explanation + Audio / Self-Study Notes) ──
                right_rect = fitz.Rect(x0_right, c_top, x1_right, c_bot)
                comp_p.draw_rect(right_rect, color=(0.85, 0.88, 0.93), fill=(1.0, 1.0, 1.0), radius=0.03, width=0.8)

                sub_r = fitz.Rect(right_rect.x0, right_rect.y0, right_rect.x1, right_rect.y0 + subh_h)
                comp_p.draw_rect(sub_r, color=(0.85, 0.88, 0.93), fill=(0.94, 0.96, 0.995), radius=0.03, width=0.8)
                comp_p.draw_line(fitz.Point(right_rect.x1 - 2, right_rect.y0 + 2), fitz.Point(right_rect.x1 - 2, right_rect.y0 + subh_h - 2), color=(0.15, 0.28, 0.65), width=3.6)
                
                if has_real_audio:
                    card_title = "قاعة المحاضرة والشرح المفاهيمي (مرتبط بالتسجيل والسلايد)"
                else:
                    card_title = "الشرح والتفكيك المفاهيمي الجوهري (إعداد رفيق لمّاح الذكي)"

                insert_text_auto(comp_p, fitz.Rect(right_rect.x0 + 12, right_rect.y0 + 5, right_rect.x1 - 12, right_rect.y0 + subh_h - 3),
                                 [format_arabic(card_title)], start_fs=10.5, min_fs=9.0, fontname=fn_b, color=(0.12, 0.18, 0.45))

                # Assemble Right Column content
                r_lines = []
                # 1. Audio notes from lecturer ONLY if genuine audio exists!
                if has_real_audio:
                    r_lines.append("قاعة المحاضرة (ما ركز عليه وشرحه الدكتور):")
                    doc_bullets = sanitize_bullet_points(raw_doctor_exp)
                    r_lines.extend(doc_bullets[:3])
                    r_lines.append("")
                elif study_notes:
                    # Self-Study / Document Mode: Notes presented honestly without mentioning audio!
                    r_lines.append("تنبيهات وملاحظات المذاكرة المركزة:")
                    for sn in study_notes[:3]:
                        clean_sn = clean_text(sn)
                        if clean_sn:
                            r_lines.append(f"• {clean_sn}")
                    r_lines.append("")

                # 2. Core AI explanation points directly explaining the slide
                ai_bullets = sanitize_bullet_points(ai_exp_text)
                if ai_bullets:
                    r_lines.append("تفاصيل الشرح والاستيعاب الأكاديمي:")
                    for b in ai_bullets[:6]:
                        clean_b = b.lstrip("•- \t")
                        r_lines.append(f"• {clean_b}")
                elif ai_exp_text:
                    r_lines.append("تفاصيل الشرح والاستيعاب الأكاديمي:")
                    r_lines.append(ai_exp_text)

                w_right_lines = []
                for l in r_lines:
                    if l == "":
                        w_right_lines.append("")
                    elif any(l.startswith(pfx) for pfx in ["قاعة المحاضرة", "تنبيهات", "تفاصيل"]):
                        w_right_lines.append(format_arabic(l))
                    else:
                        w_right_lines.extend(wrap_arabic_text(l, max_chars=68))

                insert_text_auto(comp_p, fitz.Rect(right_rect.x0 + 16, right_rect.y0 + subh_h + 10, right_rect.x1 - 16, right_rect.y1 - 10),
                                 w_right_lines, start_fs=9.8, min_fs=7.2, fontname=fn_r, color=(0.12, 0.14, 0.20))

                # ── LEFT COLUMN: Real-World Analogy & Bilingual Exam Triggers ──────────
                if has_left_col:
                    # Card 1 text
                    w_ex = wrap_arabic_text(example_text, max_chars=44) if example_text else []

                    # Build dictionary of terms to Arabic translations for mandatory bilingual exam triggers
                    term_trans_map = {}
                    for v in vocab_list:
                        if isinstance(v, dict):
                            t = clean_text(v.get("term", "")).strip()
                            tr = clean_text(v.get("translation", "") or v.get("trans", "")).strip()
                            if t and tr:
                                term_trans_map[t.lower()] = tr

                    # Card 2 text: Vocabulary + Exam tips with MANDATORY Arabic Translations
                    exam_lines = []
                    if vocab_list:
                        exam_lines.append("مفاتيح ومصطلحات الاختبار (مع الترجمة):")
                        for v in vocab_list[:4]:
                            if isinstance(v, dict):
                                t = clean_text(v.get("term", "")).strip()
                                tr = clean_text(v.get("translation", "") or v.get("trans", "")).strip()
                                sd = clean_text(v.get("simple_def", "")).strip()
                                if t and tr and sd:
                                    exam_lines.append(f"• {t} [{tr}] : {sd}")
                                elif t and tr:
                                    exam_lines.append(f"• {t} [{tr}]")
                                elif t and sd:
                                    exam_lines.append(f"• {t} : {sd}")
                            elif isinstance(v, str) and v.strip():
                                exam_lines.append(f"• {clean_text(v)}")
                        exam_lines.append("")

                    if exam_tip:
                        exam_lines.append("فخ امتحاني ونقاط الحذر:")
                        tip_clean = clean_text(exam_tip)
                        for t_line in tip_clean.split("\n"):
                            t_line = t_line.strip().lstrip("•-* ")
                            if not t_line:
                                continue
                            # Ensure English terms in exam tips have Arabic translation attached
                            if not re.search(r'\[[\u0600-\u06FF\s]+\]', t_line):
                                for tm, tr in term_trans_map.items():
                                    pattern = re.compile(rf'\(?\b{re.escape(tm)}\)?', re.IGNORECASE)
                                    if pattern.search(t_line):
                                        t_line = pattern.sub(rf'\g<0> [{tr}]', t_line, count=1)
                                        break
                            exam_lines.append(f"• {t_line}")

                    w_exam = []
                    for el in exam_lines:
                        if el == "":
                            w_exam.append("")
                        elif el.startswith("📌") or el.startswith("⚠️"):
                            w_exam.append(format_arabic(el))
                        else:
                            w_exam.extend(wrap_arabic_text(el, max_chars=44))

                    while w_exam and w_exam[-1] == "":
                        w_exam.pop()

                    has_card1 = bool(w_ex)
                    has_card2 = bool(w_exam and any(not (l.startswith("📌") or l.startswith("⚠️") or l == "") for l in exam_lines))

                    if has_card1 and has_card2:
                        ex_c = max(len(w_ex), 3)
                        exam_c = max(len(w_exam), 3)
                        ratio = max(0.28, min(0.48, ex_c / (ex_c + exam_c)))
                        h_top = int((c_h - gap) * ratio)
                        lt_rect = fitz.Rect(x0_left, c_top, x1_left, c_top + h_top)
                        lb_rect = fitz.Rect(x0_left, c_top + h_top + gap, x1_left, c_bot)
                    elif has_card1 and not has_card2:
                        lt_rect = fitz.Rect(x0_left, c_top, x1_left, c_bot)
                        lb_rect = None
                    elif not has_card1 and has_card2:
                        lt_rect = None
                        lb_rect = fitz.Rect(x0_left, c_top, x1_left, c_bot)
                    else:
                        lt_rect = None
                        lb_rect = None

                    if lt_rect:
                        # Card 1: Real-World Analogy
                        comp_p.draw_rect(lt_rect, color=(0.78, 0.88, 0.82), fill=(0.975, 0.995, 0.985), radius=0.03, width=0.8)
                        sub_lt = fitz.Rect(lt_rect.x0, lt_rect.y0, lt_rect.x1, lt_rect.y0 + subh_h)
                        comp_p.draw_rect(sub_lt, color=(0.78, 0.88, 0.82), fill=(0.91, 0.965, 0.935), radius=0.03, width=0.8)
                        comp_p.draw_line(fitz.Point(lt_rect.x1 - 2, lt_rect.y0 + 2), fitz.Point(lt_rect.x1 - 2, lt_rect.y0 + subh_h - 2), color=(0.08, 0.55, 0.28), width=3.6)
                        insert_text_auto(comp_p, fitz.Rect(lt_rect.x0 + 10, lt_rect.y0 + 5, lt_rect.x1 - 10, lt_rect.y0 + subh_h - 3),
                                         [format_arabic("الربط الواقعي والتطبيقي (مثال ملموس)")],
                                         start_fs=10.2, min_fs=8.8, fontname=fn_b, color=(0.08, 0.38, 0.20))
                        
                        insert_text_auto(comp_p, fitz.Rect(lt_rect.x0 + 12, lt_rect.y0 + subh_h + 8, lt_rect.x1 - 12, lt_rect.y1 - 6),
                                         w_ex, start_fs=9.8, min_fs=7.8, fontname=fn_r, color=(0.06, 0.22, 0.12))

                    if lb_rect:
                        # Card 2: Exam Triggers & Vocabulary
                        comp_p.draw_rect(lb_rect, color=(0.92, 0.82, 0.70), fill=(0.998, 0.985, 0.965), radius=0.03, width=0.8)
                        sub_lb = fitz.Rect(lb_rect.x0, lb_rect.y0, lb_rect.x1, lb_rect.y0 + subh_h)
                        comp_p.draw_rect(sub_lb, color=(0.92, 0.82, 0.70), fill=(0.99, 0.945, 0.88), radius=0.03, width=0.8)
                        comp_p.draw_line(fitz.Point(lb_rect.x1 - 2, lb_rect.y0 + 2), fitz.Point(lb_rect.x1 - 2, lb_rect.y0 + subh_h - 2), color=(0.85, 0.45, 0.08), width=3.6)
                        insert_text_auto(comp_p, fitz.Rect(lb_rect.x0 + 10, lb_rect.y0 + 5, lb_rect.x1 - 10, lb_rect.y0 + subh_h - 3),
                                         [format_arabic("مفاتيح وفخاخ الامتحان بالإنجليزي (Exam Triggers)")],
                                         start_fs=10.0, min_fs=8.5, fontname=fn_b, color=(0.65, 0.28, 0.04))
                        
                        insert_text_auto(comp_p, fitz.Rect(lb_rect.x0 + 12, lb_rect.y0 + subh_h + 8, lb_rect.x1 - 12, lb_rect.y1 - 10),
                                         w_exam, start_fs=9.2, min_fs=7.2, fontname=fn_r, color=(0.55, 0.22, 0.04))
                # 3. Footer
                comp_p.draw_line(fitz.Point(m_x + 10, ph - 16), fitz.Point(pw - m_x - 10, ph - 16), color=(0.82, 0.84, 0.88), width=0.8)
                comp_p.insert_textbox(fitz.Rect(m_x, ph - 16, pw - m_x, ph - 2),
                                      format_arabic(f"دفتر لمّاح الأكاديمي  |  دليل المذاكرة والتحضير للامتحان  |  شريحة {orig_page_num}"),
                                      fontsize=8.2, fontname=fn_r, color=(0.42, 0.45, 0.50), align=fitz.TEXT_ALIGN_CENTER)

                comp_pages_for_this_slide.append(len(new_doc))
                companion_page_map[orig_page_num] = comp_pages_for_this_slide

            annotations["slide_page_map"] = slide_page_map
            annotations["companion_page_map"] = companion_page_map
            old_doc = doc
            doc = new_doc
            if old_doc and not old_doc.is_closed:
                old_doc.close()

        doc.save(output_path, garbage=4, deflate=True)
        return output_path
        
    except Exception as e:
        logger.error(f"Error annotating PDF: {str(e)}")
        raise e
    finally:
        if 'doc' in locals() and doc and not doc.is_closed:
            doc.close()
        if 'new_doc' in locals() and new_doc and not new_doc.is_closed:
            new_doc.close()
