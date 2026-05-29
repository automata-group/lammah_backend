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

def _has_ar(t): return bool(_AR.search(t))

def _bidi(text):
    if not text: return "", False
    if _has_ar(text):
        try:
            reshaped = arabic_reshaper.reshape(text)
            bidi_text = get_display(reshaped)
            # Remove ALL directional and invisible Unicode control characters
            _CTRL_CHARS = [
                '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',  # LRE, RLE, PDF, LRO, RLO
                '\u200e', '\u200f',  # LRM, RLM
                '\ufeff',            # BOM / ZWNBSP
                '\u200b', '\u200c', '\u200d',  # ZWSP, ZWNJ, ZWJ
                '\u2066', '\u2067', '\u2068', '\u2069',  # LRI, RLI, FSI, PDI
                '\u061c',  # Arabic Letter Mark
            ]
            for char in _CTRL_CHARS:
                bidi_text = bidi_text.replace(char, '')
            return bidi_text, True
        except Exception:
            return text, True
    return text, False

def _clean(s):
    return re.sub(r'\*\*|<mark>|</mark>|<span[^>]*>|</span>', '', s)

# ── Lined Paper Background ────────────────────────────────────────────────────
def _bg(c):
    c.setFillColor(C_BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setStrokeColor(C_LINE)
    c.setLineWidth(0.5)
    y = H - MARGIN - 28
    while y >= BOTTOM + 8:
        c.line(MARGIN - 12, y, W - MARGIN + 12, y)
        y -= 26
    # Margin line (RTL: right side)
    c.setStrokeColor(C_MARGIN)
    c.setLineWidth(1.0)
    c.line(W - MARGIN - 18, 0, W - MARGIN - 18, H)

# ── Page State ────────────────────────────────────────────────────────────────
class _S:
    def __init__(self, page_num=1, student_name="طالب متميز"):
        self.y = H - MARGIN - 18
        self.page_num = page_num
        self.student_name = student_name

    def new_page(self, c):
        c.showPage()
        self.page_num += 1
        _bg(c)
        self._footer(c)
        self.y = H - MARGIN - 18

    def _footer(self, c):
        # Separator line
        c.setStrokeColor(C_DIV)
        c.setLineWidth(0.5)
        c.line(MARGIN, BOTTOM - 8, W - MARGIN, BOTTOM - 8)
        c.setFont(FONT_REG, 7.5)
        c.setFillColor(C_MUTED)
        # Left: brand
        brand, _ = _bidi("✨ متجر لمّاح للملازم")
        c.drawString(MARGIN, BOTTOM - 20, brand)
        # Center: student
        attr, _ = _bidi(f"إعداد: {self.student_name}")
        aw = c.stringWidth(attr, FONT_REG, 7.5)
        c.drawString(W / 2 - aw / 2, BOTTOM - 20, attr)
        # Right: page
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
        pfx = ""
        if is_bullet:
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

        for li, line_str in enumerate(lines):
            if st.y < BOTTOM + lh: st.new_page(c)

            # Prefix (bullet / number)
            if li == 0 and pfx:
                pfx_d, _ = _bidi(pfx)
                c.setFillColor(C_GOLD)
                c.setFont(FONT_BOLD, font_sz + 1)
                px = MARGIN + indent + avail - bul_ind + 2
                c.drawString(px, st.y, pfx_d)

            # Highlights
            hl = [m.group(1) for m in re.finditer(r'<mark>(.*?)</mark>', line_str)]
            hl += [m.group(1) for m in re.finditer(r'<span[^>]*>(.*?)</span>', line_str)]

            clean_l = _clean(line_str)
            disp, is_rtl = _bidi(clean_l)
            is_bold_line  = line_str.count("**") >= 2
            dfn = FONT_BOLD if (bold or is_bold_line) else fn

            c.setFont(dfn, font_sz)
            lw = c.stringWidth(disp, dfn, font_sz)
            x_base = MARGIN + indent
            x = (x_base + avail_w - bul_ind - lw - 16) if is_rtl else (x_base + bul_ind)

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
    if st.y - 50 < BOTTOM: st.new_page(c)
    st.y -= 14
    t = _clean(str(title))[:65]
    td, _ = _bidi(t)
    c.setFont(FONT_BOLD, 15)
    tw = c.stringWidth(td, FONT_BOLD, 15)
    # Background banner
    c.setFillColor(C_BLUE)
    c.roundRect(MARGIN, st.y - 6, TXT_W, 26, 5, fill=1, stroke=0)
    c.setFillColor(HexColor("#FDFBF7"))
    c.drawString(W - MARGIN - tw - 22, st.y + 4, td)
    st.y -= 34

# ── Sub-heading (H2) ──────────────────────────────────────────────────────────
def _h2(c, st, title):
    if st.y - 40 < BOTTOM: st.new_page(c)
    st.y -= 8
    t = _clean(str(title))[:70]
    td, _ = _bidi(t)
    c.setFont(FONT_BOLD, 13)
    tw = c.stringWidth(td, FONT_BOLD, 13)
    c.setFillColor(C_GOLD)
    c.drawString(W - MARGIN - tw - 20, st.y, td)
    c.setStrokeColor(C_GOLD)
    c.setLineWidth(1.2)
    c.line(W - MARGIN - tw - 22, st.y - 3, W - MARGIN - 16, st.y - 3)
    st.y -= 22

# ── H3 (Exam Summary Header) ─────────────────────────────────────────────────
def _h3(c, st, title):
    if st.y - 30 < BOTTOM: st.new_page(c)
    st.y -= 6
    t = _clean(str(title))[:80]
    td, _ = _bidi(t)
    c.setFont(FONT_BOLD, 11.5)
    tw = c.stringWidth(td, FONT_BOLD, 11.5)
    c.setFillColor(C_TEAL)
    c.drawString(W - MARGIN - tw - 18, st.y, td)
    st.y -= 20

# ── Callout Box ───────────────────────────────────────────────────────────────
def _callout(c, st, text, kind="note"):
    """kind: 'note' | 'exam' | 'remember'"""
    # Auto-detect language: use English labels for English text, Arabic for Arabic
    is_arabic_text = _has_ar(text)
    if is_arabic_text:
        labels = {"exam": "⚠ هام للامتحان", "note": "💡 توضيح دراسي", "remember": "📌 تذكّر"}
    else:
        labels = {"exam": "⚠ Exam Alert", "note": "💡 Note", "remember": "📌 Remember"}
    colors  = {"exam": C_GOLD, "note": C_BLUE, "remember": C_TEAL}
    lbl     = labels.get(kind, "💡 Note" if not is_arabic_text else "💡 توضيح")
    bar_clr = colors.get(kind, C_BLUE)

    est_lines = max(2, len(text) // 50 + 2)
    box_h     = est_lines * 20 + 28
    if st.y - box_h < BOTTOM: st.new_page(c)

    box_y = st.y - box_h
    c.setFillColor(C_BOX_BG)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 7, fill=1, stroke=0)
    c.setStrokeColor(C_BOX_BD)
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, box_y, TXT_W, box_h, 7, fill=0, stroke=1)
    # Color left bar (RTL: right side)
    c.setFillColor(bar_clr)
    c.rect(W - MARGIN - 18, box_y + 5, 4, box_h - 10, fill=1, stroke=0)

    lbl_d, _ = _bidi(lbl)
    c.setFont(FONT_BOLD, 8.5)
    c.setFillColor(bar_clr)
    lw = c.stringWidth(lbl_d, FONT_BOLD, 8.5)
    c.drawString(W - MARGIN - lw - 24, box_y + box_h - 14, lbl_d)

    inner = _S(st.page_num, st.student_name)
    inner.y = box_y + box_h - 24
    _para(c, text, inner, font_sz=9.5, lh=19, color=C_BODY, indent=10, max_w=TXT_W - 24)
    st.y = box_y - 10

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

    for seg in txt.split("\n"):
        s = seg.strip()
        if not s: continue

        if s == "[[TABLE]]":
            try: _table(c, st, next(ti))
            except StopIteration: pass
            continue

        if s.startswith("---") or s.startswith("===") or s.startswith("***"):
            _divider(c, st)
            continue

        level = 0
        while level < len(s) and s[level] == "#":
            level += 1
        body = s[level:].strip() if level else s

        if level == 1:
            _h1(c, st, body)
        elif level == 2:
            _h2(c, st, body)
        elif level == 3:
            _h3(c, st, body)
        else:
            # Arabic callout keywords
            is_exam    = any(k in s for k in ["هام للامتحان", "⚠", "امتحان", "مهم", "ركز",
                                               "Exam Alert", "EXAM", "Important:", "WARNING:"])
            is_note    = any(k in s for k in ["💡", "ملاحظة", "توضيح",
                                               "Note:", "NOTE:", "Tip:", "TIP:"])
            is_rem     = any(k in s for k in ["📌", "تذكّر", "تذكر",
                                               "Remember:", "REMEMBER:", "Key Point:"])

            if is_exam and len(body) < 500:
                _callout(c, st, body, kind="exam")
            elif is_rem and len(body) < 500:
                _callout(c, st, body, kind="remember")
            elif is_note and len(body) < 500:
                _callout(c, st, body, kind="note")
            else:
                _para(c, body, st)

# ── Cover Page ────────────────────────────────────────────────────────────────
def _cover(c, title, student_name):
    _bg(c)

    # Top decorative header bar
    c.setFillColor(C_COVER_H)
    c.rect(0, H - 200, W, 200, fill=1, stroke=0)
    c.setFillColor(C_GOLD)
    c.rect(0, H - 204, W, 4, fill=1, stroke=0)

    # Brand name
    brand, _ = _bidi("✨ دفتر الطالب المتفوق الجامعي")
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
        ("حقوق الملكية:", "محفوظة للبائع والمنصة 🔒"),
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

    # New page with section header
    st.new_page(c)
    st.y -= 6

    # Section banner
    c.setFillColor(HexColor("#1B3A8A"))
    c.rect(MARGIN, st.y - 34, TXT_W, 34, fill=1, stroke=0)
    c.setFillColor(HexColor("#FCD34D"))
    c.rect(MARGIN, st.y - 38, TXT_W, 4, fill=1, stroke=0)
    title_d, _ = _bidi("⚡ خلاصة ليلة الامتحان")
    c.setFillColor(HexColor("#FDFBF7"))
    c.setFont(FONT_BOLD, 14)
    tw = c.stringWidth(title_d, FONT_BOLD, 14)
    c.drawString(W / 2 - tw / 2, st.y - 24, title_d)
    st.y -= 52

    sub_d, _ = _bidi("جميع المصطلحات والقوانين المهمة — راجعها قبل الامتحان مباشرةً")
    c.setFont(FONT_REG, 9)
    c.setFillColor(C_MUTED)
    sw = c.stringWidth(sub_d, FONT_REG, 9)
    c.drawString(W / 2 - sw / 2, st.y, sub_d)
    st.y -= 20

    # Table header row
    col_term = TXT_W * 0.35
    col_def  = TXT_W * 0.65
    row_h = 22

    def _draw_row(term_text, def_text, is_header=False):
        if st.y - row_h < BOTTOM: st.new_page(c)
        bg = HexColor("#D9CBBA") if is_header else (HexColor("#FDFBF7") if (items.index({"term": term_text, "brief_definition": def_text} if not is_header else {}) % 2 == 0) else HexColor("#F7F1E8"))
        try:
            idx_check = next((i for i, x in enumerate(items) if x.get("term") == term_text), -1)
            bg = HexColor("#D9CBBA") if is_header else (HexColor("#FDFBF7") if idx_check % 2 == 0 else HexColor("#F7F1E8"))
        except Exception:
            bg = HexColor("#F7F1E8")
        c.setFillColor(bg)
        c.rect(MARGIN, st.y - row_h, TXT_W, row_h, fill=1, stroke=0)
        c.setStrokeColor(C_DIV)
        c.setLineWidth(0.4)
        c.rect(MARGIN, st.y - row_h, TXT_W, row_h, fill=0, stroke=1)
        c.line(MARGIN + col_term, st.y - row_h, MARGIN + col_term, st.y)

        fn = FONT_BOLD if is_header else FONT_REG
        fc = C_BLUE if is_header else C_BODY
        c.setFillColor(fc)
        c.setFont(fn, 9.5 if is_header else 9)

        # Term (right side, RTL)
        td, _ = _bidi(str(term_text)[:40])
        tw2 = c.stringWidth(td, fn, 9.5 if is_header else 9)
        c.drawString(MARGIN + col_term - tw2 - 6, st.y - row_h + 7, td)

        # Definition (left side)
        dd, is_rtl = _bidi(str(def_text)[:80])
        dw = c.stringWidth(dd, fn, 9.5 if is_header else 9)
        x_def = MARGIN + col_term + col_def - dw - 8 if is_rtl else MARGIN + col_term + 6
        c.drawString(x_def, st.y - row_h + 7, dd)
        st.y -= row_h

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
    th1, _ = _bidi("المصطلح / القانون")
    th2, _ = _bidi("التعريف المختصر")
    tw1 = c.stringWidth(th1, FONT_BOLD, 10)
    tw2 = c.stringWidth(th2, FONT_BOLD, 10)
    c.drawString(MARGIN + col_term - tw1 - 6, st.y - row_h + 7, th1)
    c.drawString(MARGIN + col_term + col_def - tw2 - 8, st.y - row_h + 7, th2)
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

        # Highlight term
        c.setFillColor(C_MARK)
        c.roundRect(MARGIN + 3, st.y - row_h + 3, col_term - 8, row_h - 6, 3, fill=1, stroke=0)

        c.setFillColor(C_BODY)
        c.setFont(FONT_BOLD, 9)
        td, _ = _bidi(term)
        tw3 = c.stringWidth(td, FONT_BOLD, 9)
        c.drawString(MARGIN + col_term - tw3 - 6, st.y - row_h + 7, td)

        c.setFont(FONT_REG, 9)
        c.setFillColor(C_BODY)
        dd, is_rtl = _bidi(defn)
        dw = c.stringWidth(dd, FONT_REG, 9)
        x_def = MARGIN + col_term + col_def - dw - 8 if is_rtl else MARGIN + col_term + 6
        c.drawString(x_def, st.y - row_h + 7, dd)
        st.y -= row_h


# ── MCQ Quiz Page ──────────────────────────────────────────────────────────────
def _draw_mcqs(c, st, mcqs: list):
    """Renders 'اختبر نفسك' multiple choice questions."""
    if not mcqs: return

    st.new_page(c)
    st.y -= 6

    # Section banner
    c.setFillColor(C_TEAL)
    c.rect(MARGIN, st.y - 34, TXT_W, 34, fill=1, stroke=0)
    c.setFillColor(C_GOLD)
    c.rect(MARGIN, st.y - 38, TXT_W, 4, fill=1, stroke=0)
    title_d, _ = _bidi("🧪 اختبر نفسك — هل أنت جاهز للامتحان؟")
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
        c.setFillColor(C_TEAL)
        c.rect(W - MARGIN - 5, st.y - q_box_h + 4, 4, q_box_h - 8, fill=1, stroke=0)

        c.setFont(FONT_BOLD, 10.5)
        c.setFillColor(HexColor("#0F766E"))
        qd, _ = _bidi(q_text[:120])
        qw = c.stringWidth(qd, FONT_BOLD, 10.5)
        c.drawString(W - MARGIN - qw - 12, st.y - q_box_h + 10, qd)
        st.y -= q_box_h + 4

        # Choices
        choices = q.get("choices", [])
        for ch in choices:
            if st.y - 22 < BOTTOM: st.new_page(c)
            ch_d, _ = _bidi(str(ch)[:100])
            c.setFont(FONT_REG, 9.5)
            c.setFillColor(C_BODY)
            chw = c.stringWidth(ch_d, FONT_REG, 9.5)
            # Small circle bullet
            c.setFillColor(C_DIV)
            c.circle(W - MARGIN - 10, st.y - 7, 4, fill=1, stroke=0)
            c.setFillColor(C_BODY)
            c.drawString(W - MARGIN - chw - 20, st.y - 13, ch_d)
            st.y -= 22

        answers.append(f"{i+1}. {q.get('answer', '')}")
        st.y -= 10

    # Answers section (small, upside-down style - we'll just do small grey text)
    if answers:
        st.y -= 14
        if st.y - 30 < BOTTOM: st.new_page(c)
        ans_lbl, _ = _bidi("✅ مفتاح الإجابات:")
        c.setFont(FONT_BOLD, 8)
        c.setFillColor(C_MUTED)
        alw = c.stringWidth(ans_lbl, FONT_BOLD, 8)
        c.drawString(W - MARGIN - alw - 8, st.y, ans_lbl)
        st.y -= 16

        ans_text = "   |   ".join(answers)
        ans_d, _ = _bidi(ans_text)
        c.setFont(FONT_REG, 7.5)
        c.setFillColor(C_MUTED)
        adw = c.stringWidth(ans_d, FONT_REG, 7.5)
        c.drawString(W - MARGIN - adw - 8, st.y, ans_d)
        st.y -= 20


# ── Comparison Tables Page ────────────────────────────────────────────────────
def _draw_comparisons(c, st, comparisons: list):
    """Renders smart comparison tables extracted by the AI."""
    if not comparisons: return

    st.new_page(c)
    st.y -= 6

    # Section banner
    c.setFillColor(C_GOLD)
    c.rect(MARGIN, st.y - 34, TXT_W, 34, fill=1, stroke=0)
    c.setFillColor(C_BLUE)
    c.rect(MARGIN, st.y - 38, TXT_W, 4, fill=1, stroke=0)
    title_d, _ = _bidi("📊 جداول المقارنة الذكية")
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

        # "وجه المقارنة" label for first col
        aspect_lbl, _ = _bidi("وجه المقارنة")
        alw = c.stringWidth(aspect_lbl, FONT_BOLD, 9.5)
        c.drawString(MARGIN + col_w - alw - 6, hy + 8, aspect_lbl)
        c.setStrokeColor(HexColor("#FDFBF7"))
        c.setLineWidth(0.3)
        c.line(MARGIN + col_w, hy, MARGIN + col_w, hy + row_h)

        for hi, hdr in enumerate(headers[:ncols - 1]):
            hx = MARGIN + (hi + 1) * col_w
            hd, _ = _bidi(str(hdr)[:30])
            hw = c.stringWidth(hd, FONT_BOLD, 9.5)
            c.drawString(hx + col_w - hw - 6, hy + 8, hd)
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

    # 2. Main Content
    st = _S(page_num=2, student_name=student_name)
    _bg(cv)
    st._footer(cv)
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
