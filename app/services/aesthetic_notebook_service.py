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
from typing import Optional, List, Dict, Any, Union
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

def _clean(s):
    t = re.sub(r'\*\*|<mark>|</mark>|<span[^>]*>|</span>', '', str(s))
    # Strip emojis cleanly so ReportLab never renders missing glyph [] boxes
    t = re.sub(r'[\U00010000-\U0010ffff]', '', t)
    t = re.sub(r'[\u2600-\u27BF\uE000-\uF8FF]', '', t)
    t = re.sub(r'[\uFE00-\uFE0F]', '', t)
    t = re.sub(r'[\u200B-\u200F\u202A-\u202E]', '', t)
    return t.strip()

def _is_rtl_para(t, is_main_rtl=True):
    """Determine if a paragraph should be rendered as RTL.
    In Arabic or Bilingual documents, any paragraph containing Arabic text
    must have RTL base direction to protect parentheses and bullet points."""
    cleaned = _clean(t)
    if is_main_rtl:
        return _has_ar(cleaned)
    first_char = re.sub(r'^[\s\d\W]+', '', _strip_ctrl(cleaned))
    return bool(_AR.search(first_char[0])) if first_char else False

def _bidi(text: Any, base_dir: Optional[str] = None) -> tuple[str, bool]:
    if not text: return "", False
    text_clean = _strip_ctrl(_clean(text))
    if _has_ar(text_clean):
        try:
            reshaped = arabic_reshaper.reshape(text_clean)
            bd = base_dir if base_dir else ('R' if _has_ar(text_clean) else 'L')
            bidi_text = get_display(reshaped, base_dir=bd)
            if isinstance(bidi_text, bytes):
                bidi_text = bidi_text.decode('utf-8', errors='ignore')
            return _strip_ctrl(bidi_text), True
        except Exception:
            return text_clean, True
    return text_clean, False

def _has_keyword(s, keywords):
    for k in keywords:
        if k in ["💡", "⚠", "📌"]:
            if k in s:
                return True
        elif _has_ar(k):
            pattern = rf"(?<![\w\u0600-\u06FF]){re.escape(k)}(?![\w\u0600-\u06FF])"
            if re.search(pattern, s):
                return True
        else:
            pattern = rf"\b{re.escape(k)}\b"
            if re.search(pattern, s, re.IGNORECASE):
                return True
    return False

# ── Lined Paper Background ────────────────────────────────────────────────────
def _bg(c, is_rtl=True):
    # Soft warm ivory canvas
    c.setFillColor(C_BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    # Subtle ruled lines with delicate opacity for clean academic reading
    c.setStrokeColor(HexColor("#F1ECE1"))
    c.setLineWidth(0.4)
    y = H - MARGIN - 28
    while y >= BOTTOM + 8:
        c.line(MARGIN - 12, y, W - MARGIN + 12, y)
        y -= 26

# ── Page State ────────────────────────────────────────────────────────────────
class _S:
    def __init__(self, page_num=1, student_name="طالب متميز", is_rtl=True, is_free_trial=False):
        self.y = H - MARGIN - 18
        self.page_num = page_num
        self.student_name = student_name
        self.main_rtl = is_rtl   # Document-level RTL (never changes after init)
        self.is_rtl = is_rtl     # Current paragraph RTL (changes per paragraph)
        self.is_free_trial = is_free_trial

    def new_page(self, c):
        c.showPage()
        self.page_num += 1
        _bg(c, is_rtl=self.main_rtl)   # Use document-level, not per-para
        if self.is_free_trial:
            c.saveState()
            c.setFont("HW-Reg", 18)
            c.setFillColor(HexColor("#888888"))
            c.setFillAlpha(0.2)
            wm_text = get_display(arabic_reshaper.reshape("Created by Free Trial - اشترك للحصول على الملزمة كاملة وبدون علامة"))
            c.drawString(40, H / 2, wm_text)
            c.restoreState()
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
def _para(c, text, st, font_sz: Union[int, float] = FSIZ, lh: Union[int, float] = LINE_H, color=C_BODY, bold=False, indent=0, max_w=None):
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
            if m:
                pfx = m.group(1)
                p   = p[m.end():].strip()
            else:
                pfx = ""
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

        # Detect paragraph language
        para_is_rtl = _is_rtl_para(p, is_main_rtl=st.main_rtl)
        st.is_rtl = para_is_rtl

        total_lines = len(lines)
        # Prevent orphan lines: if list item or short paragraph (<= 3 lines), keep all lines together
        if total_lines <= 3:
            if st.y - (total_lines * lh + 4) < BOTTOM:
                st.new_page(c)
        else:
            # Need at least 2 lines to start on the current page
            if st.y - (2 * lh + 4) < BOTTOM:
                st.new_page(c)

        # Compute x boundaries once per paragraph
        x_base = MARGIN + indent
        avail_right = x_base + avail   # right bound of available area

        for li, line_str in enumerate(lines):
            # If last line would be left alone on a new page (widow line), break early if possible
            if li == total_lines - 1 and st.y < BOTTOM + lh:
                st.new_page(c)
            elif st.y < BOTTOM + lh:
                st.new_page(c)

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
                    pfx_d, _ = _bidi(pfx, base_dir='R' if para_is_rtl else 'L')
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
            disp, _ = _bidi(clean_l, base_dir='R' if para_is_rtl else 'L')
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
                hd, _ = _bidi(hw, base_dir='R' if para_is_rtl else 'L')
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
    t = _clean(str(title)).rstrip(': :：').strip()[:70]
    is_latin_start = bool(re.search(r'^[a-zA-Z]', re.sub(r'^[\s\d\W]+', '', t)))
    is_rtl = False if is_latin_start else _has_ar(t)
    st.is_rtl = is_rtl
    if st.y - 50 < BOTTOM: st.new_page(c)
    st.y -= 14
    td, _ = _bidi(t, base_dir='L' if is_latin_start else 'R')
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
    t = _clean(str(title)).rstrip(': :：').strip()[:75]
    is_latin_start = bool(re.search(r'^[a-zA-Z]', re.sub(r'^[\s\d\W]+', '', t)))
    is_rtl = False if is_latin_start else _has_ar(t)
    st.is_rtl = is_rtl
    if st.y - 40 < BOTTOM: st.new_page(c)
    st.y -= 8
    td, _ = _bidi(t, base_dir='L' if is_latin_start else 'R')
    c.setFont(FONT_BOLD, 13)
    tw = c.stringWidth(td, FONT_BOLD, 13)
    c.setFillColor(C_GOLD)
    if is_rtl:
        c.drawString(W - MARGIN - tw - 20, st.y, td)
    else:
        c.drawString(MARGIN + 20, st.y, td)
    
    # Clean full-width line under the heading for a premium look
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(1.0)
    c.line(MARGIN, st.y - 4, W - MARGIN, st.y - 4)
    st.y -= 22

# ── H3 (Exam Summary Header) ─────────────────────────────────────────────────
def _h3(c, st, title):
    t = _clean(str(title)).rstrip(': :：').strip()[:80]
    is_latin_start = bool(re.search(r'^[a-zA-Z]', re.sub(r'^[\s\d\W]+', '', t)))
    is_rtl = False if is_latin_start else _has_ar(t)
    st.is_rtl = is_rtl
    if st.y - 30 < BOTTOM: st.new_page(c)
    st.y -= 6
    td, _ = _bidi(t, base_dir='L' if is_latin_start else 'R')
    c.setFont(FONT_BOLD, 11.5)
    tw = c.stringWidth(td, FONT_BOLD, 11.5)
    c.setFillColor(C_TEAL)
    if is_rtl:
        c.drawString(W - MARGIN - tw - 18, st.y, td)
    else:
        c.drawString(MARGIN + 18, st.y, td)
    st.y -= 20

# ── Modern Callout Card ────────────────────────────────────────────────────────
def _cornell_box(c, st, text, kind="note"):
    """Renders a beautiful full-width callout card with precise height, accent bar, and badge."""
    # Strip any leading markdown quotes > or bullets
    text = re.sub(r'^[>\s•\-]+', '', text).strip()
    is_arabic_text = _has_ar(text)
    st.is_rtl = is_arabic_text
    
    labels = {
        "exam": ("Exam Alert", "تنبيه امتحاني"),
        "note": ("Key Note", "ملاحظة هامة"),
        "remember": ("Remember", "تذكّر"),
        "trap": ("Exam Trap", "فخ امتحاني"),
        "quote": ("Professor's Quote", "اقتباس المحاضر")
    }
    lbl_en, lbl_ar = labels.get(kind, ("Note", "ملاحظة"))
    
    # Split text into cue/title and body at the first colon
    if ":" in text:
        parts = text.split(":", 1)
        title = parts[0].strip()
        body = parts[1].strip()
    else:
        title = lbl_ar if is_arabic_text else lbl_en
        body = text.strip()

    # Clean title
    title = re.sub(r'^[💡🧪⚡✅📌⚠📊✨🔒\-•>\s]+', '', title)
    title = title.replace("**", "").replace("*", "").strip()
    if not title:
        title = lbl_ar if is_arabic_text else lbl_en

    # Clean body
    body = re.sub(r'^[💡🧪⚡✅📌⚠📊✨🔒\-•>\s]+', '', body).strip()
    body = body.replace("**", "").replace("*", "").strip()
    
    # Colors for accents, background, border
    colors = {
        "exam": (C_GOLD, HexColor("#FFFBEB"), HexColor("#FDE68A")),
        "note": (C_BLUE, C_BOX_BG, C_BOX_BD),
        "remember": (C_TEAL, HexColor("#F0FDFA"), HexColor("#99F6E4")),
        "trap": (HexColor("#DC2626"), HexColor("#FEF2F2"), HexColor("#FCA5A5")),
        "quote": (HexColor("#7C3AED"), HexColor("#F5F3FF"), HexColor("#DDD6FE"))
    }
    theme_clr, bg_color, bd_color = colors.get(kind, (C_BLUE, C_BOX_BG, C_BOX_BD))
    
    # Wrap body text to compute EXACT height
    avail_text_w = TXT_W - 28
    words = body.split()
    body_lines = []
    cur_w, cur_words = 0.0, []
    for w in words:
        ww = c.stringWidth(_clean(w) + " ", FONT_REG, 9)
        if cur_w + ww <= avail_text_w:
            cur_words.append(w)
            cur_w += ww
        else:
            if cur_words: body_lines.append(" ".join(cur_words))
            cur_words, cur_w = [w], ww
    if cur_words: body_lines.append(" ".join(cur_words))
    
    line_count = max(1, len(body_lines))
    box_h = 24 + line_count * 15 + 8
    
    if st.y - box_h < BOTTOM:
        st.new_page(c)
        st.y -= 4
        
    box_y = st.y - box_h
    
    # Background and border
    c.setFillColor(bg_color)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=1, stroke=0)
    c.setStrokeColor(bd_color)
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=0, stroke=1)
    
    # Accent vertical strip and content
    c.setFillColor(theme_clr)
    if is_arabic_text:
        # Bar on right
        c.rect(W - MARGIN - 4, box_y + 4, 3.5, box_h - 8, fill=1, stroke=0)
        # Title on right
        c.setFont(FONT_BOLD, 9.5)
        title_d, _ = _bidi(title, base_dir='R')
        c.drawRightString(W - MARGIN - 14, box_y + box_h - 17, title_d)
        
        # Body lines on right
        c.setFont(FONT_REG, 9)
        c.setFillColor(C_BODY)
        cur_ly = box_y + box_h - 34
        for bl in body_lines:
            bld, _ = _bidi(bl, base_dir='R')
            c.drawRightString(W - MARGIN - 14, cur_ly, bld)
            cur_ly -= 15
    else:
        # Bar on left
        c.rect(MARGIN + 1, box_y + 4, 3.5, box_h - 8, fill=1, stroke=0)
        # Title on left
        c.setFont(FONT_BOLD, 9.5)
        title_d, _ = _bidi(title, base_dir='L')
        c.drawString(MARGIN + 14, box_y + box_h - 17, title_d)
        
        # Body lines on left
        c.setFont(FONT_REG, 9)
        c.setFillColor(C_BODY)
        cur_ly = box_y + box_h - 34
        for bl in body_lines:
            bld, _ = _bidi(bl, base_dir='L')
            c.drawString(MARGIN + 14, cur_ly, bld)
            cur_ly -= 15

    st.y = box_y - 12

# ── Code & Formula Card ──────────────────────────────────────────────────────


def _draw_study_strategy_and_tldr(c, st, lecture_title, study_strategy=None, tldr=None):
    _bg(c, is_rtl=st.main_rtl)
    st._footer(c)
    st.y = H - MARGIN - 18
    
    header_title = "دليل دراسة المحاضرة والزبدة السريعة" if st.main_rtl else "Study Guide & TL;DR"
    _h1(c, st, header_title)
    
    if not study_strategy:
        study_strategy = "هذه المحاضرة غنية بالمفاهيم الأساسية، ننصحك بالتركيز على مراجعة المصطلحات والأسئلة التدريبية لترسيخ الفهم."
        
    words = study_strategy.split()
    strat_lines = []
    cur_w, cur_words = 0.0, []
    avail_w = TXT_W - 28
    for w in words:
        ww = c.stringWidth(_clean(w) + " ", FONT_REG, 9)
        if cur_w + ww <= avail_w:
            cur_words.append(w)
            cur_w += ww
        else:
            if cur_words: strat_lines.append(" ".join(cur_words))
            cur_words, cur_w = [w], ww
    if cur_words: strat_lines.append(" ".join(cur_words))
    box_h = max(60, len(strat_lines) * 16 + 36)

    box_y = st.y - box_h
    c.setFillColor(HexColor("#FDFBF7"))
    c.setStrokeColor(HexColor("#D9C9B5"))
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=1, stroke=1)
    
    # Draw side colored bar (Teal)
    c.setFillColor(C_TEAL)
    c.rect(W - MARGIN - 4, box_y + 4, 3, box_h - 8, fill=1, stroke=0)
    
    # Title
    c.setFont(FONT_BOLD, 10)
    c.setFillColor(C_TEAL)
    t_text, _ = _bidi("نصيحة لمّاح الأكاديمية المقترحة للدراسة:", base_dir='R')
    c.drawRightString(W - MARGIN - 12, box_y + box_h - 18, t_text)
    
    # Strategy Content
    c.setFont(FONT_REG, 9)
    c.setFillColor(C_BODY)
    cur_y = box_y + box_h - 34
    for line_s in strat_lines:
        line_d, _ = _bidi(line_s, base_dir='R' if st.main_rtl else 'L')
        c.drawRightString(W - MARGIN - 16, cur_y, line_d)
        cur_y -= 15
    
    st.y = box_y - 20
    
    # TL;DR Title
    c.setFont(FONT_BOLD, 11)
    c.setFillColor(C_BLUE)
    tldr_title = "إذا كان امتحانك بعد 5 دقائق.. اقرأ هنا فقط!" if st.main_rtl else "Exam in 5 Minutes? Read this TL;DR!"
    tldr_title_d, _ = _bidi(tldr_title, base_dir='R' if st.main_rtl else 'L')
    if st.main_rtl:
        c.drawRightString(W - MARGIN, st.y, tldr_title_d)
    else:
        c.drawString(MARGIN, st.y, tldr_title_d)
    
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(1.0)
    c.line(MARGIN, st.y - 4, W - MARGIN, st.y - 4)
    st.y -= 22
    
    # TL;DR Box with auto-wrapping to guarantee zero text overflow
    if not tldr:
        tldr = [
            "المفاهيم الأساسية الواردة في هذه المحاضرة جوهرية للامتحان.",
            "احرص على مراجعة جدول المقارنة والأسئلة التطبيقية لترسيخ الفهم."
        ]
        
    bullet_items = []
    total_bullet_lines = 0
    font_sz = 9.5
    lh = 15
    avail_bullet_w = TXT_W - 38

    for pt in tldr[:6]:
        pt_clean = re.sub(r'^[•\-\*]\s*', '', str(pt)).strip()
        words = pt_clean.split()
        cur_w, cur_words = 0.0, []
        pt_lines = []
        for w in words:
            cl = _clean(w)
            ww = c.stringWidth(cl + " ", FONT_REG, font_sz)
            if cur_w + ww <= avail_bullet_w:
                cur_words.append(w)
                cur_w += ww
            else:
                if cur_words: pt_lines.append(" ".join(cur_words))
                cur_words, cur_w = [w], ww
        if cur_words: pt_lines.append(" ".join(cur_words))
        if pt_lines:
            bullet_items.append(pt_lines)
            total_bullet_lines += len(pt_lines)

    tldr_h = total_bullet_lines * lh + len(bullet_items) * 8 + 22
    tldr_y = st.y - tldr_h
    c.setFillColor(HexColor("#F7F1E8"))
    c.setStrokeColor(HexColor("#D9C9B5"))
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, tldr_y, TXT_W, tldr_h, 6, fill=1, stroke=1)
    
    # Draw side colored bar (Gold)
    c.setFillColor(C_GOLD)
    c.rect(W - MARGIN - 4, tldr_y + 4, 3, tldr_h - 8, fill=1, stroke=0)
    
    # Draw each wrapped bullet point
    current_y = tldr_y + tldr_h - 18
    for pt_lines in bullet_items:
        for li, line_str in enumerate(pt_lines):
            line_disp, _ = _bidi(line_str, base_dir='R' if st.main_rtl else 'L')
            if li == 0:
                c.setFont(FONT_BOLD, font_sz + 1)
                c.setFillColor(C_GOLD)
                c.drawRightString(W - MARGIN - 12, current_y, "•")
            
            c.setFont(FONT_REG, font_sz)
            c.setFillColor(C_BODY)
            c.drawRightString(W - MARGIN - 24, current_y, line_disp)
            current_y -= lh
        current_y -= 6
        
    st.y = tldr_y - 20

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
            if st.y - 100 < BOTTOM: st.new_page(c)
            _h1(c, st, body)
        elif level == 2:
            if st.y - 80 < BOTTOM: st.new_page(c)
            _h2(c, st, body)
        elif level == 3:
            if st.y - 65 < BOTTOM: st.new_page(c)
            _h3(c, st, body)
        else:
            is_trap    = _has_keyword(s, ["فخ امتحاني", "Exam Trap", "فخ"])
            is_exam    = _has_keyword(s, ["هام للامتحان", "امتحان", "مهم", "ركز", "Exam Alert", "EXAM", "Important:", "WARNING:", "⚠"])
            is_note    = _has_keyword(s, ["💡", "ملاحظة", "توضيح", "Note:", "NOTE:", "Tip:", "TIP:"])
            is_rem     = _has_keyword(s, ["📌", "تذكّر", "تذكر", "Remember:", "REMEMBER:", "Key Point:"])
            is_quote   = _has_keyword(s, ["💬", "اقتباسات المحاضر", "اقتباس المحاضر", "اقتباسات", "Professor's Quotes", "Speaker's Quotes", "اقتباس"])

            if (is_trap or is_exam or is_note or is_rem or is_quote) and len(s) < 500:
                kind = "trap" if is_trap else ("exam" if is_exam else ("remember" if is_rem else ("quote" if is_quote else "note")))
                col_idx = s.find(":")
                if col_idx != -1:
                    cue_part = s[:col_idx].strip()
                    body_part = s[col_idx + 1:].strip()
                else:
                    cue_part = ""
                    body_part = s.strip()
                
                cleaned_body = body_part.replace("**", "").replace("*", "").strip()
                if not cleaned_body or len(cleaned_body) < 3:
                    next_i = i + 1
                    swallowed = []
                    while next_i < len(lines_list):
                        next_line = _strip_ctrl(lines_list[next_i]).strip()
                        if not next_line:
                            next_i += 1
                            continue
                        if next_line.startswith("#") or next_line.startswith("---") or next_line.startswith("```"):
                            break
                        if any(emoji in next_line for emoji in ["💡", "⚠", "📌", "🔑", "💬"]):
                            break
                        swallowed.append(next_line)
                        next_i += 1
                        break
                    
                    if swallowed:
                        body_part = " ".join(swallowed)
                        i = next_i - 1
                        
                final_body = body_part.replace("**", "").replace("*", "").strip()
                if final_body and len(final_body) >= 3:
                    if col_idx != -1 and cue_part:
                        cue_part = re.sub(r'^[💡🧪⚡✅📌⚠📊✨🔒\-•\s]+', '', cue_part)
                        cue_part = cue_part.replace("**", "").replace("*", "").strip()
                        box_text = f"{cue_part}: {body_part}"
                    else:
                        box_text = body_part
                    _cornell_box(c, st, box_text, kind=kind)
                else:
                    _para(c, body, st)
            else:
                _para(c, body, st)
        i += 1

# ── Cover Page ────────────────────────────────────────────────────────────────
def _cover(c, title, student_name, stats: Optional[dict] = None, language_mode: str = "bilingual"):
    is_english = (language_mode == "english")
    
    # 1. Clean Luxury Ivory Canvas (NO ruled notebook paper on cover)
    c.setFillColor(HexColor("#FCFBF9"))
    c.rect(0, 0, W, H, fill=1, stroke=0)
    
    # Elegant double-border academic frame
    c.setStrokeColor(HexColor("#CBD5E1"))
    c.setLineWidth(1.0)
    c.roundRect(MARGIN - 20, MARGIN - 20, W - 2 * (MARGIN - 20), H - 2 * (MARGIN - 20), 12, fill=0, stroke=1)
    
    c.setStrokeColor(HexColor("#E2E8F0"))
    c.setLineWidth(0.5)
    c.roundRect(MARGIN - 16, MARGIN - 16, W - 2 * (MARGIN - 16), H - 2 * (MARGIN - 16), 10, fill=0, stroke=1)

    # 2. Hero Header Card (Deep Royal Midnight Navy)
    hero_h = 160
    hero_y = H - MARGIN - hero_h - 4
    c.setFillColor(HexColor("#0F172A"))
    c.roundRect(MARGIN - 10, hero_y, TXT_W + 20, hero_h, 10, fill=1, stroke=0)
    
    # Gold accent line at bottom of hero card
    c.setFillColor(HexColor("#D97706"))
    c.rect(MARGIN - 10, hero_y, TXT_W + 20, 3, fill=1, stroke=0)
    
    # Top Gold Badge inside Hero Card (NO EMOJIS OR UNICODE STARS)
    top_badge = "• النسخة الأكاديمية المعتمدة | EXAM MASTER STUDY GUIDE •" if not is_english else "• ACCREDITED ACADEMIC EDITION | MASTER STUDY GUIDE •"
    tb_d, _ = _bidi(top_badge)
    c.setFont(FONT_BOLD, 8.5)
    c.setFillColor(HexColor("#FBBF24"))
    tb_w = c.stringWidth(tb_d, FONT_BOLD, 8.5)
    c.drawString(W / 2 - tb_w / 2, hero_y + hero_h - 34, tb_d)
    
    # Platform / Brand Name
    brand_title = "منصة لمّاح الذكية للدراسة الجامعية" if not is_english else "Lammah Academic AI Engine"
    bt_d, _ = _bidi(brand_title)
    c.setFont(FONT_BOLD, 17)
    c.setFillColor(HexColor("#FFFFFF"))
    bt_w = c.stringWidth(bt_d, FONT_BOLD, 17)
    c.drawString(W / 2 - bt_w / 2, hero_y + hero_h - 72, bt_d)
    
    # Subtitle inside Hero Card
    sub_title = "ملزمة دراسية شاملة ومستقلة • High-Yield Comprehensive Lecture Companion" if not is_english else "Complete High-Yield Examination Preparation Guide"
    st_d, _ = _bidi(sub_title)
    c.setFont(FONT_REG, 9)
    c.setFillColor(HexColor("#94A3B8"))
    st_w = c.stringWidth(st_d, FONT_REG, 9)
    c.drawString(W / 2 - st_w / 2, hero_y + hero_h - 102, st_d)
    
    # 3. Lecture Title Section
    clean_t = _clean(str(title))
    if not clean_t or clean_t == "None":
        clean_t = "محاضرة جامعية شاملة"
        
    badge_lbl = "موضوع المحاضرة الرئيسي" if not is_english else "CORE LECTURE TOPIC"
    bl_d, _ = _bidi(badge_lbl)
    c.setFont(FONT_BOLD, 8.5)
    c.setFillColor(HexColor("#4F46E5"))
    bl_w = c.stringWidth(bl_d, FONT_BOLD, 8.5)
    c.drawString(W / 2 - bl_w / 2, hero_y - 28, bl_d)
    
    # Smart title splitting: if title has (English in parentheses), put English on 2nd line!
    m_en = re.search(r'\(([^)]+)\)', clean_t)
    if m_en and len(clean_t) > 35:
        en_part = f"({m_en.group(1)})"
        ar_part = clean_t.replace(en_part, "").strip()
        t1_d, _ = _bidi(ar_part)
        t2_d, _ = _bidi(en_part, base_dir='L')
        c.setFont(FONT_BOLD, 20)
        c.setFillColor(HexColor("#0F172A"))
        c.drawString(W / 2 - c.stringWidth(t1_d, FONT_BOLD, 20) / 2, hero_y - 58, t1_d)
        c.setFont(FONT_BOLD, 15)
        c.setFillColor(HexColor("#4338CA"))
        c.drawString(W / 2 - c.stringWidth(t2_d, FONT_BOLD, 15) / 2, hero_y - 84, t2_d)
    else:
        td, _ = _bidi(clean_t)
        c.setFont(FONT_BOLD, 20)
        c.setFillColor(HexColor("#0F172A"))
        tw = c.stringWidth(td, FONT_BOLD, 20)
        c.drawString(W / 2 - tw / 2, hero_y - 64, td)
        
    # Elegant divider
    c.setStrokeColor(HexColor("#E2E8F0"))
    c.setLineWidth(1.0)
    c.line(W / 2 - 80, hero_y - 98, W / 2 + 80, hero_y - 98)
    c.setFillColor(HexColor("#D97706"))
    c.circle(W / 2, hero_y - 98, 2.5, fill=1, stroke=0)

    # 4. Analytics 2x2 Metric Cards
    panel_y = hero_y - 128
    panel_text = "خريطة المحاضرة والبيانات الأساسية (Lecture Blueprint & Key Metrics)" if not is_english else "Lecture Blueprint & Key Metrics"
    panel_lbl, _ = _bidi(panel_text)
    c.setFont(FONT_BOLD, 9)
    c.setFillColor(HexColor("#64748B"))
    pl_w = c.stringWidth(panel_lbl, FONT_BOLD, 9)
    c.drawString(W / 2 - pl_w / 2, panel_y, panel_lbl)
    
    if not stats:
        stats = {"num_key_terms": 16, "num_mcqs": 5, "num_comparisons": 2, "est_study_time": 25}
        
    box_w = (TXT_W - 12) / 2
    box_h = 56
    col1_x = MARGIN
    col2_x = MARGIN + box_w + 12
    row1_y = panel_y - 70
    row2_y = row1_y - 66
    
    study_time_val = f"{stats.get('est_study_time', 25)} دقيقة" if not is_english else f"{stats.get('est_study_time', 25)} min"
    
    cards = [
        (col1_x, row1_y, str(stats.get("num_key_terms", 16)), "مصطلح وقاعدة أساسية (Key Terms)" if not is_english else "Key Terminology (High-Yield)", HexColor("#EEF2FF"), HexColor("#C7D2FE"), HexColor("#4F46E5"), HexColor("#3730A3")),
        (col2_x, row1_y, str(stats.get("num_mcqs", 5)), "سؤال محاكاة امتحاني (Simulation MCQs)" if not is_english else "Simulation MCQs (Exam Level)", HexColor("#ECFDF5"), HexColor("#A7F3D0"), HexColor("#10B981"), HexColor("#065F46")),
        (col1_x, row2_y, str(stats.get("num_comparisons", 1)), "جدول مقارنة وفروق (Comparisons)" if not is_english else "Comparison Matrices", HexColor("#FFFBEB"), HexColor("#FDE68A"), HexColor("#F59E0B"), HexColor("#92400E")),
        (col2_x, row2_y, study_time_val, "الوقت المقدر للمراجعة (Study Time)" if not is_english else "Target Study Time", HexColor("#F0F9FF"), HexColor("#BAE6FD"), HexColor("#0EA5E9"), HexColor("#0369A1")),
    ]
    
    for x, y, val, lbl, bg, bd, acc, num_c in cards:
        c.setFillColor(bg)
        c.setStrokeColor(bd)
        c.setLineWidth(0.8)
        c.roundRect(x, y, box_w, box_h, 8, fill=1, stroke=1)
        
        c.setFillColor(acc)
        c.roundRect(x + 4, y + 6, 3, box_h - 12, 1.5, fill=1, stroke=0)
        
        val_d, _ = _bidi(val)
        c.setFont(FONT_BOLD, 17)
        c.setFillColor(num_c)
        vw = c.stringWidth(val_d, FONT_BOLD, 17)
        c.drawString(x + box_w / 2 - vw / 2, y + 28, val_d)
        
        lbl_d, _ = _bidi(lbl)
        c.setFont(FONT_REG, 7.8)
        c.setFillColor(HexColor("#475569"))
        lw = c.stringWidth(lbl_d, FONT_REG, 7.8)
        c.drawString(x + box_w / 2 - lw / 2, y + 12, lbl_d)

    # 5. Luxury Credentials Card
    cred_y = 95
    cred_h = 100
    c.setFillColor(HexColor("#F8FAFC"))
    c.setStrokeColor(HexColor("#E2E8F0"))
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, cred_y, TXT_W, cred_h, 8, fill=1, stroke=1)
    
    c.setFillColor(HexColor("#4F46E5"))
    c.roundRect(MARGIN, cred_y + cred_h - 3, TXT_W, 3, 1, fill=1, stroke=0)
    
    s_name = str(student_name).strip() if student_name else ""
    if not s_name or s_name in ["1", "طالب", "None"]:
        s_name = "طالب متفوق" if not is_english else "Distinguished Student"
        
    if not is_english:
        lines_info = [
            ("إعداد وتوثيق الطالب:", s_name),
            ("الجهة الناشرة:", "منصة لمّاح الرقمية للدراسة الجامعية الذكية"),
            ("طبيعة الإصدار الأكاديمي:", "نسخة معتمدة ومطابقة لمتطلبات الاختبارات الجامعية"),
            ("حقوق الملكية الفكرية:", "محفوظة للطالب ولمنصة لمّاح © 2026")
        ]
        for idx, (label, val) in enumerate(lines_info):
            ly = cred_y + cred_h - 22 - idx * 20
            ld, _ = _bidi(label, base_dir='R')
            vd, _ = _bidi(val, base_dir='R')
            c.setFont(FONT_BOLD, 8.5)
            c.setFillColor(HexColor("#334155"))
            c.drawRightString(W - MARGIN - 16, ly, ld)
            
            c.setFont(FONT_REG, 8.5)
            c.setFillColor(HexColor("#0F172A"))
            c.drawRightString(W - MARGIN - 160, ly, vd)
    else:
        lines_info = [
            ("Author / Student:", s_name),
            ("Publishing Platform:", "Lammah Academic AI Platform"),
            ("Edition Standard:", "Accredited University Exam Master Edition"),
            ("Academic Rights:", "All Rights Reserved to Creator & Lammah © 2026")
        ]
        for idx, (label, val) in enumerate(lines_info):
            ly = cred_y + cred_h - 22 - idx * 20
            c.setFont(FONT_BOLD, 8.5)
            c.setFillColor(HexColor("#334155"))
            c.drawString(MARGIN + 16, ly, label)
            
            c.setFont(FONT_REG, 8.5)
            c.setFillColor(HexColor("#0F172A"))
            c.drawString(MARGIN + 160, ly, val)

    # 6. Bottom Signature
    bot_text = "صُنع بإتقان فائق بواسطة محرك لمّاح الأكاديمي • Lammah AI Master Engine" if not is_english else "Crafted with Academic Rigor by Lammah AI Master Engine"
    bot_d, _ = _bidi(bot_text)
    c.setFont(FONT_REG, 7.5)
    c.setFillColor(HexColor("#94A3B8"))
    c.drawString(W / 2 - c.stringWidth(bot_d, FONT_REG, 7.5) / 2, 38, bot_d)

    c.showPage()

# ── Cheat Sheet Page ─────────────────────────────────────────────────────────
def _draw_cheat_sheet(c, st, items: list):
    """Renders a 'زبدة ليلة الامتحان' cheat sheet table."""
    if not items: return

    # Detect cheat sheet language
    is_arabic_cheat = any(_has_ar(item.get("term", "")) or _has_ar(item.get("brief_definition", "")) for item in items)
    st.is_rtl = is_arabic_cheat

    # Check if we have space on current page for banner + header + items, else new page
    needed_for_cheat = 150 + min(len(items), 3) * 22
    if st.y - needed_for_cheat < BOTTOM:
        st.new_page(c)
        st.y -= 6
    else:
        st.y -= 14
        _divider(c, st)
        st.y -= 10

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
    """Renders 'اختبر نفسك' multiple choice questions with bilingual English exam support."""
    if not mcqs: return

    is_arabic_mcqs = any(_has_ar(q.get("question", "")) for q in mcqs)
    st.is_rtl = is_arabic_mcqs

    first_q_choices = (mcqs[0].get("choices") or mcqs[0].get("options") or []) if mcqs else []
    needed_for_mcq = 160 + len(first_q_choices) * 24
    if st.y - needed_for_mcq < BOTTOM:
        st.new_page(c)
        st.y -= 6
    else:
        st.y -= 14
        _divider(c, st)
        st.y -= 10

    # Section banner (No emojis)
    c.setFillColor(HexColor("#0F766E"))
    c.roundRect(MARGIN, st.y - 34, TXT_W, 34, 6, fill=1, stroke=0)
    c.setFillColor(HexColor("#D97706"))
    c.rect(MARGIN, st.y - 37, TXT_W, 3, fill=1, stroke=0)
    
    title_text = "اختبر نفسك — هل أنت جاهز للامتحان؟ (Simulation MCQs)" if is_arabic_mcqs else "Simulation Exam Questions (Self-Test MCQs)"
    title_d, _ = _bidi(title_text)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(FONT_BOLD, 13)
    tw = c.stringWidth(title_d, FONT_BOLD, 13)
    c.drawString(W / 2 - tw / 2, st.y - 23, title_d)
    st.y -= 52

    answers = []

    for i, q in enumerate(mcqs):
        # Question header & text
        q_raw = q.get('question', '').strip()
        q_full = f"{i+1}. {q_raw}"
        
        # Detect if question starts with English
        first_clean = re.sub(r'^[\s\d\.\W]+', '', q_full)
        is_latin_start = bool(re.search(r'^[a-zA-Z]', first_clean))
        
        # Word wrapping for question box
        words = q_full.split()
        lines = []
        cur = []
        cur_w = 0
        max_qw = TXT_W - 32
        for w in words:
            wd, _ = _bidi(w, base_dir='L' if is_latin_start else 'R')
            ww = c.stringWidth(wd + " ", FONT_BOLD, 9.8)
            if cur_w + ww <= max_qw:
                cur.append(w)
                cur_w += ww
            else:
                if cur: lines.append(" ".join(cur))
                cur = [w]
                cur_w = ww
        if cur: lines.append(" ".join(cur))
        
        q_box_h = len(lines) * 18 + 18
        
        # Choices list (supports both choices and options keys)
        choices = q.get("choices") or q.get("options") or []
        q_total_h = q_box_h + len(choices) * 24 + 16
        
        # Keep entire question + choices together if possible
        if st.y - q_total_h < BOTTOM and st.y < H - MARGIN - 60:
            st.new_page(c)
            st.y -= 4
        elif st.y - q_box_h < BOTTOM:
            st.new_page(c)
            st.y -= 4
            
        box_y = st.y - q_box_h
        
        c.setFillColor(HexColor("#F0FDFA"))
        c.setStrokeColor(HexColor("#99F6E4"))
        c.setLineWidth(0.8)
        c.roundRect(MARGIN, box_y, TXT_W, q_box_h, 6, fill=1, stroke=1)
        
        c.setFont(FONT_BOLD, 9.8)
        c.setFillColor(HexColor("#115E59"))
        
        if is_latin_start:
            # Teal accent bar on Left
            c.setFillColor(HexColor("#0D9488"))
            c.rect(MARGIN + 1, box_y + 4, 3.5, q_box_h - 8, fill=1, stroke=0)
            for li, line_str in enumerate(lines):
                line_y = box_y + q_box_h - 16 - li * 18
                ld, _ = _bidi(line_str, base_dir='L')
                c.drawString(MARGIN + 14, line_y, ld)
        else:
            # Teal accent bar on Right
            c.setFillColor(HexColor("#0D9488"))
            c.rect(W - MARGIN - 4.5, box_y + 4, 3.5, q_box_h - 8, fill=1, stroke=0)
            for li, line_str in enumerate(lines):
                line_y = box_y + q_box_h - 16 - li * 18
                ld, _ = _bidi(line_str, base_dir='R')
                c.drawRightString(W - MARGIN - 14, line_y, ld)
            
        st.y = box_y - 12

        # Choices with modern pill badges
        for j, ch in enumerate(choices):
            if st.y - 24 < BOTTOM: st.new_page(c)
            
            # Extract letter and body
            ch_str = str(ch).strip()
            m = re.match(r'^([A-Da-dأ-ي][\)\.\:\-]?)\s*(.*)', ch_str)
            if m:
                letter = m.group(1).rstrip(').:- ').upper()
                body = m.group(2).strip()
            else:
                letter = chr(65 + (j % 4))
                body = ch_str
                
            ch_is_rtl = _has_ar(body) and not bool(re.search(r'^[a-zA-Z]', _clean(body)))
            
            badge_w, badge_h = 20, 15
            
            if ch_is_rtl:
                badge_x = W - MARGIN - badge_w - 6
                badge_y = st.y - 14
                
                c.setFillColor(HexColor("#F1F5F9"))
                c.setStrokeColor(HexColor("#CBD5E1"))
                c.setLineWidth(0.6)
                c.roundRect(badge_x, badge_y, badge_w, badge_h, 3, fill=1, stroke=1)
                
                c.setFont(FONT_BOLD, 9)
                c.setFillColor(HexColor("#334155"))
                c.drawCentredString(badge_x + badge_w / 2, badge_y + 3.5, letter)
                
                # Choice body right aligned
                text_x = badge_x - 8
                body_d, _ = _bidi(body, base_dir='R')
                c.setFont(FONT_REG, 9)
                c.setFillColor(HexColor("#1E293B"))
                c.drawRightString(text_x, st.y - 11, body_d)
            else:
                badge_x = MARGIN + 8
                badge_y = st.y - 14
                
                c.setFillColor(HexColor("#F1F5F9"))
                c.setStrokeColor(HexColor("#CBD5E1"))
                c.setLineWidth(0.6)
                c.roundRect(badge_x, badge_y, badge_w, badge_h, 3, fill=1, stroke=1)
                
                c.setFont(FONT_BOLD, 9)
                c.setFillColor(HexColor("#334155"))
                c.drawCentredString(badge_x + badge_w / 2, badge_y + 3.5, letter)
                
                # Choice body left aligned
                text_x = badge_x + badge_w + 8
                body_d, _ = _bidi(body, base_dir='L')
                c.setFont(FONT_REG, 9)
                c.setFillColor(HexColor("#1E293B"))
                c.drawString(text_x, st.y - 11, body_d)
            
            st.y -= 22

        ans_val = q.get('answer') or q.get('correct_answer') or q.get('correct') or ""
        answers.append(f"{i+1}. {ans_val}")
        st.y -= 8

    # Answers Section
    if answers:
        st.y -= 10
        c.setFillColor(HexColor("#F8FAFC"))
        c.setStrokeColor(HexColor("#E2E8F0"))
        c.setLineWidth(0.8)
        ans_box_h = 32
        c.roundRect(MARGIN, st.y - ans_box_h, TXT_W, ans_box_h, 6, fill=1, stroke=1)
        
        ans_lbl = "مفتاح الإجابات النموذجية (Answer Key):" if is_arabic_mcqs else "Answer Key:"
        ans_lbl_d, _ = _bidi(ans_lbl, base_dir='R' if is_arabic_mcqs else 'L')
        c.setFont(FONT_BOLD, 8.5)
        c.setFillColor(HexColor("#475569"))
        if is_arabic_mcqs:
            c.drawRightString(W - MARGIN - 12, st.y - 20, ans_lbl_d)
        else:
            c.drawString(MARGIN + 12, st.y - 20, ans_lbl_d)
        
        ans_text = "   |   ".join(answers)
        c.setFont(FONT_BOLD, 9)
        c.setFillColor(HexColor("#0D9488"))
        if is_arabic_mcqs:
            c.drawString(MARGIN + 16, st.y - 20, ans_text)
        else:
            c.drawString(MARGIN + 100, st.y - 20, ans_text)
        
        st.y -= ans_box_h + 16

    # Self-Evaluation Mastery Checklist (keep checklist title + items together)
    if st.y - 100 < BOTTOM:
        st.new_page(c)
        st.y -= 6

    chk_title = "قائمة التقييم الذاتي والاستيعاب (Self-Evaluation Checklist):" if is_arabic_mcqs else "Self-Evaluation & Mastery Checklist:"
    chk_title_d, _ = _bidi(chk_title, base_dir='R' if is_arabic_mcqs else 'L')
    c.setFont(FONT_BOLD, 10.5)
    c.setFillColor(HexColor("#1E3A8A"))
    if is_arabic_mcqs:
        c.drawRightString(W - MARGIN - 8, st.y, chk_title_d)
    else:
        c.drawString(MARGIN + 8, st.y, chk_title_d)
    st.y -= 18
    
    chk_items = [
        "استوعبت المفاهيم والعمليات الأساسية الواردة في هذه المحاضرة بدقة." if is_arabic_mcqs else "I understand the core concepts covered in this lecture.",
        "أستطيع التمييز بين المصطلحات والمراحل المتشابهة لتفادي فخاخ الامتحان." if is_arabic_mcqs else "I can distinguish between similar terms and avoid exam traps.",
        "قمت بحل كافة الأسئلة التدريبية بنجاح ومراجعة الإجابات الصحيحة." if is_arabic_mcqs else "I have successfully solved all practice questions and reviewed answers."
    ]
    for item in chk_items:
        if st.y - 20 < BOTTOM: st.new_page(c)
        px = W - MARGIN - 16
        c.setStrokeColor(HexColor("#D97706"))
        c.setLineWidth(1.0)
        c.roundRect(px, st.y - 2, 10, 10, 2, fill=0, stroke=1)
        
        # Checkmark
        c.setStrokeColor(HexColor("#059669"))
        c.setLineWidth(1.2)
        c.line(px + 2, st.y + 3, px + 4, st.y + 1)
        c.line(px + 4, st.y + 1, px + 8, st.y + 7)
        
        item_d, _ = _bidi(item, base_dir='R')
        c.setFont(FONT_REG, 9)
        c.setFillColor(HexColor("#334155"))
        c.drawRightString(W - MARGIN - 26, st.y, item_d)
        st.y -= 18

# ── Comparison Tables Page ────────────────────────────────────────────────────
def _draw_comparisons(c, st, comparisons: list):
    """Renders smart comparison tables extracted by the AI."""
    if not comparisons: return

    is_arabic_comp = any(_has_ar(comp.get("title", "")) for comp in comparisons)
    st.is_rtl = is_arabic_comp

    first_tbl_h = (len(comparisons[0].get("rows", [])) + 1) * 32 + 4 if comparisons else 120
    needed_for_banner_and_table = 150 + first_tbl_h
    if st.y - needed_for_banner_and_table < BOTTOM:
        st.new_page(c)
        st.y -= 6
    else:
        st.y -= 14
        _divider(c, st)
        st.y -= 10

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

        # Check if headers already includes the aspect column
        full_headers = list(headers)
        aspect_keywords = ["وجه المقارنة", "المقارنة", "Aspect", "Feature", "المعيار", "الخاصية", "وجه الشبه", "الميزة", "Criterion", "Criteria"]
        already_has_aspect = bool(full_headers and (
            any(k.lower() in str(full_headers[0]).lower() for k in aspect_keywords) or
            (rows and len(full_headers) == len(rows[0]))
        ))
        if not already_has_aspect:
            aspect_lbl = "وجه المقارنة" if is_arabic_comp else "Comparison Aspect"
            full_headers = [aspect_lbl] + full_headers

        ncols   = len(full_headers)
        row_h   = 36
        tbl_h   = (len(rows) + 1) * row_h + 4

        # Smart column widths: aspect column is compact (~105pt), data columns share remaining space
        if ncols >= 3:
            w_aspect = max(95.0, min(115.0, TXT_W * 0.22))
            w_other = (TXT_W - w_aspect) / (ncols - 1)
            col_widths = [w_aspect] + [w_other] * (ncols - 1)
        else:
            col_widths = [TXT_W / ncols] * ncols

        # X positions for each column ci
        col_xs = [0.0] * ncols
        if is_arabic_comp:
            cur_x = MARGIN + TXT_W
            for ci in range(ncols):
                cur_x -= col_widths[ci]
                col_xs[ci] = cur_x
        else:
            cur_x = MARGIN
            for ci in range(ncols):
                col_xs[ci] = cur_x
                cur_x += col_widths[ci]

        # If subtitle + table doesn't fit on current page, advance to new page together
        if st.y - tbl_h - 45 < BOTTOM and st.y < H - MARGIN - 60:
            st.new_page(c)
            st.y -= 6

        # Comparison sub-title
        _h2(c, st, comp_title)

        top = st.y

        # Header row
        hy = top - row_h
        c.setFillColor(HexColor("#1B3A8A"))
        c.rect(MARGIN, hy, TXT_W, row_h, fill=1, stroke=0)
        c.setFont(FONT_BOLD, 9.0)
        c.setFillColor(HexColor("#FDFBF7"))

        for hi, hdr in enumerate(full_headers):
            hx = col_xs[hi]
            hw_col = col_widths[hi]
            hdr_text = str(hdr).strip()
            max_hw = hw_col - 10
            
            words = hdr_text.split()
            h_lines = []
            h_cur = []
            h_w = 0
            for hw_word in words:
                hww = c.stringWidth(_clean(hw_word) + " ", FONT_BOLD, 8.5)
                if h_w + hww <= max_hw:
                    h_cur.append(hw_word)
                    h_w += hww
                else:
                    if h_cur: h_lines.append(" ".join(h_cur))
                    h_cur = [hw_word]
                    h_w = hww
            if h_cur: h_lines.append(" ".join(h_cur))

            if len(h_lines) <= 1:
                txt = h_lines[0] if h_lines else ""
                hd, _ = _bidi(txt, base_dir='R' if is_arabic_comp else 'L')
                hw = c.stringWidth(hd, FONT_BOLD, 8.8)
                x_hdr = hx + hw_col / 2 - hw / 2
                c.drawString(x_hdr, hy + row_h / 2 - 3, hd)
            else:
                for li_h, hl_text in enumerate(h_lines[:2]):
                    hd, _ = _bidi(hl_text, base_dir='R' if is_arabic_comp else 'L')
                    hw = c.stringWidth(hd, FONT_BOLD, 8.2)
                    x_hdr = hx + hw_col / 2 - hw / 2
                    line_y = hy + row_h - 14 - li_h * 13
                    c.drawString(x_hdr, line_y, hd)

        # Header vertical separators
        c.setStrokeColor(HexColor("#FDFBF7"))
        c.setLineWidth(0.3)
        for x_sep in sorted(col_xs)[1:]:
            c.line(x_sep, hy, x_sep, hy + row_h)

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
                cx = col_xs[ci]
                cw_col = col_widths[ci]
                fn = FONT_BOLD if ci == 0 else FONT_REG
                fc = C_BLUE   if ci == 0 else C_BODY
                c.setFont(fn, 8.2)
                c.setFillColor(fc)
                
                cell_text = str(cell).strip()
                max_cw = cw_col - 14
                
                # Split cell text into up to 2 lines
                words = cell_text.split()
                c_lines = []
                c_cur = []
                c_w = 0
                for cw_word in words:
                    cww = c.stringWidth(_clean(cw_word) + " ", fn, 8.2)
                    if c_w + cww <= max_cw:
                        c_cur.append(cw_word)
                        c_w += cww
                    else:
                        if c_cur: c_lines.append(" ".join(c_cur))
                        c_cur = [cw_word]
                        c_w = cww
                if c_cur: c_lines.append(" ".join(c_cur))
                
                if len(c_lines) > 2:
                    c_lines = c_lines[:2]
                    while c_lines[1] and c.stringWidth(_clean(c_lines[1]), fn, 8.2) > max_cw:
                        c_lines[1] = c_lines[1][:-2] + "…"
                elif len(c_lines) == 1:
                    while c_lines[0] and c.stringWidth(_clean(c_lines[0]), fn, 8.2) > max_cw:
                        c_lines[0] = c_lines[0][:-2] + "…"
                        
                if len(c_lines) <= 1:
                    txt = c_lines[0] if c_lines else ""
                    cd, is_rtl = _bidi(txt, base_dir='R' if is_arabic_comp else 'L')
                    cw = c.stringWidth(cd, fn, 8.2)
                    cx_text = cx + cw_col / 2 - cw / 2 if ci == 0 else (cx + cw_col - cw - 7 if is_rtl else cx + 7)
                    c.drawString(cx_text, ry + row_h / 2 - 3, cd)
                else:
                    for li_c, cl_text in enumerate(c_lines):
                        cd, is_rtl = _bidi(cl_text, base_dir='R' if is_arabic_comp else 'L')
                        cw = c.stringWidth(cd, fn, 8.2)
                        cx_text = cx + cw_col / 2 - cw / 2 if ci == 0 else (cx + cw_col - cw - 7 if is_rtl else cx + 7)
                        line_y = ry + row_h - 14 - li_c * 13
                        c.drawString(cx_text, line_y, cd)

            # Data row vertical separators
            c.setStrokeColor(C_DIV)
            c.setLineWidth(0.3)
            for x_sep in sorted(col_xs)[1:]:
                c.line(x_sep, ry, x_sep, ry + row_h)

        st.y = top - (len(rows) + 1) * row_h - 18


# ── Main Entrypoint ───────────────────────────────────────────────────────────
def generate_aesthetic_notebook_pdf(
    output_path: str,
    continuous_markdown: str,
    student_name: str = "طالب متميز",
    lecture_title: str = "ملزمة دراسية ممتازة",
    cheat_sheet: Optional[list] = None,
    mcqs: Optional[list] = None,
    comparisons: Optional[list] = None,
    is_free_trial: bool = False,
    study_strategy: Optional[str] = None,
    tldr: Optional[list] = None,
    stats: Optional[dict] = None,
    language_mode: str = "bilingual",
) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv = canvas.Canvas(output_path, pagesize=A4)

    # 1. Cover Page
    _cover(cv, lecture_title, student_name, stats=stats, language_mode=language_mode)
    if is_free_trial:
        cv.saveState()
        cv.setFont("HW-Reg", 18)
        cv.setFillColor(HexColor("#888888"))
        cv.setFillAlpha(0.2)
        wm_msg = "Created by Free Trial - Subscribe to unlock full notebook" if language_mode == "english" else "Created by Free Trial - اشترك للحصول على الملزمة كاملة وبدون علامة"
        wm_text, _ = _bidi(wm_msg)
        cv.drawString(40, H / 2, wm_text)
        cv.restoreState()

    # 2. Main Content Init
    is_main_rtl = False if language_mode == "english" else (_has_ar(continuous_markdown[:2000]) if continuous_markdown else True)
    st = _S(page_num=2, student_name=student_name, is_rtl=is_main_rtl, is_free_trial=is_free_trial)

    # 2a. Study Strategy & TL;DR page (Page 2)
    _draw_study_strategy_and_tldr(cv, st, lecture_title, study_strategy=study_strategy, tldr=tldr)
    if is_free_trial:
        cv.saveState()
        cv.setFont("HW-Reg", 18)
        cv.setFillColor(HexColor("#888888"))
        cv.setFillAlpha(0.2)
        wm_text, _ = _bidi("Created by Free Trial - اشترك للحصول على الملزمة كاملة وبدون علامة")
        cv.drawString(40, H / 2, wm_text)
        cv.restoreState()

    # 2b. Continuous Markdown (Seamlessly flow onto Page 2 if space remains, otherwise new page)
    if st.y - 180 < BOTTOM:
        st.new_page(cv)
        st.y = H - MARGIN - 18
    else:
        st.y -= 14
        _divider(cv, st)
        st.y -= 10

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
    doc = None
    prev = None
    try:
        doc = fitz.open(original_pdf_path)
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
        logger.info(f"[NOTEBOOK] Preview PDF → {preview_pdf_path}")
        return preview_pdf_path
    except Exception as e:
        logger.error(f"[NOTEBOOK] Preview failed: {e}")
        return original_pdf_path
    finally:
        if prev and not prev.is_closed:
            prev.close()
        if doc and not doc.is_closed:
            doc.close()


# ── Course & Podcast Notebook Generator ────────────────────────────────────────
def _draw_workbook_lines(c, st, num_lines=3):
    c.saveState()
    c.setStrokeColor(HexColor("#D3C7B3"))
    c.setLineWidth(0.8)
    c.setDash([4, 4])  # dashed line style
    for _ in range(num_lines):
        if st.y - 26 < BOTTOM:
            st.new_page(c)
            c.setStrokeColor(HexColor("#D3C7B3"))
            c.setDash([4, 4])
        st.y -= 26
        c.line(MARGIN + 20, st.y, W - MARGIN - 20, st.y)
    c.restoreState()
    st.y -= 12


def _cover_podcast(c, title, student_name, stats: Optional[dict] = None):
    _bg(c, is_rtl=True)

    # Top decorative header bar
    c.setFillColor(HexColor("#111827")) # Dark charcoal/slate
    c.rect(0, H - 180, W, 180, fill=1, stroke=0)
    c.setFillColor(C_GOLD)
    c.rect(0, H - 184, W, 4, fill=1, stroke=0)

    # Brand name
    brand, _ = _bidi("دفتر لمّاح للدورات والبودكاست")
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 15)
    bw = c.stringWidth(brand, FONT_BOLD, 15)
    c.drawString(W / 2 - bw / 2, H - 90, brand)

    sub, _ = _bidi("نسخة متميزة للإنتاجية والتطوير الشخصي")
    c.setFont(FONT_REG, 10)
    sw = c.stringWidth(sub, FONT_REG, 10)
    c.setFillColor(HexColor("#FCD34D"))
    c.drawString(W / 2 - sw / 2, H - 120, sub)

    # Title
    c.setFillColor(C_BODY)
    c.setFont(FONT_BOLD, 18)
    clean_t = _clean(str(title))
    td, _ = _bidi(clean_t)
    tw = c.stringWidth(td, FONT_BOLD, 18)
    c.drawString(W / 2 - tw / 2, H - 240, td)

    # Underline
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(1.5)
    c.line(W / 2 - 100, H - 250, W / 2 + 100, H - 250)

    # Stats Panel
    panel_y = H - 280
    panel_lbl, _ = _bidi("مؤشرات المحتوى والإنتاجية (Content Insights)")
    c.setFont(FONT_BOLD, 9.5)
    c.setFillColor(C_MUTED)
    pl_w = c.stringWidth(panel_lbl, FONT_BOLD, 9.5)
    c.drawString(W / 2 - pl_w / 2, panel_y, panel_lbl)

    box_w, box_h = 165, 52
    col1_x = W / 2 - box_w - 6
    col2_x = W / 2 + 6
    row1_y = panel_y - 68
    row2_y = row1_y - 60

    if not stats:
        stats = {}
    num_takeaways = stats.get("num_takeaways", 5)
    num_concepts = stats.get("num_concepts", 4)
    num_action_steps = stats.get("num_action_steps", 3)
    est_read_time = stats.get("est_read_time", 10)

    cards = [
        (col2_x, row1_y, str(num_takeaways), "استنتاج رئيسي (Key Takeaways)"),
        (col1_x, row1_y, str(num_concepts), "مفهوم تم تفكيكه (Concepts)"),
        (col2_x, row2_y, str(num_action_steps), "خطوة عمل للتطبيق (Action Steps)"),
        (col1_x, row2_y, f"{est_read_time} دقيقة", "وقت القراءة المقدر (Reading Time)")
    ]

    for x, y, val, lbl in cards:
        c.setFillColor(C_BOX_BG)
        c.setStrokeColor(C_BOX_BD)
        c.setLineWidth(0.8)
        c.roundRect(x, y, box_w, box_h, 8, fill=1, stroke=1)
        
        accent_clr = C_TEAL if x == col1_x else C_GOLD
        c.setFillColor(accent_clr)
        c.rect(x + box_w - 4, y + 4, 3, box_h - 8, fill=1, stroke=0)
        
        val_d, _ = _bidi(val)
        c.setFont(FONT_BOLD, 15)
        c.setFillColor(C_BLUE)
        vw = c.stringWidth(val_d, FONT_BOLD, 15)
        c.drawString(x + box_w / 2 - vw / 2 - 2, y + 26, val_d)
        
        lbl_d, _ = _bidi(lbl)
        c.setFont(FONT_REG, 7.5)
        c.setFillColor(C_BODY)
        lw = c.stringWidth(lbl_d, FONT_REG, 7.5)
        c.drawString(x + box_w / 2 - lw / 2 - 2, y + 10, lbl_d)

    # Metadata
    bw2, bh2 = 330, 140
    bx = W / 2 - bw2 / 2
    by = 180
    c.setFillColor(C_BOX_BG)
    c.roundRect(bx, by, bw2, bh2, 12, fill=1, stroke=0)
    c.setStrokeColor(C_BOX_BD)
    c.setLineWidth(0.8)
    c.roundRect(bx, by, bw2, bh2, 12, fill=0, stroke=1)
    
    c.setFillColor(C_BLUE)
    c.rect(bx + bw2 - 5, by + 10, 4, bh2 - 20, fill=1, stroke=0)

    rows = [
        ("إعداد البائع:", student_name),
        ("المنصة الناشرة:", "منصة لمّاح الرقمية"),
        ("حقوق الملكية:", "محفوظة للبائع والمنصة"),
    ]
    for i, (lbl, val) in enumerate(rows):
        row_y = by + bh2 - 35 - i * 38
        lbl_d, _ = _bidi(lbl)
        val_d, _ = _bidi(val)
        c.setFont(FONT_BOLD, 10)
        c.setFillColor(C_BLUE)
        c.drawRightString(bx + bw2 - 14, row_y, lbl_d)
        c.setFont(FONT_REG, 9.5)
        c.setFillColor(C_BODY)
        c.drawRightString(bx + bw2 - 14, row_y - 15, val_d)

    note, _ = _bidi("صُنع بكل حب واحترافية بواسطة محرك لمّاح للإنتاجية")
    c.setFillColor(C_MUTED)
    c.setFont(FONT_REG, 8.5)
    nw = c.stringWidth(note, FONT_REG, 8.5)
    c.drawString(W / 2 - nw / 2, 42, note)
    c.showPage()


def generate_course_podcast_notebook_pdf(
    output_path: str,
    student_name: str = "طالب متميز",
    lecture_title: str = "دفتر إنتاجية مميز",
    is_free_trial: bool = False,
    big_picture: Optional[str] = None,
    concept_breakdown: Optional[list] = None,
    key_takeaways: Optional[list] = None,
    actionable_blueprint: Optional[list] = None,
    roadblocks_solutions: Optional[list] = None,
    reflection_prompts: Optional[list] = None,
    shareable_quotes: Optional[list] = None,
    resources_tools: Optional[list] = None,
    stats: Optional[dict] = None,
) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv = canvas.Canvas(output_path, pagesize=A4)

    # 1. Cover Page
    _cover_podcast(cv, lecture_title, student_name, stats=stats)
    if is_free_trial:
        cv.saveState()
        cv.setFont("HW-Reg", 18)
        cv.setFillColor(HexColor("#888888"))
        cv.setFillAlpha(0.2)
        wm_text, _ = _bidi("Created by Free Trial - اشترك للحصول على الملزمة كاملة وبدون علامة")
        cv.drawString(40, H / 2, wm_text)
        cv.restoreState()

    # 2. Main Content Setup
    st = _S(page_num=2, student_name=student_name, is_rtl=True, is_free_trial=is_free_trial)
    _bg(cv, is_rtl=True)
    st._footer(cv)

    # Section 1: الخلاصة الجوهرية (The Big Picture)
    if big_picture:
        _h1(cv, st, "الخلاصة الجوهرية (The Big Picture)")
        # Draw a beautiful highlighted callout box for the big picture
        box_h = 60 + (len(big_picture) // 50) * 16
        if st.y - box_h < BOTTOM: st.new_page(cv)
        box_y = st.y - box_h
        cv.setFillColor(HexColor("#FDFBF7"))
        cv.setStrokeColor(HexColor("#D9C9B5"))
        cv.setLineWidth(0.8)
        cv.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=1, stroke=1)
        
        cv.setFillColor(C_GOLD)
        cv.rect(W - MARGIN - 4, box_y + 4, 3, box_h - 8, fill=1, stroke=0)
        
        inner_s = _S(st.page_num, st.student_name)
        inner_s.y = box_y + box_h - 18
        _para(cv, big_picture, inner_s, font_sz=10.5, lh=20, color=C_BODY, indent=12, max_w=TXT_W - 24)
        st.y = box_y - 24

    # Section 2: تفكيك المفاهيم (Concept Breakdown)
    if concept_breakdown:
        _h1(cv, st, "تفكيك المفاهيم (Concept Breakdown)")
        for item in concept_breakdown:
            term = item.get("term", "")
            definition = item.get("definition", "")
            if not term: continue
            
            if st.y - 65 < BOTTOM: st.new_page(cv)
            
            # Subtitle
            _h2(cv, st, term)
            
            # Paragraph explanation
            _para(cv, definition, st, font_sz=10, lh=18, color=C_BODY, indent=12)
            st.y -= 14

    # Section 3: أهم الاستنتاجات (Key Takeaways)
    if key_takeaways:
        if st.y - 120 < BOTTOM: st.new_page(cv)
        _h1(cv, st, "أهم الاستنتاجات (Key Takeaways)")
        for take in key_takeaways:
            _para(cv, f"- {take}", st, font_sz=10.5, lh=20, color=C_BODY, indent=6)
        st.y -= 16

    # Section 4: خطة العمل والتطبيق (Actionable Blueprint)
    if actionable_blueprint:
        if st.y - 140 < BOTTOM: st.new_page(cv)
        _h1(cv, st, "خطة العمل والتطبيق (Actionable Blueprint)")
        for i, step in enumerate(actionable_blueprint):
            _para(cv, f"{i+1}. {step}", st, font_sz=10.5, lh=20, color=C_BODY, indent=6)
        st.y -= 16

    # Section 5: عقبات وحلول (Roadblocks & Solutions)
    if roadblocks_solutions:
        if st.y - 150 < BOTTOM: st.new_page(cv)
        _h1(cv, st, "عقبات وحلول (Roadblocks & Solutions)")
        for item in roadblocks_solutions:
            rb = item.get("roadblock", "")
            sol = item.get("solution", "")
            if not rb: continue
            
            box_h = 75 + (len(rb + sol) // 50) * 16
            if st.y - box_h < BOTTOM: st.new_page(cv)
            
            box_y = st.y - box_h
            cv.setFillColor(C_BOX_BG)
            cv.roundRect(MARGIN, box_y, TXT_W, box_h, 8, fill=1, stroke=0)
            cv.setStrokeColor(C_BOX_BD)
            cv.setLineWidth(0.8)
            cv.roundRect(MARGIN, box_y, TXT_W, box_h, 8, fill=0, stroke=1)
            
            # Accent bar
            cv.setFillColor(HexColor("#DC2626")) # Red accent for roadblock
            cv.rect(W - MARGIN - 4, box_y + 4, 3, box_h - 8, fill=1, stroke=0)
            
            inner_s = _S(st.page_num, st.student_name)
            inner_s.y = box_y + box_h - 18
            _para(cv, f"⚠️ **العقبة:** {rb}", inner_s, font_sz=9.5, lh=16, color=C_BODY, indent=12, max_w=TXT_W - 24)
            inner_s.y -= 8
            _para(cv, f"✅ **الحل:** {sol}", inner_s, font_sz=9.5, lh=16, color=C_TEAL, indent=12, max_w=TXT_W - 24)
            
            st.y = box_y - 18

    # Section 6: أسئلة للتفكير الذاتي (Reflection Prompts)
    if reflection_prompts:
        if st.y - 160 < BOTTOM: st.new_page(cv)
        _h1(cv, st, "أسئلة للتفكير الذاتي (Reflection Prompts)")
        for i, q in enumerate(reflection_prompts):
            if st.y - 120 < BOTTOM: st.new_page(cv)
            _para(cv, f"{i+1}. {q}", st, font_sz=11, lh=20, color=C_BLUE, bold=True, indent=6)
            st.y -= 6
            _draw_workbook_lines(cv, st, num_lines=3)
            st.y -= 8

    # Section 7: اقتباسات للإلهام (Shareable Quotes)
    if shareable_quotes:
        if st.y - 120 < BOTTOM: st.new_page(cv)
        _h1(cv, st, "اقتباسات للإلهام (Shareable Quotes)")
        for quote in shareable_quotes:
            box_h = 55 + (len(quote) // 50) * 16
            if st.y - box_h < BOTTOM: st.new_page(cv)
            
            box_y = st.y - box_h
            cv.setFillColor(HexColor("#FDFBF7"))
            cv.setStrokeColor(HexColor("#E3D9C9"))
            cv.setLineWidth(0.8)
            cv.roundRect(MARGIN, box_y, TXT_W, box_h, 6, fill=1, stroke=1)
            
            # Accent bar
            cv.setFillColor(C_GOLD)
            cv.rect(W - MARGIN - 4, box_y + 4, 3, box_h - 8, fill=1, stroke=0)
            
            inner_s = _S(st.page_num, st.student_name)
            inner_s.y = box_y + box_h - 18
            _para(cv, f"💬 “{quote}”", inner_s, font_sz=10, lh=18, color=HexColor("#5C4D3C"), bold=True, indent=12, max_w=TXT_W - 24)
            
            st.y = box_y - 18

    # Section 8: المراجع والأدوات (Resources & Tools)
    if resources_tools:
        if st.y - 100 < BOTTOM: st.new_page(cv)
        _h1(cv, st, "المراجع والأدوات (Resources & Tools)")
        for item in resources_tools:
            _para(cv, f"📌 {item}", st, font_sz=10.5, lh=20, color=C_BODY, indent=6)

    cv.save()
    logger.info(f"[NOTEBOOK] Podcast/Course PDF generated → {output_path}")
    return output_path
