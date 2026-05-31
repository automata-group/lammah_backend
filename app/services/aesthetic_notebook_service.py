"""
aesthetic_notebook_service.py
================================================================
Premium "Lammah Academic Notebook" PDF Generator.
Features:
  - Creamy lined notebook background (classic classroom feel).
  - Amiri Arabic calligraphy font for authentic handwritten look.
  - Soft beige marker highlighting for English terminology.
  - Per-page student attribution watermark footer.
  - Exam-alert and note callout boxes with color-coded left bars.
  - Chapter dividers with decorative underlines.
  - Numbered lists and structured bullet points.
  - Professional cover page with student metadata box.
"""

import os
import re
import logging
import urllib.request
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from bidi.algorithm import get_display
import arabic_reshaper

logger = logging.getLogger(__name__)

# ── Font Paths ────────────────────────────────────────────────────────────────
_BACKEND   = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Search in multiple potential directories (dev and Docker paths)
POTENTIAL_DIRS = [
    os.path.join(_BACKEND, "app", "static", "fonts"),
    os.path.join(_BACKEND, "static", "fonts"),
    "/app/app/static/fonts",
    "/app/static/fonts"
]

FONTS_DIR = None
for d in POTENTIAL_DIRS:
    if os.path.isdir(d):
        FONTS_DIR = d
        break

if not FONTS_DIR:
    FONTS_DIR = os.path.join(_BACKEND, "app", "static", "fonts")
    os.makedirs(FONTS_DIR, exist_ok=True)

FONT_REG_PATH  = os.path.join(FONTS_DIR, "Amiri-Regular.ttf")
FONT_BOLD_PATH = os.path.join(FONTS_DIR, "Amiri-Bold.ttf")

def _download_fonts():
    urls = {
        FONT_REG_PATH:  "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",
        FONT_BOLD_PATH: "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf",
    }
    for path, url in urls.items():
        # Remove empty/corrupted font files (e.g. < 1000 bytes)
        if os.path.exists(path) and os.path.getsize(path) < 1000:
            try:
                os.remove(path)
            except Exception:
                pass
        
        if not os.path.exists(path):
            try:
                urllib.request.urlretrieve(url, path)
                logger.info(f"[NOTEBOOK] Font downloaded successfully to {path}")
            except Exception as e:
                logger.error(f"[NOTEBOOK] Font download failed for {url}: {e}")

_download_fonts()

def _reg_fonts():
    for name, path, fallback in [
        ("HW-Reg",  FONT_REG_PATH,  "Helvetica"),
        ("HW-Bold", FONT_BOLD_PATH, "Helvetica-Bold"),
    ]:
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                logger.info(f"[NOTEBOOK] Successfully registered font {name} from {path}")
            except Exception as e:
                logger.error(f"[NOTEBOOK] Failed to register font {name}: {e}. Falling back to {fallback}")
                pdfmetrics.registerFont(TTFont(name, fallback))
        else:
            logger.warning(f"[NOTEBOOK] Font path {path} does not exist or is too small. Falling back to {fallback}")
            pdfmetrics.registerFont(TTFont(name, fallback))

_reg_fonts()


FONT_REG  = "HW-Reg"
FONT_BOLD = "HW-Bold"

# ── Color Palette ─────────────────────────────────────────────────────────────
C_BG       = HexColor("#FDFBF7")   # Warm cream paper
C_LINE     = HexColor("#EDE7D9")   # Ruled lines
C_MARGIN   = HexColor("#F5C6C6")   # Red left margin
C_BODY     = HexColor("#2E2319")   # Ink dark brown
C_MUTED    = HexColor("#8A7A6A")   # Pencil grey
C_BLUE     = HexColor("#1B3A8A")   # Royal blue headings
C_GOLD     = HexColor("#B45309")   # Burnt sienna accents
C_TEAL     = HexColor("#0F766E")   # Teal for exam alerts
C_GREEN    = HexColor("#166534")   # Dark green for tips
C_MARK     = HexColor("#EDE0C4")   # Beige highlighter
C_BOX_BG   = HexColor("#F7F1E8")   # Callout background
C_BOX_BD   = HexColor("#D9C9B5")   # Callout border
C_DIV      = HexColor("#E3D9C9")   # Divider lines
C_COVER_H  = HexColor("#162B5E")   # Deep navy cover header

W, H      = A4
MARGIN    = 52
TXT_W     = W - MARGIN * 2
LINE_H    = 24
FSIZ      = 11
BOTTOM    = 62

# ── Arabic BiDi Helper ────────────────────────────────────────────────────────
_AR = re.compile(r'[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]')

# Invisible Unicode control characters that can break heading/line detection
_CTRL_STRIP = re.compile(
    r'[\u202a\u202b\u202c\u202d\u202e\u200e\u200f\ufeff'
    r'\u200b\u200c\u200d\u2066\u2067\u2068\u2069\u061c]'
)

def _strip_ctrl(t):
    """Remove invisible Unicode directional/control characters."""
    return _CTRL_STRIP.sub('', t)

def _has_ar(t): return bool(_AR.search(t))

def _is_rtl_para(t):
    """Determine if a paragraph is primarily RTL (Arabic).
    Skips leading punctuation/numbers/symbols to find the first real character."""
    cleaned = re.sub(r'^[\s\d\W]+', '', _strip_ctrl(t))
    return bool(_AR.search(cleaned[0])) if cleaned else False

def _bidi(text):
    if not text: return "", False
    text = _strip_ctrl(text)
    if _has_ar(text):
        try:
            reshaped = arabic_reshaper.reshape(text)
            bidi_text = get_display(reshaped)
            bidi_text = _strip_ctrl(bidi_text)
            return bidi_text, True
        except Exception:
            return text, True
    return text, False

def _clean(s):
    return re.sub(r'\*\*|<mark>|</mark>|<span[^>]*>|</span>', '', s)

# ── Lined Paper Background ────────────────────────────────────────────────────
def _bg(c, is_rtl=True):
    c.setFillColor(C_BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setStrokeColor(C_LINE)
    c.setLineWidth(0.5)
    y = H - MARGIN - 28
    while y >= BOTTOM + 8:
        c.line(MARGIN - 12, y, W - MARGIN + 12, y)
        y -= 26
    # Margin line (Dynamic placement)
    c.setStrokeColor(C_MARGIN)
    c.setLineWidth(1.0)
    if is_rtl:
        c.line(W - MARGIN - 18, 0, W - MARGIN - 18, H)
    else:
        c.line(MARGIN + 18, 0, MARGIN + 18, H)

# ── Page State ────────────────────────────────────────────────────────────────
class _S:
    def __init__(self, page_num=1, student_name="طالب متميز", is_rtl=True):
        self.y = H - MARGIN - 18
        self.page_num = page_num
        self.student_name = student_name
        self.main_rtl = is_rtl   # Document-level RTL (never changes after init)
        self.is_rtl = is_rtl     # Current paragraph RTL (changes per paragraph)

    def new_page(self, c):
        c.showPage()
        self.page_num += 1
        _bg(c, is_rtl=self.main_rtl)   # Use document-level, not per-para
        self._footer(c)
        self.y = H - MARGIN - 18

    def _footer(self, c):
        # Separator line
        c.setStrokeColor(C_DIV)
        c.setLineWidth(0.5)
        c.line(MARGIN, BOTTOM - 8, W - MARGIN, BOTTOM - 8)
        c.setFont(FONT_REG, 7.5)
        c.setFillColor(C_MUTED)
        
        # Use document-level direction for footer language
        if not self.main_rtl:
            brand, _ = _bidi("Lammah Study Notebook")
            c.drawString(MARGIN, BOTTOM - 20, brand)
            attr, _ = _bidi(f"Prepared by: {self.student_name}")
            aw = c.stringWidth(attr, FONT_REG, 7.5)
            c.drawString(W / 2 - aw / 2, BOTTOM - 20, attr)
            pg, _ = _bidi(f"Page {self.page_num}")
            pw = c.stringWidth(pg, FONT_REG, 7.5)
            c.drawString(W - MARGIN - pw, BOTTOM - 20, pg)
        else:
            brand, _ = _bidi("متجر لمّاح للملازم")
            c.drawString(MARGIN, BOTTOM - 20, brand)
            attr, _ = _bidi(f"إعداد: {self.student_name}")
            aw = c.stringWidth(attr, FONT_REG, 7.5)
            c.drawString(W / 2 - aw / 2, BOTTOM - 20, attr)
            pg, _ = _bidi(f"صفحة {self.page_num}")
            pw = c.stringWidth(pg, FONT_REG, 7.5)
            c.drawString(W - MARGIN - pw, BOTTOM - 20, pg)

# ── Text Paragraph Renderer ───────────────────────────────────────────────────
def _para(c, text, st, font_sz=FSIZ, lh=LINE_H, color=C_BODY, bold=False, indent=0, max_w=None):
    if max_w is None: max_w = TXT_W
    fn = FONT_BOLD if bold else FONT_REG
    avail = max_w - indent

    for raw in text.split("\n"):
        p = raw.strip()
        if not p:
            st.y -= int(lh * 0.35)
            continue

        is_bullet = p.startswith(("- ", "• ", "* ", "– "))
        is_num    = bool(re.match(r'^\d+[\.\)]\s', p))
        is_checkbox = False
        is_checked = False
        pfx = ""
        bul_ind = 0

        if p.startswith(("- [ ]", "- [x]", "- [X]")):
            is_checkbox = True
            is_checked = "x" in p[:6] or "X" in p[:6]
            p = p[5:].strip()
            bul_ind = 16
        elif p.startswith(("[ ]", "[x]", "[X]")):
            is_checkbox = True
            is_checked = "x" in p[:4] or "X" in p[:4]
            p = p[3:].strip()
            bul_ind = 16
        elif is_bullet:
            pfx = "•"
            p   = p[2:].strip()
            bul_ind = 14
        elif is_num:
            m   = re.match(r'^(\d+[\.\)])\s', p)
            pfx = m.group(1)
            p   = p[m.end():].strip()
            bul_ind = 18
        else:
            bul_ind = 0

        avail_w = avail - bul_ind
        words   = p.split()
        lines   = []
        cur_w, cur_words = 0.0, []
        sp_w = c.stringWidth(" ", fn, font_sz)

        for word in words:
            cl  = _clean(word)
            wfn = FONT_BOLD if word.startswith("**") else fn
            ww  = c.stringWidth(cl, wfn, font_sz)
            gap = sp_w if cur_words else 0.0
            if cur_w + gap + ww <= avail_w:
                cur_words.append(word)
                cur_w += gap + ww
            else:
                if cur_words: lines.append(" ".join(cur_words))
                cur_words, cur_w = [word], ww
        if cur_words: lines.append(" ".join(cur_words))

        # Detect paragraph language using the first real character
        # (ignores leading punctuation/numbers so English bullets don't become RTL)
        para_is_rtl = _is_rtl_para(p)
        st.is_rtl = para_is_rtl

        # Compute x boundaries once per paragraph
        x_base = MARGIN + indent
        avail_right = x_base + avail   # right bound of available area

        for li, line_str in enumerate(lines):
            if st.y < BOTTOM + lh: st.new_page(c)

            # Prefix (bullet / number / checkbox)
            if li == 0:
                if is_checkbox:
                    c.setStrokeColor(C_GOLD)
                    c.setLineWidth(1.0)
                    if para_is_rtl:
                        px = avail_right - bul_ind + 2
                    else:
                        px = x_base + 2
                    c.roundRect(px, st.y - 1, 9, 9, 1.5, fill=0, stroke=1)
                    if is_checked:
                        c.setStrokeColor(C_GOLD)
                        c.setLineWidth(1.2)
                        c.line(px + 2, st.y + 3, px + 4, st.y + 1)
                        c.line(px + 4, st.y + 1, px + 7, st.y + 6)
                elif pfx:
                    pfx_d, _ = _bidi(pfx)
                    c.setFillColor(C_GOLD)
                    c.setFont(FONT_BOLD, font_sz + 1)
                    if para_is_rtl:
                        px = avail_right - bul_ind + 2
                    else:
                        px = x_base + 2
                    c.drawString(px, st.y, pfx_d)

            # Highlights
            hl = [m.group(1) for m in re.finditer(r'<mark>(.*?)</mark>', line_str)]
            hl += [m.group(1) for m in re.finditer(r'<span[^>]*>(.*?)</span>', line_str)]

            clean_l = _clean(line_str)
            disp, _ = _bidi(clean_l)
            is_bold_line  = line_str.count("**") >= 2
            dfn = FONT_BOLD if (bold or is_bold_line) else fn

            c.setFont(dfn, font_sz)
            lw = c.stringWidth(disp, dfn, font_sz)
            # Align text to left for LTR, right for RTL (with a safe 6pt buffer for Arabic stringWidth underestimation)
            if para_is_rtl:
                x = avail_right - bul_ind - lw - 6
            else:
                x = x_base + bul_ind

            # Draw highlighter rectangles
            for hw in hl:
                hd, _ = _bidi(hw)
                idx = display_idx = 0
                while True:
                    idx = disp.find(hd, display_idx)
                    if idx == -1: break
                    prefix_w = c.stringWidth(disp[:idx], dfn, font_sz)
                    hw_w     = c.stringWidth(hd, dfn, font_sz)
                    c.setFillColor(C_MARK)
                    c.roundRect(x + prefix_w - 1, st.y - 2, hw_w + 2, font_sz + 4, 2, fill=1, stroke=0)
                    display_idx = idx + len(hd)

            c.setFillColor(color)
            c.drawString(x, st.y, disp)
            st.y -= lh

# ── Section Heading (H1) ──────────────────────────────────────────────────────
def _h1(c, st, title):
    t = _clean(str(title))[:65]
    is_rtl = _has_ar(t)
    st.is_rtl = is_rtl
    if st.y - 50 < BOTTOM: st.new_page(c)
    st.y -= 14
    td, _ = _bidi(t)
    c.setFont(FONT_BOLD, 15)
    tw = c.stringWidth(td, FONT_BOLD, 15)
    # Background banner
    c.setFillColor(C_BLUE)
    c.roundRect(MARGIN, st.y - 6, TXT_W, 26, 5, fill=1, stroke=0)
    c.setFillColor(HexColor("#FDFBF7"))
    if is_rtl:
        c.drawString(W - MARGIN - tw - 22, st.y + 4, td)
    else:
        c.drawString(MARGIN + 22, st.y + 4, td)
    st.y -= 34

# ── Sub-heading (H2) ──────────────────────────────────────────────────────────
def _h2(c, st, title):
    t = _clean(str(title))[:70]
    is_rtl = _has_ar(t)
    st.is_rtl = is_rtl
    if st.y - 40 < BOTTOM: st.new_page(c)
    st.y -= 8
    td, _ = _bidi(t)
    c.setFont(FONT_BOLD, 13)
    tw = c.stringWidth(td, FONT_BOLD, 13)
    c.setFillColor(C_GOLD)
    if is_rtl:
        c.drawString(W - MARGIN - tw - 20, st.y, td)
        c.setStrokeColor(C_GOLD)
        c.setLineWidth(1.2)
        c.line(W - MARGIN - tw - 22, st.y - 3, W - MARGIN - 16, st.y - 3)
    else:
        c.drawString(MARGIN + 20, st.y, td)
        c.setStrokeColor(C_GOLD)
        c.setLineWidth(1.2)
        c.line(MARGIN + 16, st.y - 3, MARGIN + tw + 22, st.y - 3)
    st.y -= 22

# ── H3 (Exam Summary Header) ─────────────────────────────────────────────────
def _h3(c, st, title):
    t = _clean(str(title))[:80]
    is_rtl = _has_ar(t)
    st.is_rtl = is_rtl
    if st.y - 30 < BOTTOM: st.new_page(c)
    st.y -= 6
    td, _ = _bidi(t)
    c.setFont(FONT_BOLD, 11.5)
    tw = c.stringWidth(td, FONT_BOLD, 11.5)
    c.setFillColor(C_TEAL)
    if is_rtl:
        c.drawString(W - MARGIN - tw - 18, st.y, td)
    else:
        c.drawString(MARGIN + 18, st.y, td)
    st.y -= 20

# ── Cornell-Style Note Box ───────────────────────────────────────────────────
def _cornell_box(c, st, text, kind="note"):
    """Renders a beautiful Cornell-style note box with a cue column and a note column."""
    is_arabic_text = _has_ar(text)
    st.is_rtl = is_arabic_text
    
    # Define labels without emojis
    labels = {
        "exam": ("Exam Alert", "تنبيه هام"),
        "note": ("Key Note", "ملاحظة هامة"),
        "remember": ("Remember", "تذكّر")
    }
    lbl_en, lbl_ar = labels.get(kind, ("Note", "ملاحظة"))
    
    # Split text into cue and body at the first colon
    if ":" in text:
        parts = text.split(":", 1)
        cue = parts[0].strip()
        # Clean any emojis or markers from cue
        cue = re.sub(r'^[💡🧪⚡✅📌⚠📊✨🔒\-•\s]+', '', cue)
        body = parts[1].strip()
    else:
        cue = lbl_ar if is_arabic_text else lbl_en
        body = text.strip()

    # Clean the callout body of leading emojis or duplicate key prefixes (e.g. ملاحظة:)
    body = re.sub(r'^[💡🧪⚡✅📌⚠📊✨🔒\-•\s]+', '', body).strip()
    body = re.sub(r'^(?:ملاحظة|توضيح|تنبيه|تذكّر|تذكر|هام للامتحان|Note|Remember|Important|Warning|Tip|Key Point)\s*(?:هامة|هام)?\s*[:：]?\s*', '', body, flags=re.IGNORECASE).strip()

    # Estimate height based on body text length
    est_lines = max(2, len(body) // 40 + 2)
    box_h = est_lines * 19 + 28
    if st.y - box_h < BOTTOM: st.new_page(c)
    
    box_y = st.y - box_h
    
    # Draw Cornell box background
    c.setFillColor(C_BOX_BG)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=1, stroke=0)
    c.setStrokeColor(C_BOX_BD)
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=0, stroke=1)
    
    # Colors for dividers and highlights
    colors = {"exam": C_GOLD, "note": C_BLUE, "remember": C_TEAL}
    theme_clr = colors.get(kind, C_BLUE)
    
    # Cornell columns division: 28% for Cue column, 72% for Notes
    cue_w = TXT_W * 0.28
    notes_w = TXT_W * 0.72
    
    # Draw vertical divider and cue/notes content
    c.setStrokeColor(C_DIV)
    c.setLineWidth(0.8)
    
    if is_arabic_text:
        # Arabic: Cue is on the right, Notes on the left
        divider_x = W - MARGIN - cue_w
        c.line(divider_x, box_y + 4, divider_x, box_y + box_h - 4)
        
        # Draw side colored bar on right edge
        c.setFillColor(theme_clr)
        c.rect(W - MARGIN - 4, box_y + 4, 3, box_h - 8, fill=1, stroke=0)
        
        # Draw Cue text (bold) in the Cue column
        c.setFont(FONT_BOLD, 9.5)
        c.setFillColor(theme_clr)
        cue_d, _ = _bidi(cue)
        c.drawRightString(W - MARGIN - 12, box_y + box_h - 18, cue_d)
        
        # Draw Body/Notes in the Notes column (left side)
        inner = _S(st.page_num, st.student_name)
        inner.y = box_y + box_h - 18
        _para(c, body, inner, font_sz=9, lh=17, color=C_BODY, indent=0, max_w=notes_w - 12)
        
    else:
        # English: Cue is on the left, Notes on the right
        divider_x = MARGIN + cue_w
        c.line(divider_x, box_y + 4, divider_x, box_y + box_h - 4)
        
        # Draw side colored bar on left edge
        c.setFillColor(theme_clr)
        c.rect(MARGIN + 1, box_y + 4, 3, box_h - 8, fill=1, stroke=0)
        
        # Draw Cue text (bold) in the Cue column
        c.setFont(FONT_BOLD, 9.5)
        c.setFillColor(theme_clr)
        cue_d, _ = _bidi(cue)
        c.drawString(MARGIN + 12, box_y + box_h - 18, cue_d)
        
        # Draw Body/Notes in the Notes column (right side)
        inner = _S(st.page_num, st.student_name)
        inner.y = box_y + box_h - 18
        _para(c, body, inner, font_sz=9, lh=17, color=C_BODY, indent=cue_w + 8, max_w=TXT_W - 8)
        
    st.y = box_y - 12

# ── Code & Formula Card ──────────────────────────────────────────────────────


def _extract_concepts(continuous_markdown, cheat_sheet):
    concepts = []
    if cheat_sheet:
        for item in cheat_sheet:
            term = item.get("term", "").strip()
            if term:
                concepts.append(term)
    if len(concepts) < 5 and continuous_markdown:
        for line in continuous_markdown.split("\n"):
            line = _strip_ctrl(line).strip()
            if line.startswith("## ") or line.startswith("### "):
                title = line.lstrip("#").strip()
                title = _clean(title)
                if title and title not in concepts:
                    concepts.append(title)
                    if len(concepts) >= 6:
                        break
    seen = set()
    final_concepts = []
    for c in concepts:
        c_clean = re.sub(r'^[💡🧪⚡✅📌⚠📊✨🔒\-•\s]+', '', c).strip()
        if c_clean and c_clean not in seen:
            seen.add(c_clean)
            final_concepts.append(c_clean)
            
    if not final_concepts:
        final_concepts = ["شرح المفاهيم", "المصطلحات الهامة", "تطبيقات ونماذج"]
        
    return final_concepts[:5]

def _draw_visual_mind_map(c, st, lecture_title, continuous_markdown, cheat_sheet):
    _bg(c, is_rtl=st.main_rtl)
    st._footer(c)
    st.y = H - MARGIN - 18
    
    title_text = "خريطة المفاهيم البصرية" if st.main_rtl else "Visual Concept Mind Map"
    _h1(c, st, title_text)
    
    sub_text = "مخطط مفاهيمي يوضح ترابط هيكل المحاضرة وعناصرها الأساسية" if st.main_rtl else "Concept diagram showing relationships between the main lecture sections"
    sub_d, _ = _bidi(sub_text)
    c.setFont(FONT_REG, 9)
    c.setFillColor(C_MUTED)
    sw = c.stringWidth(sub_d, FONT_REG, 9)
    if st.main_rtl:
        c.drawString(W - MARGIN - sw - 8, st.y, sub_d)
    else:
        c.drawString(MARGIN + 8, st.y, sub_d)
    st.y -= 28
    
    concepts = _extract_concepts(continuous_markdown, cheat_sheet)
    num_nodes = len(concepts)
    
    board_h = 320
    board_y = st.y - board_h - 10
    c.setFillColor(HexColor("#FDFBF7"))
    c.roundRect(MARGIN, board_y, TXT_W, board_h, 8, fill=1, stroke=0)
    c.setStrokeColor(C_BOX_BD)
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, board_y, TXT_W, board_h, 8, fill=0, stroke=1)
    
    cx = W / 2
    cy = board_y + board_h / 2
    
    positions = []
    if num_nodes == 1:
        positions = [(cx, cy + 90)]
    elif num_nodes == 2:
        positions = [(cx - 130, cy), (cx + 130, cy)]
    elif num_nodes == 3:
        positions = [(cx, cy + 90), (cx - 130, cy - 50), (cx + 130, cy - 50)]
    elif num_nodes == 4:
        positions = [(cx - 130, cy + 70), (cx + 130, cy + 70), (cx - 130, cy - 70), (cx + 130, cy - 70)]
    else:
        positions = [
            (cx, cy + 95),
            (cx - 140, cy + 25),
            (cx + 140, cy + 25),
            (cx - 100, cy - 75),
            (cx + 100, cy - 75)
        ]
        
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(1.2)
    for px, py in positions:
        c.line(cx, cy, px, py)
        c.setFillColor(C_GOLD)
        c.circle(px, py, 3, fill=1, stroke=0)
        
    c.setFillColor(C_BLUE)
    c.setStrokeColor(HexColor("#1A2B5E"))
    c.setLineWidth(1.5)
    
    center_text = _clean(lecture_title)
    if len(center_text) > 28:
        center_text = center_text[:25] + "..."
    ct_d, _ = _bidi(center_text)
    
    ct_w = c.stringWidth(ct_d, FONT_BOLD, 10.5)
    box_w = max(110, ct_w + 24)
    box_h = 36
    
    c.roundRect(cx - box_w/2, cy - box_h/2, box_w, box_h, 6, fill=1, stroke=1)
    
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 9.5)
    c.drawString(cx - ct_w/2, cy - 3.5, ct_d)
    
    for idx, (px, py) in enumerate(positions):
        concept_text = concepts[idx]
        if len(concept_text) > 24:
            concept_text = concept_text[:21] + "..."
        con_d, _ = _bidi(concept_text)
        
        con_w = c.stringWidth(con_d, FONT_BOLD, 9)
        n_w = max(80, con_w + 16)
        n_h = 24
        
        c.setFillColor(HexColor("#F7F1E8"))
        c.setStrokeColor(C_BOX_BD)
        c.setLineWidth(1.0)
        c.roundRect(px - n_w/2, py - n_h/2, n_w, n_h, 4, fill=1, stroke=1)
        
        c.setFillColor(C_BODY)
        c.setFont(FONT_BOLD, 8)
        c.drawString(px - con_w/2, py - 2.5, con_d)
        
    st.y = board_y - 20

# ── Callout Box (Backwards Compatibility Wrapper) ────────────────────────────
def _callout(c, st, text, kind="note"):
    _cornell_box(c, st, text, kind)

# ── Table Renderer ────────────────────────────────────────────────────────────
def _table(c, st, raw):
    rows_raw = [r.strip() for r in raw.strip().split("\n") if "|" in r]
    rows     = [r for r in rows_raw if not re.match(r'^\|[\s\-|:]+\|$', r)]
    if not rows: return

    parsed = [[cell.strip() for cell in r.strip("|").split("|")] for r in rows]
    ncols  = max(len(r) for r in parsed)
    col_w  = TXT_W / ncols
    row_h  = 23
    tbl_h  = len(parsed) * row_h + 4

    if st.y - tbl_h < BOTTOM: st.new_page(c)

    top = st.y
    for ri, row in enumerate(parsed):
        ry = top - (ri + 1) * row_h
        bg = HexColor("#E8DDD0") if ri == 0 else (HexColor("#FDFBF7") if ri % 2 == 0 else HexColor("#F7F1E8"))
        c.setFillColor(bg)
        c.rect(MARGIN, ry, TXT_W, row_h, fill=1, stroke=0)
        c.setStrokeColor(C_DIV)
        c.setLineWidth(0.4)
        c.rect(MARGIN, ry, TXT_W, row_h, fill=0, stroke=1)

        fn = FONT_BOLD if ri == 0 else FONT_REG
        fc = C_BLUE   if ri == 0 else C_BODY
        for ci, cell in enumerate(row[:ncols]):
            cx = MARGIN + ci * col_w
            cd, rtl = _bidi(str(cell)[:35])
            c.setFillColor(fc)
            c.setFont(fn, 9)
            cw = c.stringWidth(cd, fn, 9)
            cx_text = cx + col_w - cw - 6 if rtl else cx + 6
            c.drawString(cx_text, ry + 7, cd)
            c.setStrokeColor(C_DIV)
            c.line(cx + col_w, ry, cx + col_w, ry + row_h)

    st.y = top - tbl_h - 10

# ── Horizontal Divider ────────────────────────────────────────────────────────
def _divider(c, st):
    if st.y - 20 < BOTTOM: st.new_page(c)
    mid_y = st.y - 8
    c.setStrokeColor(C_DIV)
    c.setLineWidth(0.6)
    c.line(MARGIN + 20, mid_y, W - MARGIN - 20, mid_y)
    c.setFillColor(C_GOLD)
    c.circle(W / 2, mid_y, 3, fill=1, stroke=0)
    st.y -= 20

# ── Markdown Renderer ─────────────────────────────────────────────────────────
def _render(c, st, text):
    TABLE_PAT = re.compile(r'((?:\|[^\n]+\|\n?){2,})', re.MULTILINE)
    tables    = []

    def _save(m):
        tables.append(m.group(0))
        return "\n[[TABLE]]\n"

    txt = TABLE_PAT.sub(_save, text)
    ti  = iter(tables)

    lines_list = txt.split("\n")
    in_code_block = False
    code_lines = []
    code_lang = ""

    i = 0
    while i < len(lines_list):
        seg = lines_list[i]
        # Strip invisible Unicode control chars FIRST so # detection works correctly
        s = _strip_ctrl(seg).strip()
        
        if not s:
            if in_code_block:
                code_lines.append("")
            i += 1
            continue

        if s.startswith("```"):
            if in_code_block:
                in_code_block = False
                code_lines = []
            else:
                in_code_block = True
                code_lang = s[3:].strip()
            i += 1
            continue

        if in_code_block:
            i += 1
            continue

        if s == "[[TABLE]]":
            try: _table(c, st, next(ti))
            except StopIteration: pass
            i += 1
            continue

        if s.startswith("---") or s.startswith("===") or s.startswith("***"):
            _divider(c, st)
            i += 1
            continue

        # Count leading # for headings, supporting optional bullets (*, -, •), checkbox, or bold (**) prefixes
        heading_match = re.match(r'^(?:[-*•\s]|\*\*|\[\s\]|\[[xX]\])*\s*(#{1,6})\s*(.*)$', s)
        if heading_match:
            level = len(heading_match.group(1))
            body = heading_match.group(2).strip()
            # Clear remaining hashes/bold stars/bullets from heading body
            body = body.lstrip("#*•- \t").rstrip("#* \t")
        else:
            level = 0
            body = s

        if level == 1:
            _h1(c, st, body)
        elif level == 2:
            _h2(c, st, body)
        elif level == 3:
            _h3(c, st, body)
        else:
            is_exam    = any(k in s for k in ["هام للامتحان", "⚠", "امتحان", "مهم", "ركز",
                                               "Exam Alert", "EXAM", "Important:", "WARNING:"])
            is_note    = any(k in s for k in ["💡", "ملاحظة", "توضيح",
                                               "Note:", "NOTE:", "Tip:", "TIP:"])
            is_rem     = any(k in s for k in ["📌", "تذكّر", "تذكر",
                                               "Remember:", "REMEMBER:", "Key Point:"])

            if is_exam and len(body) < 500:
                _cornell_box(c, st, body, kind="exam")
            elif is_rem and len(body) < 500:
                _cornell_box(c, st, body, kind="remember")
            elif is_note and len(body) < 500:
                _cornell_box(c, st, body, kind="note")
            else:
                _para(c, body, st)
        i += 1

# ── Cover Page ────────────────────────────────────────────────────────────────
def _cover(c, title, student_name):
    # Cover is drawn RTL (Arabic platform base style) but cleaned of emojis
    _bg(c, is_rtl=True)

    # Top decorative header bar
    c.setFillColor(C_COVER_H)
    c.rect(0, H - 200, W, 200, fill=1, stroke=0)
    c.setFillColor(C_GOLD)
    c.rect(0, H - 204, W, 4, fill=1, stroke=0)

    # Brand name
    brand, _ = _bidi("دفتر الطالب المتفوق الجامعي")
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 15)
    bw = c.stringWidth(brand, FONT_BOLD, 15)
    c.drawString(W / 2 - bw / 2, H - 100, brand)

    sub, _ = _bidi("منصة لمّاح — ملازم دراسية احترافية للبيع")
    c.setFont(FONT_REG, 10)
    sw = c.stringWidth(sub, FONT_REG, 10)
    c.setFillColor(HexColor("#FCD34D"))
    c.drawString(W / 2 - sw / 2, H - 130, sub)

    # Lecture Title
    c.setFillColor(C_BODY)
    c.setFont(FONT_BOLD, 20)
    clean_t = _clean(str(title))
    td, _ = _bidi(clean_t)
    tw = c.stringWidth(td, FONT_BOLD, 20)
    c.drawString(W / 2 - tw / 2, H - 290, td)

    # Gold underline under title
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(1.5)
    c.line(W / 2 - 100, H - 300, W / 2 + 100, H - 300)

    # Metadata box
    bw2, bh2 = 330, 170
    bx = W / 2 - bw2 / 2
    by = H / 2 - 130
    c.setFillColor(C_BOX_BG)
    c.roundRect(bx, by, bw2, bh2, 12, fill=1, stroke=0)
    c.setStrokeColor(C_BOX_BD)
    c.setLineWidth(0.8)
    c.roundRect(bx, by, bw2, bh2, 12, fill=0, stroke=1)
    
    # Right accent bar
    c.setFillColor(C_BLUE)
    c.rect(bx + bw2 - 5, by + 10, 4, bh2 - 20, fill=1, stroke=0)

    rows = [
        ("إعداد الطالب:", student_name),
        ("المنصة الناشرة:", "منصة لمّاح الرقمية"),
        ("حقوق الملكية:", "محفوظة للبائع والمنصة"),
    ]
    for i, (lbl, val) in enumerate(rows):
        row_y = by + bh2 - 45 - i * 42
        lbl_d, _ = _bidi(lbl)
        val_d, _ = _bidi(val)
        c.setFont(FONT_BOLD, 10.5)
        c.setFillColor(C_BLUE)
        c.drawRightString(bx + bw2 - 14, row_y, lbl_d)
        c.setFont(FONT_REG, 10)
        c.setFillColor(C_BODY)
        c.drawRightString(bx + bw2 - 14, row_y - 17, val_d)

    # Bottom note
    note, _ = _bidi("صُنع بكل حب واحترافية بواسطة محرك لمّاح الذكي")
    c.setFillColor(C_MUTED)
    c.setFont(FONT_REG, 8.5)
    nw = c.stringWidth(note, FONT_REG, 8.5)
    c.drawString(W / 2 - nw / 2, 42, note)

    c.showPage()

# ── Cheat Sheet Page ─────────────────────────────────────────────────────────
def _draw_cheat_sheet(c, st, items: list):
    """Renders a 'زبدة ليلة الامتحان' cheat sheet table."""
    if not items: return

    # Detect cheat sheet language
    is_arabic_cheat = any(_has_ar(item.get("term", "")) or _has_ar(item.get("brief_definition", "")) for item in items)
    st.is_rtl = is_arabic_cheat

    # New page with section header
    st.new_page(c)
    st.y -= 6

    # Section banner (No emojis)
    c.setFillColor(HexColor("#1B3A8A"))
    c.rect(MARGIN, st.y - 34, TXT_W, 34, fill=1, stroke=0)
    c.setFillColor(HexColor("#FCD34D"))
    c.rect(MARGIN, st.y - 38, TXT_W, 4, fill=1, stroke=0)
    
    title_text = "خلاصة ليلة الامتحان" if is_arabic_cheat else "Exam Cheat Sheet"
    title_d, _ = _bidi(title_text)
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 14)
    tw = c.stringWidth(title_d, FONT_BOLD, 14)
    c.drawString(W / 2 - tw / 2, st.y - 24, title_d)
    st.y -= 52

    sub_text = "جميع المصطلحات والقوانين المهمة — راجعها قبل الامتحان مباشرةً" if is_arabic_cheat else "Key terms and formulas to review right before the exam"
    sub_d, _ = _bidi(sub_text)
    c.setFont(FONT_REG, 9)
    c.setFillColor(C_MUTED)
    sw = c.stringWidth(sub_d, FONT_REG, 9)
    c.drawString(W / 2 - sw / 2, st.y, sub_d)
    st.y -= 20

    # Table header row
    col_term = TXT_W * 0.35
    col_def  = TXT_W * 0.65
    row_h = 22

    # Draw header
    if st.y - row_h < BOTTOM: st.new_page(c)
    c.setFillColor(HexColor("#D9CBBA"))
    c.rect(MARGIN, st.y - row_h, TXT_W, row_h, fill=1, stroke=0)
    c.setStrokeColor(C_DIV)
    c.setLineWidth(0.4)
    c.rect(MARGIN, st.y - row_h, TXT_W, row_h, fill=0, stroke=1)
    c.line(MARGIN + col_term, st.y - row_h, MARGIN + col_term, st.y)
    c.setFont(FONT_BOLD, 10)
    c.setFillColor(C_BLUE)
    
    th1 = "المصطلح / القانون" if is_arabic_cheat else "Term / Formula"
    th2 = "التعريف المختصر" if is_arabic_cheat else "Brief Definition"
    th1_d, th1_rtl = _bidi(th1)
    th2_d, th2_rtl = _bidi(th2)
    tw1 = c.stringWidth(th1_d, FONT_BOLD, 10)
    tw2 = c.stringWidth(th2_d, FONT_BOLD, 10)
    
    x_h1 = MARGIN + col_term - tw1 - 6 if th1_rtl else MARGIN + 6
    x_h2 = MARGIN + col_term + col_def - tw2 - 8 if th2_rtl else MARGIN + col_term + 6
    c.drawString(x_h1, st.y - row_h + 7, th1_d)
    c.drawString(x_h2, st.y - row_h + 7, th2_d)
    st.y -= row_h

    # Draw items
    for i, item in enumerate(items):
        if st.y - row_h < BOTTOM: st.new_page(c)
        bg = HexColor("#FDFBF7") if i % 2 == 0 else HexColor("#F7F1E8")
        c.setFillColor(bg)
        c.rect(MARGIN, st.y - row_h, TXT_W, row_h, fill=1, stroke=0)
        c.setStrokeColor(C_DIV)
        c.setLineWidth(0.4)
        c.rect(MARGIN, st.y - row_h, TXT_W, row_h, fill=0, stroke=1)
        c.line(MARGIN + col_term, st.y - row_h, MARGIN + col_term, st.y)

        term = str(item.get("term", ""))[:45]
        defn = str(item.get("brief_definition", ""))[:90]

        # Highlight term column background
        c.setFillColor(C_MARK)
        c.roundRect(MARGIN + 3, st.y - row_h + 3, col_term - 8, row_h - 6, 3, fill=1, stroke=0)

        # Draw Term
        c.setFillColor(C_BODY)
        c.setFont(FONT_BOLD, 9)
        td, term_rtl = _bidi(term)
        tw3 = c.stringWidth(td, FONT_BOLD, 9)
        x_term = MARGIN + col_term - tw3 - 6 if term_rtl else MARGIN + 6
        c.drawString(x_term, st.y - row_h + 7, td)

        # Draw Definition
        c.setFont(FONT_REG, 9)
        c.setFillColor(C_BODY)
        dd, def_rtl = _bidi(defn)
        dw = c.stringWidth(dd, FONT_REG, 9)
        x_def = MARGIN + col_term + col_def - dw - 8 if def_rtl else MARGIN + col_term + 6
        c.drawString(x_def, st.y - row_h + 7, dd)
        st.y -= row_h

# ── MCQ Quiz Page ──────────────────────────────────────────────────────────────
def _draw_mcqs(c, st, mcqs: list):
    """Renders 'اختبر نفسك' multiple choice questions."""
    if not mcqs: return

    # Detect language of questions
    is_arabic_mcqs = any(_has_ar(q.get("question", "")) for q in mcqs)
    st.is_rtl = is_arabic_mcqs

    st.new_page(c)
    st.y -= 6

    # Section banner (No emojis)
    c.setFillColor(C_TEAL)
    c.rect(MARGIN, st.y - 34, TXT_W, 34, fill=1, stroke=0)
    c.setFillColor(C_GOLD)
    c.rect(MARGIN, st.y - 38, TXT_W, 4, fill=1, stroke=0)
    
    title_text = "اختبر نفسك — هل أنت جاهز للامتحان؟" if is_arabic_mcqs else "Self-Test: Are you ready for the exam?"
    title_d, _ = _bidi(title_text)
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 14)
    tw = c.stringWidth(title_d, FONT_BOLD, 14)
    c.drawString(W / 2 - tw / 2, st.y - 24, title_d)
    st.y -= 52

    answers = []

    for i, q in enumerate(mcqs):
        if st.y - 100 < BOTTOM: st.new_page(c)

        # Question box
        q_text = f"{i+1}. {q.get('question', '')}"
        q_lines = max(1, len(q_text) // 55 + 1)
        q_box_h = q_lines * 20 + 16

        c.setFillColor(HexColor("#EBF5F3"))
        c.roundRect(MARGIN, st.y - q_box_h, TXT_W, q_box_h, 6, fill=1, stroke=0)
        c.setStrokeColor(C_TEAL)
        c.setLineWidth(0.8)
        c.roundRect(MARGIN, st.y - q_box_h, TXT_W, q_box_h, 6, fill=0, stroke=1)
        
        qd, is_q_rtl = _bidi(q_text[:120])
        c.setFont(FONT_BOLD, 10.5)
        c.setFillColor(HexColor("#0F766E"))
        qw = c.stringWidth(qd, FONT_BOLD, 10.5)
        
        if is_q_rtl:
            # Bar on the right
            c.setFillColor(C_TEAL)
            c.rect(W - MARGIN - 5, st.y - q_box_h + 4, 4, q_box_h - 8, fill=1, stroke=0)
            c.drawString(W - MARGIN - qw - 12, st.y - q_box_h + 10, qd)
        else:
            # Bar on the left
            c.setFillColor(C_TEAL)
            c.rect(MARGIN + 1, st.y - q_box_h + 4, 4, q_box_h - 8, fill=1, stroke=0)
            c.drawString(MARGIN + 12, st.y - q_box_h + 10, qd)
            
        st.y -= q_box_h + 4

        # Choices
        choices = q.get("choices", [])
        for ch in choices:
            if st.y - 22 < BOTTOM: st.new_page(c)
            ch_d, is_ch_rtl = _bidi(str(ch)[:100])
            c.setFont(FONT_REG, 9.5)
            c.setFillColor(C_BODY)
            chw = c.stringWidth(ch_d, FONT_REG, 9.5)
            
            if is_ch_rtl:
                # Circle on the right, text right-aligned
                c.setFillColor(C_DIV)
                c.circle(W - MARGIN - 10, st.y - 7, 4, fill=1, stroke=0)
                c.setFillColor(C_BODY)
                c.drawString(W - MARGIN - chw - 20, st.y - 13, ch_d)
            else:
                # Circle on the left, text left-aligned
                c.setFillColor(C_DIV)
                c.circle(MARGIN + 10, st.y - 7, 4, fill=1, stroke=0)
                c.setFillColor(C_BODY)
                c.drawString(MARGIN + 20, st.y - 13, ch_d)
            st.y -= 22

        answers.append(f"{i+1}. {q.get('answer', '')}")
        st.y -= 10

    # Answers section (No emojis)
    if answers:
        st.y -= 14
        if st.y - 30 < BOTTOM: st.new_page(c)
        ans_lbl = "مفتاح الإجابات:" if is_arabic_mcqs else "Answer Key:"
        ans_lbl_d, _ = _bidi(ans_lbl)
        c.setFont(FONT_BOLD, 8)
        c.setFillColor(C_MUTED)
        alw = c.stringWidth(ans_lbl_d, FONT_BOLD, 8)
        
        ans_text = "   |   ".join(answers)
        ans_d, _ = _bidi(ans_text)
        c.setFont(FONT_REG, 7.5)
        c.setFillColor(C_MUTED)
        adw = c.stringWidth(ans_d, FONT_REG, 7.5)
        
        if is_arabic_mcqs:
            c.drawString(W - MARGIN - alw - 8, st.y, ans_lbl_d)
            st.y -= 16
            c.drawString(W - MARGIN - adw - 8, st.y, ans_d)
        else:
            c.drawString(MARGIN + 8, st.y, ans_lbl_d)
            st.y -= 16
            c.drawString(MARGIN + 8, st.y, ans_d)
        st.y -= 20

    # Self-Evaluation Mastery Checklist
    if st.y - 120 < BOTTOM: st.new_page(c)
    st.y -= 14
    chk_title = "قائمة التقييم الذاتي والاستيعاب:" if is_arabic_mcqs else "Self-Evaluation & Mastery Checklist:"
    chk_title_d, _ = _bidi(chk_title)
    c.setFont(FONT_BOLD, 11)
    c.setFillColor(C_BLUE)
    if is_arabic_mcqs:
        ctw = c.stringWidth(chk_title_d, FONT_BOLD, 11)
        c.drawString(W - MARGIN - ctw - 8, st.y, chk_title_d)
    else:
        c.drawString(MARGIN + 8, st.y, chk_title_d)
    st.y -= 20
    
    chk_items = [
        "استوعبت المفاهيم الأساسية التي وردت في هذه المحاضرة." if is_arabic_mcqs else "I understand the core concepts covered in this lecture.",
        "أستطيع شرح أوجه المقارنة بين المصطلحات بوضوح." if is_arabic_mcqs else "I can explain the difference between the main comparison aspects.",
        "قمت بحل الأسئلة التدريبية بنجاح." if is_arabic_mcqs else "I have successfully solved all practice questions."
    ]
    for item in chk_items:
        if st.y - 20 < BOTTOM: st.new_page(c)
        c.setStrokeColor(C_GOLD)
        c.setLineWidth(1.0)
        if is_arabic_mcqs:
            px = W - MARGIN - 20
            c.roundRect(px, st.y - 1, 9, 9, 1.5, fill=0, stroke=1)
            item_d, _ = _bidi(item)
            itw = c.stringWidth(item_d, FONT_REG, 9)
            # Draw Arabic text right-aligned with a neat 12pt gap from the checkbox
            c.drawString(W - MARGIN - 32 - itw, st.y - 1, item_d)
        else:
            px = MARGIN + 8
            c.roundRect(px, st.y - 1, 9, 9, 1.5, fill=0, stroke=1)
            item_d, _ = _bidi(item)
            c.drawString(MARGIN + 24, st.y - 1, item_d)
        st.y -= 20

# ── Comparison Tables Page ────────────────────────────────────────────────────
def _draw_comparisons(c, st, comparisons: list):
    """Renders smart comparison tables extracted by the AI."""
    if not comparisons: return

    is_arabic_comp = any(_has_ar(comp.get("title", "")) for comp in comparisons)
    st.is_rtl = is_arabic_comp

    st.new_page(c)
    st.y -= 6

    # Section banner (No emojis)
    c.setFillColor(C_GOLD)
    c.rect(MARGIN, st.y - 34, TXT_W, 34, fill=1, stroke=0)
    c.setFillColor(C_BLUE)
    c.rect(MARGIN, st.y - 38, TXT_W, 4, fill=1, stroke=0)
    
    title_text = "جداول المقارنة الذكية" if is_arabic_comp else "Smart Comparison Tables"
    title_d, _ = _bidi(title_text)
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 14)
    tw = c.stringWidth(title_d, FONT_BOLD, 14)
    c.drawString(W / 2 - tw / 2, st.y - 24, title_d)
    st.y -= 52

    for comp in comparisons:
        comp_title = str(comp.get("title", "مقارنة"))
        headers    = comp.get("headers", [])
        rows       = comp.get("rows", [])
        if not rows: continue

        # Comparison sub-title
        if st.y - 40 < BOTTOM: st.new_page(c)
        _h2(c, st, comp_title)

        # Build full table: first column is the aspect, rest are values
        ncols   = 1 + len(headers)
        col_w   = TXT_W / ncols
        row_h   = 26
        tbl_h   = (len(rows) + 1) * row_h + 4

        if st.y - tbl_h < BOTTOM: st.new_page(c)

        top = st.y

        # Header row
        hy = top - row_h
        c.setFillColor(HexColor("#1B3A8A"))
        c.rect(MARGIN, hy, TXT_W, row_h, fill=1, stroke=0)
        c.setFont(FONT_BOLD, 9.5)
        c.setFillColor(HexColor("#FDFBF7"))

        # Aspect header
        aspect_lbl = "وجه المقارنة" if is_arabic_comp else "Comparison Aspect"
        aspect_lbl_d, is_asp_rtl = _bidi(aspect_lbl)
        alw = c.stringWidth(aspect_lbl_d, FONT_BOLD, 9.5)
        x_asp = MARGIN + col_w - alw - 6 if is_asp_rtl else MARGIN + 6
        c.drawString(x_asp, hy + 8, aspect_lbl_d)
        c.setStrokeColor(HexColor("#FDFBF7"))
        c.setLineWidth(0.3)
        c.line(MARGIN + col_w, hy, MARGIN + col_w, hy + row_h)

        for hi, hdr in enumerate(headers[:ncols - 1]):
            hx = MARGIN + (hi + 1) * col_w
            hd, is_hdr_rtl = _bidi(str(hdr)[:30])
            hw = c.stringWidth(hd, FONT_BOLD, 9.5)
            x_hdr = hx + col_w - hw - 6 if is_hdr_rtl else hx + 6
            c.drawString(x_hdr, hy + 8, hd)
            c.line(hx + col_w, hy, hx + col_w, hy + row_h)

        # Data rows
        for ri, row in enumerate(rows):
            ry = top - (ri + 2) * row_h
            if ry < BOTTOM: break
            bg = HexColor("#F0EBE3") if ri % 2 == 0 else HexColor("#FDFBF7")
            c.setFillColor(bg)
            c.rect(MARGIN, ry, TXT_W, row_h, fill=1, stroke=0)
            c.setStrokeColor(C_DIV)
            c.setLineWidth(0.4)
            c.rect(MARGIN, ry, TXT_W, row_h, fill=0, stroke=1)

            for ci, cell in enumerate(row[:ncols]):
                cx = MARGIN + ci * col_w
                fn = FONT_BOLD if ci == 0 else FONT_REG
                fc = C_BLUE   if ci == 0 else C_BODY
                cd, is_rtl = _bidi(str(cell)[:40])
                c.setFont(fn, 9)
                c.setFillColor(fc)
                cw = c.stringWidth(cd, fn, 9)
                cx_text = cx + col_w - cw - 6 if is_rtl else cx + 6
                c.drawString(cx_text, ry + 8, cd)
                c.setStrokeColor(C_DIV)
                c.line(cx + col_w, ry, cx + col_w, ry + row_h)

        st.y = top - (len(rows) + 1) * row_h - 18


# ── Main Entrypoint ───────────────────────────────────────────────────────────
def generate_aesthetic_notebook_pdf(
    output_path: str,
    continuous_markdown: str,
    student_name: str = "طالب متميز",
    lecture_title: str = "ملزمة دراسية ممتازة",
    cheat_sheet: list = None,
    mcqs: list = None,
    comparisons: list = None,
) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv = canvas.Canvas(output_path, pagesize=A4)

    # 1. Cover Page
    _cover(cv, lecture_title, student_name)

    # 2. Main Content Init
    is_main_rtl = _has_ar(continuous_markdown[:2000]) if continuous_markdown else True
    st = _S(page_num=2, student_name=student_name, is_rtl=is_main_rtl)

    # 2a. Visual Concept Mind Map (Page 2)
    _draw_visual_mind_map(cv, st, lecture_title, continuous_markdown, cheat_sheet)

    # 2b. Continuous Markdown (Main Content starting on Page 3)
    st.new_page(cv)
    st.y = H - MARGIN - 18
    _render(cv, st, continuous_markdown)

    # 3. Comparison Tables (inline with content flow)
    if comparisons:
        _draw_comparisons(cv, st, comparisons)

    # 4. MCQ Quiz Page
    if mcqs:
        _draw_mcqs(cv, st, mcqs)

    # 5. Cheat Sheet Page (Last Page)
    if cheat_sheet:
        _draw_cheat_sheet(cv, st, cheat_sheet)

    cv.save()
    logger.info(f"[NOTEBOOK] PDF generated → {output_path}")
    return output_path


def generate_preview_pdf(original_pdf_path, preview_pdf_path, max_pages=3):
    import fitz
    try:
        doc  = fitz.open(original_pdf_path)
        prev = fitz.open()
        prev.insert_pdf(doc, to_page=min(len(doc), max_pages) - 1)

        wm, _ = _bidi("نسخة للمعاينة — اشترِ الملزمة للحصول على النسخة الكاملة")
        for page in prev:
            r = page.rect
            page.insert_text(
                fitz.Point(r.width / 2 - 185, r.height / 2 + 60),
                wm, fontsize=22, color=(0.85, 0.25, 0.25),
                fontname="helv", fill_opacity=0.28
            )

        prev.save(preview_pdf_path)
        prev.close()
        doc.close()
        logger.info(f"[NOTEBOOK] Preview PDF → {preview_pdf_path}")
        return preview_pdf_path
    except Exception as e:
        logger.error(f"[NOTEBOOK] Preview failed: {e}")
        return original_pdf_path
