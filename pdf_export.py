import io
import re
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and print 'Page X of Y' on all pages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            super().showPage()
        super().save()

    def draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        footer_text = f"Class Scheduling System  •  Page {self._pageNumber} of {page_count}"
        self.drawRightString(792 - 26, 14, footer_text)
        self.drawString(26, 14, "Official Institutional Timetable  •  Confidential")
        self.restoreState()


def _parse_time_to_seconds(val):
    """Convert any time-like value to total seconds from midnight.
    Correctly handles timedelta, time, 12-hour strings ('01:00 PM'), and 24-hour strings ('13:00:00').
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


def _split_course_code_name(course_name):
    """Split course string into code and title, e.g. 'IT101 - Intro to Computing' -> ('IT101', 'Intro to Computing')."""
    if not course_name:
        return 'TBA', ''
    c_str = str(course_name).strip()
    if ' - ' in c_str:
        parts = c_str.split(' - ', 1)
        return parts[0].strip(), parts[1].strip()
    match = re.match(r'^([A-Z]{2,5}[-\s]?\d{2,4}[A-Z]?)\s+(.*)$', c_str, re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return c_str, ''


def generate_timetable_pdf(schedule_type, entity_info, entries, timeslots=None, filter_metadata=None):
    """Generate a high-quality landscape PDF timetable for Room, Section, or Teacher/Professor schedule.

    Args:
        schedule_type: 'room', 'section', or 'professor'
        entity_info: dict or obj with name, type, department, etc.
        entries: list of schedule entry dictionaries
        timeslots: optional list of timeslot records from database
        filter_metadata: optional dict containing active filter parameters (semester, year, major, etc.)

    Returns:
        io.BytesIO: Binary PDF stream
    """
    filter_metadata = filter_metadata or {}
    timeslots = timeslots or []
    entries = entries or []

    # 1. Setup Document Template (Landscape Letter: 792 x 612 pt)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=26,
        rightMargin=26,
        topMargin=20,
        bottomMargin=24,
    )

    # 2. Setup Styles
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=16,
        textColor=colors.HexColor('#0F172A'),
    )

    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.2,
        leading=9.5,
        textColor=colors.HexColor('#475569'),
    )

    col_header_style = ParagraphStyle(
        'ColHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=1,  # Centered
        textColor=colors.white,
    )

    time_cell_style = ParagraphStyle(
        'TimeCell',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=6.8,
        leading=8.5,
        alignment=1,  # Centered
        textColor=colors.HexColor('#1E293B'),
    )

    cell_course_code_style = ParagraphStyle(
        'CellCourseCode',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=6.8,
        leading=8,
        textColor=colors.HexColor('#0F172A'),
    )

    cell_detail_style = ParagraphStyle(
        'CellDetail',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=6.0,
        leading=7.2,
        textColor=colors.HexColor('#1E293B'),
    )

    cell_tag_style = ParagraphStyle(
        'CellTag',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=5.8,
        leading=7.0,
        textColor=colors.HexColor('#1D4ED8'),
    )

    lunch_cell_style = ParagraphStyle(
        'LunchCell',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=6.5,
        leading=8,
        alignment=1,
        textColor=colors.HexColor('#94A3B8'),
    )

    # 3. Resolve Titles & Metadata based on schedule_type
    school_year = filter_metadata.get('school_year') or "A.Y. 2026-2027"
    semester = (filter_metadata.get('semester') or '').strip()
    year_level = (str(filter_metadata.get('year') or '')).strip()
    major = (filter_metadata.get('major') or '').strip()
    program = (filter_metadata.get('program') or '').strip()

    if schedule_type == 'room':
        doc_heading = "ROOM SCHEDULE"
        room_name = entity_info.get('room_name') if isinstance(entity_info, dict) else str(entity_info)
        room_type = entity_info.get('room_type', 'Lecture') if isinstance(entity_info, dict) else 'Lecture'
        sub_heading = f"Room: {room_name}  ({room_type})"
        meta_items = [
            ("Semester", semester or "All Semesters"),
            ("Academic Year", school_year),
            ("Room Type", room_type),
            ("Program", program or "All Programs"),
            ("Scheduled Classes", f"{len(entries)} class{'es' if len(entries) != 1 else ''}"),
        ]
    elif schedule_type == 'section':
        doc_heading = "SECTION SCHEDULE"
        sec_name = entity_info.get('section_name') or entity_info.get('section') if isinstance(entity_info, dict) else str(entity_info)
        sec_year = entity_info.get('year_level') if isinstance(entity_info, dict) else year_level
        sec_sem = entity_info.get('semester') if isinstance(entity_info, dict) else semester
        sec_maj = entity_info.get('major') if isinstance(entity_info, dict) else major
        sub_heading = f"Section: {sec_name}"
        meta_items = [
            ("Year Level", f"Year {sec_year}" if sec_year else (f"Year {year_level}" if year_level else "N/A")),
            ("Semester", sec_sem or semester or "All Semesters"),
            ("Major", sec_maj or major or "General / Core"),
            ("Academic Year", school_year),
            ("Weekly Classes", f"{len(entries)} class{'es' if len(entries) != 1 else ''}"),
        ]
    elif schedule_type in ('teacher', 'professor'):
        doc_heading = "TEACHER SCHEDULE"
        if isinstance(entity_info, dict):
            p_first = entity_info.get('first_name', '')
            p_last = entity_info.get('last_name', '')
            prof_name = f"{p_first} {p_last}".strip() or entity_info.get('professor_name', 'Professor')
            dept = entity_info.get('department', 'N/A')
        else:
            prof_name = str(entity_info)
            dept = 'N/A'
        sub_heading = f"Professor: {prof_name}"
        meta_items = [
            ("Department", dept),
            ("Semester", semester or "All Semesters"),
            ("Academic Year", school_year),
            ("Assigned Classes", f"{len(entries)} class{'es' if len(entries) != 1 else ''}"),
            ("Program", program or "All Programs"),
        ]
    else:
        doc_heading = "CLASS SCHEDULE"
        sub_heading = "Schedule Details"
        meta_items = [("Semester", semester or "All Semesters"), ("Academic Year", school_year)]

    # 4. Determine Time Window and Intervals
    earliest_sec = 7 * 3600   # 7:00 AM
    latest_sec = 19 * 3600    # 7:00 PM
    lunch_sec = 12 * 3600     # 12:00 PM

    for ts in timeslots:
        s_val = _parse_time_to_seconds(ts.get('start_time'))
        e_val = _parse_time_to_seconds(ts.get('end_time'))
        l_val = _parse_time_to_seconds(ts.get('lunch_time'))
        if s_val is not None:
            earliest_sec = min(earliest_sec, s_val)
        if e_val is not None and e_val > earliest_sec:
            latest_sec = max(latest_sec, e_val)
        if l_val is not None:
            lunch_sec = l_val

    # Normalize parsed entries
    parsed_entries = []
    for e in entries:
        raw_start = e.get('start_time_raw') or e.get('class_start') or e.get('start') or e.get('start_time')
        raw_end = e.get('end_time_raw') or e.get('class_end') or e.get('end') or e.get('end_time')
        st_sec = _parse_time_to_seconds(raw_start)
        et_sec = _parse_time_to_seconds(raw_end)
        if st_sec is not None and et_sec is not None and et_sec > st_sec:
            earliest_sec = min(earliest_sec, st_sec)
            latest_sec = max(latest_sec, et_sec)
            c_code, c_title = _split_course_code_name(e.get('course_name'))
            parsed_entries.append({
                'day': (e.get('day') or '').strip().title(),
                'start_sec': st_sec,
                'end_sec': et_sec,
                'start_fmt': _seconds_to_display_time(st_sec),
                'end_fmt': _seconds_to_display_time(et_sec),
                'course_code': e.get('course_code') or c_code,
                'course_name': c_title or (c_code if not c_title else ''),
                'raw_course': e.get('course_name') or 'TBA',
                'professor': e.get('professor') or e.get('professor_name') or 'TBA',
                'room': e.get('room') or e.get('room_name') or 'TBA',
                'section': e.get('section') or 'TBA',
                'session_type': e.get('session_type') or 'Lecture',
            })

    # Hourly slot generation
    hourly_slots = []
    slot_curr = earliest_sec
    while slot_curr + 3600 <= latest_sec:
        slot_next = slot_curr + 3600
        is_lunch = (slot_curr <= lunch_sec < slot_next)
        s_lbl = f"{_seconds_to_display_time(slot_curr)} - {_seconds_to_display_time(slot_next)}"
        hourly_slots.append({
            'start_sec': slot_curr,
            'end_sec': slot_next,
            'label': s_lbl,
            'is_lunch': is_lunch,
        })
        slot_curr = slot_next

    if not hourly_slots:
        for h in range(7, 19):
            s_sec = h * 3600
            e_sec = (h + 1) * 3600
            hourly_slots.append({
                'start_sec': s_sec,
                'end_sec': e_sec,
                'label': f"{_seconds_to_display_time(s_sec)} - {_seconds_to_display_time(e_sec)}",
                'is_lunch': (h == 12),
            })

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

    # 5. Build Timetable Matrix
    table_data = []

    # Row 0: Header
    header_row = [Paragraph("<b>TIME</b>", col_header_style)]
    for day in days:
        header_row.append(Paragraph(f"<b>{day.upper()}</b>", col_header_style))
    table_data.append(header_row)

    entries_by_day = {d: [] for d in days}
    for pe in parsed_entries:
        if pe['day'] in entries_by_day:
            entries_by_day[pe['day']].append(pe)

    num_slots = len(hourly_slots)
    grid_matches = [[[] for _ in range(len(days))] for _ in range(num_slots)]

    for d_idx, day in enumerate(days):
        for s_idx, slot in enumerate(hourly_slots):
            s_start = slot['start_sec']
            s_end = slot['end_sec']
            for pe in entries_by_day[day]:
                if pe['start_sec'] < s_end and s_start < pe['end_sec']:
                    grid_matches[s_idx][d_idx].append(pe)

    # Track spanned cells to avoid duplicate content in subordinate rows
    spanned_cells = set()
    span_commands = []

    for s_idx, slot in enumerate(hourly_slots):
        for d_idx, day in enumerate(days):
            col_idx = d_idx + 1
            if (s_idx, d_idx) in spanned_cells:
                continue

            matches = grid_matches[s_idx][d_idx]
            if len(matches) == 1:
                single_entry = matches[0]
                # Check if this exact single entry spans into subsequent contiguous rows
                end_s_idx = s_idx
                while end_s_idx + 1 < num_slots:
                    next_matches = grid_matches[end_s_idx + 1][d_idx]
                    if len(next_matches) == 1 and next_matches[0] == single_entry:
                        end_s_idx += 1
                    else:
                        break

                if end_s_idx > s_idx:
                    for r_sub in range(s_idx + 1, end_s_idx + 1):
                        spanned_cells.add((r_sub, d_idx))
                    start_table_row = s_idx + 1
                    end_table_row = end_s_idx + 1
                    span_commands.append(('SPAN', (col_idx, start_table_row), (col_idx, end_table_row)))

    # Now populate table_data rows
    for s_idx, slot in enumerate(hourly_slots):
        row_cells = [Paragraph(slot['label'], time_cell_style)]

        for d_idx, day in enumerate(days):
            col_idx = d_idx + 1
            if (s_idx, d_idx) in spanned_cells:
                row_cells.append("")
                continue

            matches = grid_matches[s_idx][d_idx]
            if matches:
                cell_flowables = []
                for idx_m, m in enumerate(matches):
                    if idx_m > 0:
                        cell_flowables.append(Spacer(1, 2))

                    code_txt = f"<b>{m['course_code']}</b>"
                    if m['course_name']:
                        code_txt += f"  <font size=5.6 color='#334155'>{m['course_name'][:26]}</font>"
                    cell_flowables.append(Paragraph(code_txt, cell_course_code_style))

                    if schedule_type == 'room':
                        line2 = f"Sec: <b>{m['section']}</b>  •  Prof: <b>{m['professor']}</b>"
                    elif schedule_type == 'section':
                        line2 = f"Prof: <b>{m['professor']}</b>  •  Room: <b>{m['room']}</b>"
                    elif schedule_type in ('teacher', 'professor'):
                        line2 = f"Sec: <b>{m['section']}</b>  •  Room: <b>{m['room']}</b>"
                    else:
                        line2 = f"Sec: {m['section']} • Room: {m['room']} • Prof: {m['professor']}"

                    cell_flowables.append(Paragraph(line2, cell_detail_style))

                    line3 = f"{m['session_type']}  |  {m['start_fmt']} - {m['end_fmt']}"
                    cell_flowables.append(Paragraph(line3, cell_tag_style))

                row_cells.append(cell_flowables)
            elif slot['is_lunch']:
                row_cells.append(Paragraph("LUNCH BREAK", lunch_cell_style))
            else:
                row_cells.append("")

        table_data.append(row_cells)

    # 6. Column Widths & Table Styling
    col_widths = [76] + [110] * 6

    t_style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#F8FAFC')),
        ('VALIGN', (0, 1), (0, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]

    for s_idx, slot in enumerate(hourly_slots):
        table_row_idx = s_idx + 1
        for d_idx, day in enumerate(days):
            col_idx = d_idx + 1
            matches = grid_matches[s_idx][d_idx]
            if matches:
                t_style.append(('BACKGROUND', (col_idx, table_row_idx), (col_idx, table_row_idx), colors.HexColor('#EFF6FF')))
                t_style.append(('VALIGN', (col_idx, table_row_idx), (col_idx, table_row_idx), 'TOP'))
            elif slot['is_lunch']:
                t_style.append(('BACKGROUND', (col_idx, table_row_idx), (col_idx, table_row_idx), colors.HexColor('#F1F5F9')))

    for span_cmd in span_commands:
        t_style.append(span_cmd)

    timetable_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    timetable_table.setStyle(TableStyle(t_style))

    # 7. Header Layout Construction
    now_str = datetime.now().strftime("%b %d, %Y  %I:%M %p")

    header_table_data = [
        [
            Paragraph(f"<font size=7.5 color='#1D4ED8'><b>CLASS SCHEDULING MANAGEMENT SYSTEM</b></font><br/><b><font size=13 color='#0F172A'>{doc_heading}</font></b><br/><font size=9.5 color='#1D4ED8'><b>{sub_heading}</b></font>", title_style),
            Paragraph(f"<font color='#64748B'><b>Export Date:</b></font><br/>{now_str}<br/><font color='#16A34A'><b>Official Timetable</b></font>", ParagraphStyle('RHead', parent=meta_label_style, alignment=2))
        ]
    ]
    header_table = Table(header_table_data, colWidths=[550, 186])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))

    meta_cells = []
    for label, val in meta_items:
        txt = f"<font color='#475569'><b>{label}:</b></font>  <font color='#0F172A'><b>{val}</b></font>"
        meta_cells.append(Paragraph(txt, meta_label_style))

    while len(meta_cells) < 5:
        meta_cells.append(Paragraph("", meta_label_style))

    meta_table = Table([meta_cells[:5]], colWidths=[736 / 5] * 5)
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    # 8. Assemble Elements
    story = [
        header_table,
        Spacer(1, 3),
        meta_table,
        Spacer(1, 6),
        timetable_table,
    ]

    # 9. Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer
