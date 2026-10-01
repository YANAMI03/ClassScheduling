"""
room_utilization_pdf.py
=======================
NEUST College of ICT "Room Utilization / Section Schedule / Professor Schedule" PDF Generator.
Faithfully reproduces the official NEUST CICT HEADER_FOOTER format on Philippine Legal / Folio (8.5" x 13").

Features:
- Exact header: Dual logos (Bagong Pilipinas + NEUST Centennial Seal), vertical divider,
  official university title block, CICT seal, and full-width navy blue banner.
- Exact footer: Official NEUST Vision, Mission, Global globe, and accreditation badges banner,
  along with the 3-signatory approval block (Prepared by, Verified by, Approved by) and form code.
- Flexible modes: 'room', 'section', and 'professor'.
- Color-coded sessions: Lecture (#B8CCE4), Laboratory (#FFE699), HomeRoom (#F4CCAC).
- Robust timetable layout: Merged hour spans, graceful double-booking stacking,
  and automatic height scaling to prevent overlapping.
"""

import io
import os
import re
from datetime import datetime, timedelta

from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------------------------------------------------------------------------
# Font Registration (Windows TTF fonts with graceful Helvetica fallback)
# ---------------------------------------------------------------------------
_FONT_UNIV   = 'Helvetica-Bold'
_FONT_TITLE  = 'Helvetica-Bold'
_FONT_BODY   = 'Helvetica'
_FONT_ITALIC = 'Helvetica-Oblique'

try:
    if os.path.exists('C:/Windows/Fonts/trebucbd.ttf'):
        pdfmetrics.registerFont(TTFont('TrebuchetMS-Bold', 'C:/Windows/Fonts/trebucbd.ttf'))
        _FONT_UNIV = 'TrebuchetMS-Bold'
    if os.path.exists('C:/Windows/Fonts/arialbd.ttf'):
        pdfmetrics.registerFont(TTFont('Arial-Bold', 'C:/Windows/Fonts/arialbd.ttf'))
        _FONT_TITLE = 'Arial-Bold'
    if os.path.exists('C:/Windows/Fonts/arial.ttf'):
        pdfmetrics.registerFont(TTFont('Arial', 'C:/Windows/Fonts/arial.ttf'))
        _FONT_BODY = 'Arial'
    if os.path.exists('C:/Windows/Fonts/ariali.ttf'):
        pdfmetrics.registerFont(TTFont('Arial-Italic', 'C:/Windows/Fonts/ariali.ttf'))
        _FONT_ITALIC = 'Arial-Italic'
except Exception:
    pass

# ---------------------------------------------------------------------------
# Asset Paths (Extracted from official HEADER_FOOTER.pdf)
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(_HERE, 'static', 'images')

LOGO_HEADER_LOGOS   = os.path.join(_STATIC, 'pdf_header_logos.png')
LOGO_HEADER_DIVIDER = os.path.join(_STATIC, 'pdf_header_divider.png')
LOGO_CICT_SEAL      = os.path.join(_STATIC, 'pdf_cict_seal.png')
LOGO_FOOTER_BANNER  = os.path.join(_STATIC, 'pdf_footer_banner.png')

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
COL_LECTURE     = colors.HexColor('#B8CCE4')  # Soft gray-blue
COL_LAB         = colors.HexColor('#FFE699')  # Soft light yellow
COL_HOMEROOM    = colors.HexColor('#F4CCAC')  # Soft salmon / peach
COL_TIME_BG     = colors.HexColor('#F2F2F2')  # Neutral light gray for time col
COL_HDR_BG      = colors.HexColor('#D9D9D9')  # Light gray for table header
COL_UNIV_BLUE   = colors.HexColor('#001F5F')  # Deep navy for university header text
COL_BAR_BLUE    = colors.HexColor('#003366')  # Deep blue for full-width CICT banner
COL_FOOTER_LINE = colors.HexColor('#7E7E7E')  # Thin gray divider above footer banner

# ---------------------------------------------------------------------------
# Time Grid & Page Dimensions (Philippine Legal / Folio: 8.5" x 13" = 612 x 936 pt)
# ---------------------------------------------------------------------------
PAGE_WIDTH  = 612.0
PAGE_HEIGHT = 936.0

_START_H = 7    # 7:00 AM
_END_H   = 21   # 9:00 PM exclusive (14 slots)
_HOURS   = list(range(_START_H, _END_H))
_DAYS    = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

# ---------------------------------------------------------------------------
# Mode Titles & Labels
# ---------------------------------------------------------------------------
_MODE_LABEL = {
    'room':      'ROOM NUMBER:',
    'section':   'SECTION:',
    'professor': 'PROFESSOR:',
}
_MODE_TITLE = {
    'room':      'ROOM UTILIZATION',
    'section':   'CLASS SCHEDULE',
    'professor': 'TEACHING SCHEDULE',
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(val):
    """Normalize any value to a stripped string, converting None to empty string."""
    if val is None:
        return ''
    s = str(val).strip()
    return '' if s.lower() == 'none' else s


def _secs(val):
    """Convert any time-like value to seconds since midnight. Returns None on failure."""
    if val is None:
        return None
    if isinstance(val, timedelta):
        return int(val.total_seconds())
    if hasattr(val, 'hour'):
        return val.hour * 3600 + val.minute * 60 + getattr(val, 'second', 0)
    s = str(val).strip().upper()
    if not s:
        return None
    is_pm = 'PM' in s
    is_am = 'AM' in s
    clean = s.replace('AM', '').replace('PM', '').strip()
    clean = clean.split()[0] if clean.split() else clean
    try:
        parts = clean.split(':')
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if is_pm and h < 12:
            h += 12
        elif is_am and h == 12:
            h = 0
        return h * 3600 + m * 60
    except Exception:
        return None


def _fmt_h(h):
    """Format an integer hour (0-24) to a 12-hour string (e.g. 7 -> '7:00', 13 -> '1:00')."""
    display = h % 12
    if display == 0:
        display = 12
    return f"{display}:00"


def _row_label(h):
    """Row header label formatted with line breaks like the official printed sheet."""
    return f"{_fmt_h(h)}<br/>to<br/>{_fmt_h(h + 1)}"


def _sem_label(s):
    """Normalize semester string to standard display text."""
    if not s:
        return '1st Semester'
    s_clean = s.strip()
    if '1' in s_clean or 'first' in s_clean.lower():
        return '1st Semester'
    if '2' in s_clean or 'second' in s_clean.lower():
        return '2nd Semester'
    if 'mid' in s_clean.lower() or 'summer' in s_clean.lower():
        return 'Midyear'
    return s_clean


def _ay_from_sem(sem_str):
    """Derive academic year string (e.g. '2026-2027') from semester string or current date."""
    if sem_str:
        m = re.search(r'(\d{4})\s*[-–]\s*(\d{4})', str(sem_str))
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    y = datetime.now().year
    if datetime.now().month < 8:
        y -= 1
    return f"{y}-{y + 1}"


def _cell_color(session_type):
    """Map session type string to ReportLab Color object."""
    st = (session_type or '').strip().lower()
    if 'lab' in st:
        return COL_LAB
    if 'home' in st or 'advis' in st or 'consult' in st:
        return COL_HOMEROOM
    return COL_LECTURE


def _initials_surname(name):
    """Format instructor name to initials + surname (e.g. 'Andrew Caezar Villegas' -> 'ACAVillegas')."""
    if not name:
        return ''
    raw = str(name).strip()
    if not raw or raw.lower() == 'none':
        return ''

    # Separate suffix if any (e.g. Jr., III)
    suffix = ''
    m = re.search(r'[, ]+(Jr\.?|Sr\.?|III|II|IV)$', raw, flags=re.IGNORECASE)
    if m:
        suffix = ',' + m.group(1).strip()
        raw = raw[:m.start()].strip()

    tokens = [t for t in re.split(r'[\s.]+', raw) if t]
    if not tokens:
        return ''
    if len(tokens) == 1:
        return tokens[0] + suffix

    surname = tokens[-1]
    initials = ''.join(t[0].upper() for t in tokens[:-1])
    return f"{initials}{surname}{suffix}"


def _cell_text(entry, mode='room'):
    """
    Build the three-line cell text for a schedule entry.
    Lines are separated by newlines (\n) which are converted to <br/> in Paragraphs.
    mode 'room'      -> course / instructor / section
    mode 'section'   -> course / instructor / room
    mode 'professor' -> course / section / room
    """
    cn   = _safe(entry.get('course_name')) or 'TBA'
    prof = _initials_surname(_safe(entry.get('professor')))
    sec  = _safe(entry.get('section'))
    room = _safe(entry.get('room_name') or entry.get('room'))

    if mode == 'professor':
        return f"{cn}\n{sec}\n{room}"
    elif mode == 'section':
        return f"{cn}\n{prof}\n{room}"
    else:  # room (default)
        return f"{cn}\n{prof}\n{sec}"


# ---------------------------------------------------------------------------
# Header (Exact Reproduction of HEADER_FOOTER.pdf)
# ---------------------------------------------------------------------------

def _draw_header(c, pw=PAGE_WIDTH, ph=PAGE_HEIGHT):
    """
    Draw the exact official NEUST CICT header at the top of the page.
    Returns the bottom Y coordinate of the header block (841.7 pt).
    """
    # 1. Dual logos (Bagong Pilipinas + NEUST Centennial Seal) on left
    if os.path.exists(LOGO_HEADER_LOGOS):
        c.drawImage(LOGO_HEADER_LOGOS, 27.825, 860.66, width=112.15, height=63.445, mask='auto')

    # 2. Vertical decorative divider (Orange & Navy stripes)
    if os.path.exists(LOGO_HEADER_DIVIDER):
        c.drawImage(LOGO_HEADER_DIVIDER, 139.02, 859.70, width=12.595, height=76.30, mask='auto')

    # 3. CICT Official Seal on right
    if os.path.exists(LOGO_CICT_SEAL):
        c.drawImage(LOGO_CICT_SEAL, 503.79, 868.58, width=47.929, height=47.73, mask='auto')

    # 4. University Header Text
    c.setFillColor(COL_UNIV_BLUE)
    c.setFont(_FONT_BODY, 9.96)
    c.drawString(153.02, 899.04, 'Republic of the Philippines')

    to = c.beginText(153.02, 882.84)
    to.setFont(_FONT_UNIV, 15.96)
    to.setHorizScale(79.365)
    to.setFillColor(COL_UNIV_BLUE)
    to.textOut('NUEVA ECIJA UNIVERSITY OF SCIENCE AND TECHNOLOGY')
    c.drawText(to)

    c.setFont(_FONT_BODY, 9.96)
    c.drawString(154.22, 871.20, 'Cabanatuan City, Nueva Ecija')

    # 5. Full-width College Banner Bar
    c.setFillColor(COL_BAR_BLUE)
    c.rect(0, 841.80, pw, 19.95, fill=1, stroke=0)

    # 6. Banner White Text (centered across full page width)
    c.setFillColor(colors.white)
    c.setFont(_FONT_TITLE, 14.04)
    banner_txt = 'COLLEGE OF INFORMATION AND COMMUNICATIONS TECHNOLOGY'
    tw = c.stringWidth(banner_txt, _FONT_TITLE, 14.04)
    c.drawString((pw - tw) / 2, 846.36, banner_txt)

    # 7. Thin black border line below bar
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.75)
    c.line(0, 841.70, pw, 841.70)

    return 841.70


# ---------------------------------------------------------------------------
# Title Block
# ---------------------------------------------------------------------------

def _draw_title(c, pw, top_y, entity_label, sem_str, ay_str, mode='room'):
    """
    Draw the document title, semester, academic year, and entity label below the header.
    Returns the Y coordinate where the timetable table begins.
    """
    title_str = _MODE_TITLE.get(mode, 'ROOM UTILIZATION')
    label_prefix = _MODE_LABEL.get(mode, 'ROOM NUMBER:')

    # Mode Title (centered)
    c.setFillColor(colors.black)
    c.setFont(_FONT_TITLE, 11)
    tw = c.stringWidth(title_str, _FONT_TITLE, 11)
    c.drawString((pw - tw) / 2, top_y - 15.7, title_str)

    # Semester and Academic Year (centered, italic)
    c.setFont(_FONT_ITALIC, 9)
    sem_text = f"{_sem_label(sem_str)}, Academic Year {ay_str}"
    sw = c.stringWidth(sem_text, _FONT_ITALIC, 9)
    c.drawString((pw - sw) / 2, top_y - 29.7, sem_text)

    # Entity Label (left-aligned with table left edge at x = 36)
    c.setFont(_FONT_TITLE, 9)
    c.drawString(36.0, top_y - 45.7, f"{label_prefix}   {entity_label}")

    return top_y - 53.7


# ---------------------------------------------------------------------------
# Timetable Grid Builder
# ---------------------------------------------------------------------------

def _build_table(entries, mode='room', col_w=None, row_h=None):
    """
    Build the 14-hour-row x 7-column timetable Table.
    Robustly handles:
    - Normalization and deduplication of schedule records.
    - Merging contiguous / overlapping entries of the same class/color into clean multi-hour spans.
    - Resolving span collisions so text never bleeds into adjacent rows or overlaps.
    - Deduplicating stacked text lines so identical classes are never repeated in the same slot.
    - Dynamic font size and line leading computation per cell to guarantee text strictly fits within the cell boundaries.
    """
    _SPAN = object()

    # 1. Clean & normalize incoming entries
    seen = set()
    cleaned = []
    for e in entries:
        day = _safe(e.get('day')).strip().title()
        if day not in _DAYS:
            continue
        st = _secs(e.get('start_time_raw') or e.get('start_time'))
        et = _secs(e.get('end_time_raw') or e.get('end_time'))
        if st is None or et is None or et <= st:
            continue
        txt = _cell_text(e, mode)
        clr = _cell_color(e.get('session_type'))
        clr_key = clr.hexval() if hasattr(clr, 'hexval') else str(clr)
        key = (day, st, et, txt, clr_key)
        if key not in seen:
            seen.add(key)
            cleaned.append({'day': day, 'st': st, 'et': et, 'txt': txt, 'clr': clr})

    # 2. Merge contiguous / overlapping entries of the SAME class
    by_day = {d: [] for d in _DAYS}
    for e in cleaned:
        by_day[e['day']].append(e)

    merged_by_day = {d: [] for d in _DAYS}
    for d in _DAYS:
        day_list = sorted(by_day[d], key=lambda x: (x['st'], x['et']))
        merged = []
        for item in day_list:
            if not merged:
                merged.append(dict(item))
            else:
                prev = merged[-1]
                prev_hex = prev['clr'].hexval() if hasattr(prev['clr'], 'hexval') else str(prev['clr'])
                item_hex = item['clr'].hexval() if hasattr(item['clr'], 'hexval') else str(item['clr'])
                # If same text and color and contiguous or overlapping:
                if item['txt'] == prev['txt'] and item_hex == prev_hex and item['st'] <= prev['et']:
                    prev['et'] = max(prev['et'], item['et'])
                else:
                    merged.append(dict(item))
        merged_by_day[d] = merged

    # 3. Grid setup
    nr = len(_HOURS) + 1  # 15 rows: row 0 header, rows 1-14 hours
    nc = 1 + len(_DAYS)   # 7 cols: col 0 time, cols 1-6 days

    grid = [[''] * nc for _ in range(nr)]
    cgrid = [[None] * nc for _ in range(nr)]
    span_map = {}  # (anchor_row, col) -> rowspan

    # Headers
    grid[0][0] = 'Time'
    for ci, d in enumerate(_DAYS, 1):
        grid[0][ci] = d

    # Time column labels & background
    for ri, h in enumerate(_HOURS, 1):
        grid[ri][0] = _row_label(h)
        cgrid[ri][0] = COL_TIME_BG

    # 4. Place merged entries
    for ci, day in enumerate(_DAYS, 1):
        for item in merged_by_day[day]:
            st = item['st']
            et = item['et']
            txt = item['txt']
            clr = item['clr']

            rs = re_ = None
            for ri, h in enumerate(_HOURS, 1):
                h_start = h * 3600
                h_end = (h + 1) * 3600
                if st < h_end and et > h_start:
                    if rs is None:
                        rs = ri
                    re_ = ri

            if rs is None:
                continue

            rowspan = re_ - rs + 1

            # Determine anchor row
            if grid[rs][ci] is _SPAN:
                # Find the parent anchor row above rs
                anchor = rs
                for r in range(rs - 1, 0, -1):
                    if grid[r][ci] is not _SPAN:
                        anchor = r
                        break
            else:
                anchor = rs

            cell = grid[anchor][ci]
            if cell == '' or cell is None or cell is _SPAN:
                grid[anchor][ci] = txt
                cgrid[anchor][ci] = clr
                curr_span = span_map.get((anchor, ci), 1)
                new_span = min(nr - anchor, max(curr_span, (rs - anchor) + rowspan))
                span_map[(anchor, ci)] = new_span
                for r in range(anchor + 1, anchor + new_span):
                    grid[r][ci] = _SPAN
                    if cgrid[r][ci] is None:
                        cgrid[r][ci] = clr
            else:
                # Cell already has text — stack without duplicating
                existing = [x.strip() for x in cell.split('\n—\n')]
                if txt.strip() not in existing:
                    grid[anchor][ci] = cell + '\n—\n' + txt
                # Extend span if needed
                curr_span = span_map.get((anchor, ci), 1)
                new_span = min(nr - anchor, max(curr_span, (rs - anchor) + rowspan))
                span_map[(anchor, ci)] = new_span
                for r in range(anchor + 1, anchor + new_span):
                    grid[r][ci] = _SPAN
                    if cgrid[r][ci] is None:
                        cgrid[r][ci] = clr

    # 5. Column widths & row heights
    if col_w is None:
        tw = 48.0
        dw = (540.0 - tw) / 6.0
        col_w = [tw] + [dw] * 6

    if row_h is None:
        row_h = [18.0] + [37.0] * len(_HOURS)

    # 6. Build Table Data with Auto-fitting Paragraph Styles
    tdata = []
    base_hs = ParagraphStyle('hs', fontName=_FONT_TITLE, fontSize=8.0, leading=10.0, alignment=1)
    base_ts = ParagraphStyle('ts', fontName=_FONT_BODY,  fontSize=6.5, leading=8.0, alignment=1)

    for ri in range(nr):
        row = []
        for ci in range(nc):
            raw = grid[ri][ci]
            if raw is _SPAN:
                row.append('')
                continue
            text = '' if (raw is None or raw is _SPAN) else str(raw)
            text_html = text.replace('\n', '<br/>')

            if ri == 0:
                row.append(Paragraph(text_html, base_hs))
            elif ci == 0:
                row.append(Paragraph(text_html, base_ts))
            else:
                if not text.strip():
                    row.append('')
                    continue

                lines_count = text_html.count('<br/>') + 1
                curr_span = span_map.get((ri, ci), 1)
                hour_h = row_h[ri] if ri < len(row_h) else 37.0
                avail_h = max(10.0, (curr_span * hour_h) - 4.0)

                # Strict mathematical sizing: lines_count * ld <= avail_h to prevent any cell overflow
                if lines_count * 8.5 <= avail_h:
                    fs, ld = 7.0, 8.5
                elif lines_count * 7.5 <= avail_h:
                    fs, ld = 6.2, 7.5
                elif lines_count * 6.5 <= avail_h:
                    fs, ld = 5.4, 6.5
                elif lines_count * 5.5 <= avail_h:
                    fs, ld = 4.6, 5.5
                else:
                    ld = max(3.8, avail_h / float(lines_count))
                    fs = max(3.4, ld * 0.82)

                p_style = ParagraphStyle(
                    f'cs_{ri}_{ci}',
                    fontName=_FONT_BODY,
                    fontSize=fs,
                    leading=ld,
                    alignment=1
                )
                row.append(Paragraph(text_html, p_style))
        tdata.append(row)

    # 7. TableStyle Commands
    cmds = [
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.black),
        ('BACKGROUND',    (0, 0), (-1,  0), COL_HDR_BG),
        ('BACKGROUND',    (0, 1), ( 0, -1), COL_TIME_BG),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING',    (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING',   (0, 0), (-1, -1), 1),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 1),
    ]

    for ri in range(1, nr):
        for ci in range(1, nc):
            bg = cgrid[ri][ci]
            if bg is not None:
                cmds.append(('BACKGROUND', (ci, ri), (ci, ri), bg))

    for (anchor_r, anchor_c), span in span_map.items():
        if span > 1:
            end_r = min(nr - 1, anchor_r + span - 1)
            cmds.append(('SPAN', (anchor_c, anchor_r), (anchor_c, end_r)))
            bg = cgrid[anchor_r][anchor_c]
            if bg is not None:
                cmds.append(('BACKGROUND', (anchor_c, anchor_r), (anchor_c, end_r), bg))

    tbl = Table(tdata, colWidths=col_w, rowHeights=row_h, repeatRows=1)
    tbl.setStyle(TableStyle(cmds))
    return tbl, sum(col_w), sum(row_h)


# ---------------------------------------------------------------------------
# Footer & Signatures (Exact Reproduction of HEADER_FOOTER.pdf)
# ---------------------------------------------------------------------------

def _draw_footer(c, pw=PAGE_WIDTH, table_bottom_y=280.0):
    """
    Draw the 3-signatory approval block and the official Vision/Mission footer banner.
    Anchored cleanly above the gray divider rule and footer banner.
    """
    # 1. Signatures Block
    sig_y = min(table_bottom_y - 10.0, 260.0)
    cw = 540.0 / 3.0
    lw = 145.0
    x0 = 36.0

    c.setFillColor(colors.black)
    c.setFont(_FONT_BODY, 7.5)
    c.drawString(x0, sig_y, 'Prepared by:')
    c.drawString(x0 + cw * 2.0 + 5.0, sig_y, 'Verified by:')

    c.setFont(_FONT_TITLE, 7.5)
    c.drawString(x0, sig_y - 18.0, 'ANDREW CAEZAR A. VILLEGAS, MSIT')
    c.drawString(x0 + cw * 2.0 + 5.0, sig_y - 18.0, 'DR. RONALD S. SANTOS, REE')

    c.setLineWidth(0.5)
    c.line(x0, sig_y - 20.0, x0 + lw, sig_y - 20.0)
    c.line(x0 + cw * 2.0 + 5.0, sig_y - 20.0, x0 + cw * 2.0 + 5.0 + lw, sig_y - 20.0)

    c.setFont(_FONT_BODY, 7.0)
    c.drawString(x0, sig_y - 28.0, 'Program Chair/Head')
    c.drawString(x0 + cw * 2.0 + 5.0, sig_y - 28.0, 'College Dean/Campus Director')

    c.setFont(_FONT_ITALIC, 7.0)
    c.drawString(x0, sig_y - 36.0, 'Date Signed: _______________')
    c.drawString(x0 + cw * 2.0 + 5.0, sig_y - 36.0, 'Date Signed: _______________')

    ay = sig_y - 44.0
    c.setFont(_FONT_BODY, 7.5)
    abl = 'Approved by:'
    c.drawString((pw - c.stringWidth(abl, _FONT_BODY, 7.5)) / 2.0, ay, abl)

    c.setFont(_FONT_TITLE, 7.5)
    avp_name = 'ENGR. FELICIANA P. JACOBA, Ed.D.'
    c.drawString((pw - c.stringWidth(avp_name, _FONT_TITLE, 7.5)) / 2.0, ay - 16.0, avp_name)

    c.line((pw - lw) / 2.0, ay - 18.0, (pw + lw) / 2.0, ay - 18.0)

    c.setFont(_FONT_BODY, 7.0)
    avp_title = 'Vice President for Academic Affairs'
    c.drawString((pw - c.stringWidth(avp_title, _FONT_BODY, 7.0)) / 2.0, ay - 26.0, avp_title)

    c.setFont(_FONT_ITALIC, 7.0)
    ads = 'Date Signed: _______________'
    c.drawString((pw - c.stringWidth(ads, _FONT_ITALIC, 7.0)) / 2.0, ay - 34.0, ads)

    # 2. Form Revision Code at bottom left
    c.setFont(_FONT_BODY, 6.0)
    c.drawString(x0, 56.0, 'NEUST-AAF-F012')
    c.drawString(x0, 50.0, 'Rev.01 (10.29.2024)')

    # 3. Horizontal gray divider rule (from x = 68.0 to x = 548.9 at y = 49.8)
    c.setStrokeColor(COL_FOOTER_LINE)
    c.setLineWidth(0.5)
    c.line(68.0, 49.80, 548.9, 49.80)

    # 4. Official NEUST Vision, Mission, Global, and Accreditation Badges Banner
    if os.path.exists(LOGO_FOOTER_BANNER):
        c.drawImage(LOGO_FOOTER_BANNER, 0.35, -14.71, width=611.94, height=62.32, mask='auto')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_room_utilization_pdf(pages, semester=None, academic_year=None,
                                   # legacy kwarg kept for backwards compatibility
                                   rooms_entries=None):
    """
    Generate a multi-page NEUST-style PDF adhering to the official HEADER_FOOTER format.

    Args:
        pages: list of page-descriptor dicts:
            {
                'mode':    'room' | 'section' | 'professor',
                'label':   str,   # entity label (e.g. 'Rm 303', 'BSIT 1F', 'Juan Dela Cruz')
                'entries': list,  # schedule entries
                'semester': str,  # optional; derived from entries if omitted
                # legacy alias:
                'room_name': str  (-> label for mode='room')
            }
        semester:      optional global semester override
        academic_year: optional global AY override (e.g. '2026-2027')
        rooms_entries: legacy positional alias for `pages` (room-only usage)

    Returns:
        io.BytesIO containing the PDF.
    """
    if pages is None and rooms_entries is not None:
        pages = rooms_entries
    if pages is None:
        pages = []

    # Normalise legacy room-only dicts
    normalised = []
    for p in pages:
        d = dict(p)
        if 'label' not in d:
            d['label'] = d.get('room_name') or d.get('room') or 'Room'
        if 'mode' not in d:
            d['mode'] = 'room'
        normalised.append(d)
    pages = normalised

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))

    for page_data in pages:
        mode    = page_data.get('mode', 'room')
        label   = _safe(page_data.get('label')) or 'Room'
        entries = page_data.get('entries') or []

        # Determine semester
        sem_str = semester
        if not sem_str:
            for e in entries:
                if e.get('semester'):
                    sem_str = e['semester']
                    break
        if not sem_str:
            sem_str = page_data.get('semester') or ''
        sem_str = sem_str or '1st Semester'
        ay_str  = academic_year or _ay_from_sem(sem_str)

        # 1. Draw Header
        hdr_bottom = _draw_header(c, PAGE_WIDTH, PAGE_HEIGHT)

        # 2. Draw Title Block
        tbl_top = _draw_title(c, PAGE_WIDTH, hdr_bottom, label, sem_str, ay_str, mode)

        # 3. Available Height Budget:
        # Table starts at tbl_top. Signatures occupy ~105 pt above footer line (50 pt).
        min_tbl_bottom = 160.0
        avail_tbl_h = tbl_top - min_tbl_bottom

        # 4. Build Table
        tbl, tbl_w, tbl_h = _build_table(entries, mode)

        # If table height exceeds available space, scale row heights proportionally
        if tbl_h > avail_tbl_h and avail_tbl_h > 0:
            scale = avail_tbl_h / tbl_h
            new_rh = [18.0] + [max(14.0, 37.0 * scale)] * len(_HOURS)
            tbl, tbl_w, tbl_h = _build_table(entries, mode, row_h=new_rh)

        tbl_x = 36.0
        tbl_y = tbl_top - tbl_h
        tbl.wrapOn(c, 540.0, tbl_h)
        tbl.drawOn(c, tbl_x, tbl_y)

        # 5. Draw Footer & Signatures
        _draw_footer(c, PAGE_WIDTH, tbl_y)

        c.showPage()

    c.save()
    buf.seek(0)
    return buf
