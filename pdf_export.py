import io
import os
import re
from datetime import datetime, timedelta

from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle, Paragraph, KeepTogether, Flowable
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
# Colors (Official NEUST Schedule Scheme)
# ---------------------------------------------------------------------------
COL_LECTURE     = colors.HexColor('#D9D9D9')  # Light gray fill for lecture classes
COL_LAB         = colors.HexColor('#FFE699')  # Light orange / soft amber fill for laboratory classes
COL_TIME_BG     = colors.HexColor('#F2F2F2')  # Neutral light gray for time column
COL_HDR_BG      = colors.HexColor('#D9D9D9')  # Light gray for table header
COL_UNIV_BLUE   = colors.HexColor('#001F5F')  # Deep navy for university header text
COL_BAR_BLUE    = colors.HexColor('#003366')  # Deep blue for full-width CICT banner
COL_FOOTER_LINE = colors.HexColor('#7E7E7E')  # Thin gray divider above footer banner

# ---------------------------------------------------------------------------
# Page Dimensions (Philippine Legal / Folio: 8.5" x 13" = 612 x 936 pt, Portrait)
# ---------------------------------------------------------------------------
PAGE_WIDTH  = 612.0
PAGE_HEIGHT = 936.0

_BASE_START_H = 7   # 7:00 AM (PRD operational start)
_BASE_END_H   = 20  # 8:00 PM (PRD operational end: 13 one-hour slots)
_DAYS         = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

# ---------------------------------------------------------------------------
# Mode Titles & Labels
# ---------------------------------------------------------------------------
_MODE_TITLE = {
    'professor': 'PROFESSOR SCHEDULE',
    'teacher':   'PROFESSOR SCHEDULE',
    'section':   'SECTION SCHEDULE',
    'room':      'ROOM UTILIZATION',
}

_MODE_LABEL = {
    'professor': 'PROFESSOR:',
    'teacher':   'PROFESSOR:',
    'section':   'SECTION:',
    'room':      'ROOM NUMBER:',
}


# ---------------------------------------------------------------------------
# Time & String Parsing Utilities
# ---------------------------------------------------------------------------

def _parse_time_to_seconds(val):
    """Convert any time-like value to total seconds from midnight.
    Handles timedelta, datetime.time, 12-hour strings ('01:00 PM'), and 24-hour strings ('13:00:00').
    """
    if val is None:
        return None
    if isinstance(val, timedelta):
        return int(val.total_seconds())
    if hasattr(val, 'hour'):
        return val.hour * 3600 + val.minute * 60 + getattr(val, 'second', 0)

    val_str = str(val).strip().upper()
    if not val_str:
        return None

    is_pm = 'PM' in val_str
    is_am = 'AM' in val_str

    clean = val_str.replace('AM', '').replace('PM', '').strip()
    if ' ' in clean:
        clean = clean.split(' ', 1)[0]

    try:
        parts = clean.split(':')
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0

        if is_pm:
            if hour < 12:
                hour += 12
        elif is_am:
            if hour == 12:
                hour = 0

        return hour * 3600 + minute * 60 + second
    except (ValueError, IndexError):
        return None


def _seconds_to_display_time(seconds):
    """Convert seconds from midnight to 12-hour 'HH:MM AM/PM' string."""
    if seconds is None:
        return ''
    seconds = int(seconds) % 86400
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    suffix = 'AM' if hours < 12 else 'PM'
    hour_12 = hours % 12 or 12
    return f"{hour_12:02d}:{minutes:02d} {suffix}"


def _split_course_code_name(course_code):
    """Split course string into code and title, e.g. 'IT101 - Intro to Computing' -> ('IT101', 'Intro to Computing')."""
    if not course_code:
        return 'TBA', ''
    c_str = str(course_code).strip()
    if ' - ' in c_str:
        parts = c_str.split(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    match = re.match(r'^([A-Z]{2,5}[-\s]?\d{2,4}[A-Z]?)\s+(.*)$', c_str, re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return c_str, ''


def _safe_str(val):
    """Normalize any value to stripped string, converting None/'none'/'null' to empty string."""
    if val is None:
        return ''
    s = str(val).strip()
    return '' if s.lower() in ('none', 'null') else s


def _fmt_h(h):
    """Format an integer hour (0-24) to a 12-hour string (e.g. 7 -> '7:00', 13 -> '1:00')."""
    display = h % 12
    if display == 0:
        display = 12
    return f"{display}:00"


def _row_label(h):
    """Row header label formatted with line breaks like the official printed sheet."""
    return f"{_fmt_h(h)}<br/>to<br/>{_fmt_h(h + 1)}"


def _normalize_semester(s):
    """Normalize semester string to standard NEUST display text."""
    if not s:
        return '1st Semester'
    s_clean = str(s).strip()
    if '1' in s_clean or 'first' in s_clean.lower():
        return '1st Semester'
    if '2' in s_clean or 'second' in s_clean.lower():
        return '2nd Semester'
    if 'mid' in s_clean.lower() or 'summer' in s_clean.lower():
        return 'Midyear'
    return s_clean


def _normalize_academic_year(ay_str, sem_str=None):
    """Extract or derive academic year string (e.g. '2026-2027')."""
    for candidate in (ay_str, sem_str):
        if candidate:
            m = re.search(r'(\d{4})\s*[-–]\s*(\d{4})', str(candidate))
            if m:
                return f"{m.group(1)}-{m.group(2)}"
    y = datetime.now().year
    if datetime.now().month < 8:
        y -= 1
    return f"{y}-{y + 1}"


def _format_instructor_name(name):
    """Format instructor name cleanly; uses initials+surname for long names."""
    if not name:
        return 'TBA'
    raw = str(name).strip()
    if not raw or raw.lower() in ('none', 'null'):
        return 'TBA'

    # Separate suffix if any (e.g. Jr., III)
    suffix = ''
    m = re.search(r'[, ]+(Jr\.?|Sr\.?|III|II|IV)$', raw, flags=re.IGNORECASE)
    if m:
        suffix = ', ' + m.group(1).strip()
        raw = raw[:m.start()].strip()

    tokens = [t for t in re.split(r'[\s.]+', raw) if t]
    if not tokens:
        return 'TBA'
    if len(tokens) == 1:
        return tokens[0] + suffix

    if len(raw) <= 16:
        return raw + suffix

    surname = tokens[-1]
    initials = ''.join(t[0].upper() for t in tokens[:-1])
    return f"{initials}{surname}{suffix}"


def _cell_color(session_type):
    """Map session type string to ReportLab Color object.
    Laboratory -> Light Orange, Lecture / other -> Light Gray.
    """
    st = (session_type or '').strip().lower()
    if 'lab' in st:
        return COL_LAB
    return COL_LECTURE


def _cell_text(entry, mode='room'):
    """
    Build the three-line centered cell text for a schedule entry.
    Lines are separated by newlines (\n) converted to <br/> in Paragraphs.

    Line 1: Course code (e.g. IT-WS07)
    Line 2: Professor name (section export) / Section (professor export)
    Line 3: Room name (or Section if room TBA)
    """
    c_raw = _safe_str(entry.get('course_code') or entry.get('course'))
    code, _ = _split_course_code_name(c_raw)
    course_code = code if code else (c_raw or 'TBA')

    prof = _format_instructor_name(_safe_str(entry.get('professor') or entry.get('professor_name')))
    sec = _safe_str(entry.get('section')) or 'TBA'
    room = _safe_str(entry.get('room_name') or entry.get('room')) or 'TBA'

    if mode == 'professor':
        return f"{course_code}\n{sec}\n{room}"
    elif mode == 'section':
        return f"{course_code}\n{prof}\n{room}"
    else:  # room
        return f"{course_code}\n{prof}\n{sec}"


# ---------------------------------------------------------------------------
# Header (Exact Reproduction of HEADER_FOOTER.pdf)
# ---------------------------------------------------------------------------

def _draw_header(c, pw=PAGE_WIDTH, ph=PAGE_HEIGHT):
    """
    Draw the exact official NEUST CICT header at the top of the page.
    Returns the bottom Y coordinate of the header block (841.70 pt).
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
    Draw document title, semester, academic year, and entity label below header.
    Returns the Y coordinate where the timetable grid begins.
    """
    norm_mode = 'professor' if mode in ('professor', 'teacher') else ('section' if mode == 'section' else 'room')
    title_str = _MODE_TITLE.get(norm_mode, 'ROOM UTILIZATION')
    label_prefix = _MODE_LABEL.get(norm_mode, 'ROOM NUMBER:')

    # Mode Title (centered)
    c.setFillColor(colors.black)
    c.setFont(_FONT_TITLE, 11)
    tw = c.stringWidth(title_str, _FONT_TITLE, 11)
    c.drawString((pw - tw) / 2, top_y - 15.7, title_str)

    # Semester and Academic Year (centered, italic)
    c.setFont(_FONT_ITALIC, 9)
    sem_text = f"{_normalize_semester(sem_str)}, Academic Year {_normalize_academic_year(ay_str, sem_str)}"
    sw = c.stringWidth(sem_text, _FONT_ITALIC, 9)
    c.drawString((pw - sw) / 2, top_y - 29.7, sem_text)

    # Entity Label (left-aligned with table left edge at x = 36.0)
    c.setFont(_FONT_TITLE, 9)
    c.drawString(36.0, top_y - 45.7, f"{label_prefix}   {entity_label}")

    return top_y - 53.7


# ---------------------------------------------------------------------------
# Timetable Grid Builder
# ---------------------------------------------------------------------------

def _build_timetable_grid(entries, mode='room', hours=None, col_w=None, row_h=None):
    """
    Build the weekly timetable Table.
    - Resolves multi-hour spans with single merged cells (rowspan).
    - Prevents overflow and double-booking overlaps.
    - Dynamically computes font size and line height to fit cell boundaries.
    """
    _SPAN = object()
    norm_mode = 'professor' if mode in ('professor', 'teacher') else ('section' if mode == 'section' else 'room')

    if hours is None:
        hours = list(range(_BASE_START_H, _BASE_END_H))

    # 1. Clean & normalize incoming entries
    seen = set()
    cleaned = []
    for e in entries:
        day = _safe_str(e.get('day')).strip().title()
        if day not in _DAYS:
            continue
        raw_start = e.get('start_time_raw') or e.get('start_time') or e.get('class_start') or e.get('start')
        raw_end   = e.get('end_time_raw') or e.get('end_time') or e.get('class_end') or e.get('end')
        st = _parse_time_to_seconds(raw_start)
        et = _parse_time_to_seconds(raw_end)
        if st is None or et is None or et <= st:
            continue
        txt = _cell_text(e, norm_mode)
        clr = _cell_color(e.get('session_type'))
        clr_key = clr.hexval() if hasattr(clr, 'hexval') else str(clr)
        key = (day, st, et, txt, clr_key)
        if key not in seen:
            seen.add(key)
            cleaned.append({'day': day, 'st': st, 'et': et, 'txt': txt, 'clr': clr})

    # 2. Merge contiguous / overlapping entries of the same class
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
                if item['txt'] == prev['txt'] and item_hex == prev_hex and item['st'] <= prev['et']:
                    prev['et'] = max(prev['et'], item['et'])
                else:
                    merged.append(dict(item))
        merged_by_day[d] = merged

    # 3. Grid setup
    nr = len(hours) + 1  # Header row + hour rows
    nc = 1 + len(_DAYS)  # Time column + 6 day columns

    grid = [[''] * nc for _ in range(nr)]
    cgrid = [[None] * nc for _ in range(nr)]
    span_map = {}

    grid[0][0] = 'Time'
    for ci, d in enumerate(_DAYS, 1):
        grid[0][ci] = d

    for ri, h in enumerate(hours, 1):
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
            for ri, h in enumerate(hours, 1):
                h_start = h * 3600
                h_end   = (h + 1) * 3600
                if st < h_end and et > h_start:
                    if rs is None:
                        rs = ri
                    re_ = ri

            if rs is None:
                continue

            rowspan = re_ - rs + 1

            if grid[rs][ci] is _SPAN:
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
                existing = [x.strip() for x in cell.split('\n—\n')]
                if txt.strip() not in existing:
                    grid[anchor][ci] = cell + '\n—\n' + txt
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
        base_h = 40.0 if len(hours) <= 13 else max(24.0, (520.0 / len(hours)))
        row_h = [18.0] + [base_h] * len(hours)

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
                hour_h = row_h[ri] if ri < len(row_h) else 40.0
                avail_h = max(10.0, (curr_span * hour_h) - 4.0)

                # Strict mathematical sizing: lines_count * ld <= avail_h to prevent cell overflow
                if lines_count * 9.0 <= avail_h:
                    fs, ld = 7.5, 9.0
                elif lines_count * 8.0 <= avail_h:
                    fs, ld = 6.8, 8.0
                elif lines_count * 7.0 <= avail_h:
                    fs, ld = 6.0, 7.0
                elif lines_count * 6.0 <= avail_h:
                    fs, ld = 5.2, 6.0
                else:
                    ld = max(4.0, avail_h / float(lines_count))
                    fs = max(3.5, ld * 0.82)

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
# SafeKeepTogether and Flowable Signatures Area
# ---------------------------------------------------------------------------

class SafeKeepTogether(KeepTogether):
    """
    Subclass of ReportLab KeepTogether that supports direct canvas drawing via wrapOn/drawOn,
    while guaranteeing that isinstance(obj, KeepTogether) is True and preventing signature
    blocks from splitting across pages.
    """
    def wrapOn(self, canv, aW, aH):
        w, h = 0, 0
        for f in self._content:
            fw, fh = f.wrapOn(canv, aW, aH)
            w = max(w, fw)
            h += fh
        self.width, self.height = w, h
        return w, h

    def drawOn(self, canv, x, y, _sW=0):
        curr_y = y + self.height
        for f in self._content:
            fh = getattr(f, 'height', 0)
            curr_y -= fh
            f.drawOn(canv, x, curr_y)


class SignatureAreaFlowable(Flowable):
    """
    Flowable rendering the full signature area (Prepared by blocks, Verified by, Approved by).
    Arranges blocks side-by-side or stacked according to the number of program preparers,
    autoscaling font sizes to prevent text overflow.
    """
    def __init__(self, preparers=None, pw=PAGE_WIDTH, max_w=145.0):
        super().__init__()
        self.preparers = preparers or [{'name': None, 'title': 'Program Scheduler - BSIT'}]
        self.pw = pw
        self.lw = max_w
        self.width = 540.0

        num_prep = len(self.preparers)
        if num_prep <= 2:
            self.height = 84.0
        else:
            extra_rows = (num_prep - 1) // 2
            self.height = 84.0 + extra_rows * 44.0

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def _draw_block(self, canv, x, y, header, name, title, max_w, show_date=True):
        canv.setFillColor(colors.black)
        canv.setFont(_FONT_BODY, 7.5)
        canv.drawString(x, y, header)

        # Name line & underline
        line_y = y - 20.0
        name_clean = str(name).strip().upper() if name else ''
        if name_clean:
            base_size = 7.5
            w = canv.stringWidth(name_clean, _FONT_TITLE, base_size)
            if w > max_w and w > 0:
                base_size = max(4.5, base_size * (max_w / w))
            canv.setFont(_FONT_TITLE, base_size)
            canv.drawString(x, y - 18.0, name_clean)

        # Draw signature line
        canv.setLineWidth(0.5)
        canv.line(x, line_y, x + max_w, line_y)

        # Title line
        title_clean = str(title).strip() if title else ''
        if title_clean:
            t_size = 7.0
            tw = canv.stringWidth(title_clean, _FONT_BODY, t_size)
            if tw > max_w and tw > 0:
                t_size = max(4.5, t_size * (max_w / tw))
            canv.setFont(_FONT_BODY, t_size)
            canv.drawString(x, y - 28.0, title_clean)

        # Date Signed line
        if show_date:
            canv.setFont(_FONT_ITALIC, 7.0)
            canv.drawString(x, y - 36.0, 'Date Signed: _______________')

    def _draw_approved_by(self, c, ay, lw):
        x_appr = (self.width - lw) / 2.0
        abl = 'Approved by:'
        c.setFont(_FONT_BODY, 7.5)
        c.drawString((self.width - c.stringWidth(abl, _FONT_BODY, 7.5)) / 2.0, ay, abl)

        c.setFont(_FONT_TITLE, 7.5)
        avp_name = 'ENGR. FELICIANA P. JACOBA, Ed.D.'
        c.drawString((self.width - c.stringWidth(avp_name, _FONT_TITLE, 7.5)) / 2.0, ay - 16.0, avp_name)

        c.setLineWidth(0.5)
        c.line(x_appr, ay - 18.0, x_appr + lw, ay - 18.0)

        c.setFont(_FONT_BODY, 7.0)
        avp_title = 'Vice President for Academic Affairs'
        c.drawString((self.width - c.stringWidth(avp_title, _FONT_BODY, 7.0)) / 2.0, ay - 26.0, avp_title)

        c.setFont(_FONT_ITALIC, 7.0)
        ads = 'Date Signed: _______________'
        c.drawString((self.width - c.stringWidth(ads, _FONT_ITALIC, 7.0)) / 2.0, ay - 34.0, ads)

    def draw(self):
        c = self.canv
        num_prep = len(self.preparers)
        lw = self.lw
        y_top = self.height

        if num_prep == 1:
            cw = 540.0 / 3.0
            x_prep = 0.0
            x_ver = cw * 2.0 + 5.0

            p = self.preparers[0]
            self._draw_block(c, x_prep, y_top, 'Prepared by:', p.get('name'), p.get('title'), lw)
            self._draw_block(c, x_ver, y_top, 'Verified by:', 'DR. RONALD S. SANTOS, REE', 'College Dean/Campus Director', lw)

            ay = y_top - 46.0
            self._draw_approved_by(c, ay, lw)

        elif num_prep == 2:
            cw = 540.0 / 3.0
            col_lw = min(lw, cw - 10.0)
            x_p1 = 0.0
            x_p2 = cw
            x_ver = cw * 2.0

            p1, p2 = self.preparers[0], self.preparers[1]
            self._draw_block(c, x_p1, y_top, 'Prepared by:', p1.get('name'), p1.get('title'), col_lw)
            self._draw_block(c, x_p2, y_top, 'Prepared by:', p2.get('name'), p2.get('title'), col_lw)
            self._draw_block(c, x_ver, y_top, 'Verified by:', 'DR. RONALD S. SANTOS, REE', 'College Dean/Campus Director', col_lw)

            ay = y_top - 46.0
            self._draw_approved_by(c, ay, lw)

        else:
            cw = 540.0 / 3.0
            col_lw = min(lw, cw - 10.0)

            # Row 1: Preparer 1, Preparer 2, Verified by
            self._draw_block(c, 0.0, y_top, 'Prepared by:', self.preparers[0].get('name'), self.preparers[0].get('title'), col_lw)
            self._draw_block(c, cw, y_top, 'Prepared by:', self.preparers[1].get('name'), self.preparers[1].get('title'), col_lw)
            self._draw_block(c, cw * 2.0, y_top, 'Verified by:', 'DR. RONALD S. SANTOS, REE', 'College Dean/Campus Director', col_lw)

            # Subsequent rows of preparers
            curr_y = y_top - 44.0
            idx = 2
            while idx < num_prep:
                p_a = self.preparers[idx]
                self._draw_block(c, 0.0, curr_y, 'Prepared by:', p_a.get('name'), p_a.get('title'), col_lw)
                if idx + 1 < num_prep:
                    p_b = self.preparers[idx + 1]
                    self._draw_block(c, cw, curr_y, 'Prepared by:', p_b.get('name'), p_b.get('title'), col_lw)
                curr_y -= 44.0
                idx += 2

            ay = curr_y - 4.0
            self._draw_approved_by(c, ay, lw)


# ---------------------------------------------------------------------------
# Footer & Signatures (Exact Reproduction of HEADER_FOOTER.pdf)
# ---------------------------------------------------------------------------

def _draw_footer(c, pw=PAGE_WIDTH, table_bottom_y=250.0, preparers=None):
    """
    Draw approval block wrapped in KeepTogether and official Vision/Mission footer banner.
    """
    # 1. Signatures Block wrapped in SafeKeepTogether
    sig_flowable = SignatureAreaFlowable(preparers=preparers, pw=pw)
    kt = SafeKeepTogether([sig_flowable])
    w, h = kt.wrapOn(c, 540.0, 300.0)

    # Position so top of signatures block aligns with sig_y
    sig_y = min(table_bottom_y - 12.0, 240.0)
    draw_y = sig_y - h
    kt.drawOn(c, 36.0, draw_y)

    # 2. Form Revision Code at bottom left
    x0 = 36.0
    c.setFillColor(colors.black)
    c.setFont(_FONT_BODY, 6.0)
    c.drawString(x0, 56.0, 'NEUST-AAF-F012')
    c.drawString(x0, 50.0, 'Rev.01 (10.29.2024)')

    # 3. Horizontal gray divider rule (from x = 68.0 to x = 548.9 at y = 49.80)
    c.setStrokeColor(COL_FOOTER_LINE)
    c.setLineWidth(0.5)
    c.line(68.0, 49.80, 548.9, 49.80)

    # 4. Official NEUST Vision, Mission, Global, and Accreditation Badges Banner
    if os.path.exists(LOGO_FOOTER_BANNER):
        c.drawImage(LOGO_FOOTER_BANNER, 0.35, -14.71, width=611.94, height=62.32, mask='auto')


def _extract_preparers(schedule_type, entity_info, entries, filter_metadata):
    """
    Extracts deduplicated preparer blocks for the schedule.
    - Section timetable: uses the preparer for that section's program.
    - Room & Professor: produces one block per distinct program appearing in the data.
    """
    entries = entries or []
    filter_metadata = filter_metadata or {}
    norm_type = 'professor' if schedule_type in ('professor', 'teacher') else ('section' if schedule_type == 'section' else 'room')

    if norm_type == 'section':
        prog = (filter_metadata.get('program') or
                (entity_info.get('program') if isinstance(entity_info, dict) else None) or
                (entity_info.get('program_name') if isinstance(entity_info, dict) else None) or
                (entries[0].get('program') if entries else None) or
                'BSIT')
        
        name = None
        title = None
        for e in entries:
            if e.get('preparer_name'):
                name = e.get('preparer_name')
            if e.get('preparer_title'):
                title = e.get('preparer_title')
            if name or title:
                break
        
        if not title:
            title = f"Program Scheduler - {prog}"
        
        return [{'name': name, 'title': title, 'program': prog}]

    else:
        # Multi-program views: Room or Professor
        prog_order = []
        entries_by_prog = {}
        for e in entries:
            p = e.get('program')
            if not p:
                sec = str(e.get('section') or '').strip()
                if sec:
                    p = sec.split()[0].split('-')[0]
            if not p:
                p = filter_metadata.get('program') or 'BSIT'
            p = str(p).strip().upper()
            if p not in entries_by_prog:
                entries_by_prog[p] = []
                prog_order.append(p)
            entries_by_prog[p].append(e)

        if not prog_order:
            default_p = filter_metadata.get('program') or 'BSIT'
            return [{'name': None, 'title': f"Program Scheduler - {default_p}", 'program': default_p}]

        preparers = []
        for p_code in prog_order:
            p_entries = entries_by_prog[p_code]
            p_name = None
            p_title = None
            for e in p_entries:
                if e.get('preparer_name'):
                    p_name = e.get('preparer_name')
                if e.get('preparer_title'):
                    p_title = e.get('preparer_title')
                if p_name or p_title:
                    break
            
            if not p_title:
                p_title = f"Program Scheduler - {p_code}"
            
            preparers.append({
                'name': p_name,
                'title': p_title,
                'program': p_code,
            })

        return preparers


# ---------------------------------------------------------------------------
# Public Unified Timetable PDF Generator
# ---------------------------------------------------------------------------

def generate_timetable_pdf(schedule_type, entity_info, entries, working_hours=None, filter_metadata=None):
    """
    Generate an official NEUST timetable PDF adhering to the official HEADER_FOOTER format.
    Fits strictly on ONE portrait page (Philippine Folio 8.5" x 13" = 612 x 936 pt).

    Args:
        schedule_type: 'professor', 'teacher', 'section', or 'room'
        entity_info: dict or obj with name, type, etc.
        entries: list of schedule entry dictionaries
        working_hours: optional list of working-hours records from database
        filter_metadata: optional dict containing filter parameters (semester, school_year, year, major, etc.)

    Returns:
        io.BytesIO: Binary PDF stream seeked to 0
    """
    filter_metadata = filter_metadata or {}
    entries = entries or []
    working_hours = working_hours or []

    norm_type = 'professor' if schedule_type in ('professor', 'teacher') else ('section' if schedule_type == 'section' else 'room')

    # Resolve entity label
    if norm_type == 'professor':
        if isinstance(entity_info, dict):
            first = _safe_str(entity_info.get('first_name'))
            last  = _safe_str(entity_info.get('last_name'))
            prof_name = f"{first} {last}".strip() or _safe_str(entity_info.get('professor_name')) or 'Professor'
        else:
            prof_name = _safe_str(entity_info) or 'Professor'

        # Compute total assigned hours
        total_secs = 0
        for e in entries:
            st = _parse_time_to_seconds(e.get('start_time_raw') or e.get('start_time') or e.get('class_start') or e.get('start'))
            et = _parse_time_to_seconds(e.get('end_time_raw') or e.get('end_time') or e.get('class_end') or e.get('end'))
            if st is not None and et is not None and et > st:
                total_secs += (et - st)
        total_hours = round(total_secs / 3600.0, 1)
        if total_hours > 0:
            hrs_disp = int(total_hours) if total_hours.is_integer() else total_hours
            entity_label = f"{prof_name}   (Total Hours: {hrs_disp} hrs)"
        else:
            entity_label = prof_name
    elif norm_type == 'section':
        prog = filter_metadata.get('program') or ''
        if isinstance(entity_info, dict):
            sec_name = _safe_str(entity_info.get('section_name') or entity_info.get('section')) or 'Section'
            prog = _safe_str(entity_info.get('program') or entity_info.get('program_name') or prog)
        else:
            sec_name = _safe_str(entity_info) or 'Section'

        if prog and not sec_name.upper().startswith(prog.upper()) and len(sec_name) <= 6:
            entity_label = f"{prog} {sec_name}"
        else:
            entity_label = sec_name
    else:  # room
        if isinstance(entity_info, dict):
            room_name = _safe_str(entity_info.get('room_name') or entity_info.get('room')) or 'Room'
        else:
            room_name = _safe_str(entity_info) or 'Room'
        entity_label = room_name

    # Resolve semester and academic year
    sem_str = filter_metadata.get('semester')
    if not sem_str and entries:
        for e in entries:
            if e.get('semester'):
                sem_str = e['semester']
                break
    if not sem_str and isinstance(entity_info, dict):
        sem_str = entity_info.get('semester')
    sem_str = sem_str or '1st Semester'

    ay_str = filter_metadata.get('school_year') or _normalize_academic_year(None, sem_str)

    # Extract dynamic preparers per program
    preparers = _extract_preparers(norm_type, entity_info, entries, filter_metadata)

    # Determine hour range: default PRD range is 7:00 AM to 8:00 PM (hours 7 to 19, ending at 20:00)
    start_h = _BASE_START_H
    end_h   = _BASE_END_H

    # If any entry falls outside 7..20, expand dynamically
    for e in entries:
        raw_start = e.get('start_time_raw') or e.get('start_time') or e.get('class_start') or e.get('start')
        raw_end   = e.get('end_time_raw') or e.get('end_time') or e.get('class_end') or e.get('end')
        st = _parse_time_to_seconds(raw_start)
        et = _parse_time_to_seconds(raw_end)
        if st is not None and st >= 0:
            start_h = min(start_h, st // 3600)
        if et is not None and et > 0:
            end_h = max(end_h, (et + 3599) // 3600)

    hours = list(range(start_h, end_h))

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=0)

    # 1. Draw Header
    hdr_bottom = _draw_header(c, PAGE_WIDTH, PAGE_HEIGHT)

    # 2. Draw Title Block
    tbl_top = _draw_title(c, PAGE_WIDTH, hdr_bottom, entity_label, sem_str, ay_str, norm_type)

    # 3. Available Height Budget:
    # Signatures need ~120 pt above footer rule (50 pt). Adapt to number of preparers.
    sig_block = SignatureAreaFlowable(preparers=preparers, pw=PAGE_WIDTH)
    sig_h = sig_block.height
    min_tbl_bottom = max(175.0, 56.0 + sig_h + 15.0)
    avail_tbl_h = tbl_top - min_tbl_bottom

    # 4. Build Table
    tbl, tbl_w, tbl_h = _build_timetable_grid(entries, norm_type, hours=hours)

    if tbl_h > avail_tbl_h and avail_tbl_h > 0:
        scale = avail_tbl_h / tbl_h
        base_h = 40.0 if len(hours) <= 13 else max(24.0, (520.0 / len(hours)))
        new_rh = [18.0] + [max(14.0, base_h * scale)] * len(hours)
        tbl, tbl_w, tbl_h = _build_timetable_grid(entries, norm_type, hours=hours, row_h=new_rh)

    tbl_x = 36.0
    tbl_y = tbl_top - tbl_h
    tbl.wrapOn(c, 540.0, tbl_h)
    tbl.drawOn(c, tbl_x, tbl_y)

    # 5. Draw Footer & Signatures
    _draw_footer(c, PAGE_WIDTH, tbl_y, preparers=preparers)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf
