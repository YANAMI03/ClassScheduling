import sys
sys.path.insert(0, '.')
from room_utilization_pdf import _secs, _safe, _cell_text, _cell_color, _DAYS, _HOURS
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas
import fitz

def build_clean_table(entries, mode='professor'):
    _SPAN = object()

    # 1. Clean & normalize incoming entries
    # Deduplicate exact entries
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
        key = (day, st, et, txt, clr.hexval())
        if key not in seen:
            seen.add(key)
            cleaned.append({'day': day, 'st': st, 'et': et, 'txt': txt, 'clr': clr})

    # 2. Merge contiguous/overlapping entries of the SAME class
    by_day = {d: [] for d in _DAYS}
    for e in cleaned:
        by_day[e['day']].append(e)

    merged_by_day = {d: [] for d in _DAYS}
    for d in _DAYS:
        day_list = sorted(by_day[d], key=lambda x: (x['st'], x['et']))
        merged = []
        for item in day_list:
            if not merged:
                merged.append(item)
            else:
                prev = merged[-1]
                # If same text & color and contiguous or overlapping
                if item['txt'] == prev['txt'] and item['clr'].hexval() == prev['clr'].hexval() and item['st'] <= prev['et']:
                    prev['et'] = max(prev['et'], item['et'])
                else:
                    merged.append(item)
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

    for ri, h in enumerate(_HOURS, 1):
        grid[ri][0] = f"{h % 12 or 12}:00<br/>to<br/>{(h + 1) % 12 or 12}:00"
        cgrid[ri][0] = colors.HexColor('#F2F2F2')

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
                new_span = max(curr_span, (rs - anchor) + rowspan)
                span_map[(anchor, ci)] = new_span
                for r in range(anchor + 1, anchor + new_span):
                    if r < nr:
                        grid[r][ci] = _SPAN
                        cgrid[r][ci] = clr
            else:
                # Cell already has text — stack without duplicating
                existing = [x.strip() for x in cell.split('\n—\n')]
                if txt.strip() not in existing:
                    grid[anchor][ci] = cell + '\n—\n' + txt
                # Extend span if needed
                curr_span = span_map.get((anchor, ci), 1)
                new_span = max(curr_span, (rs - anchor) + rowspan)
                span_map[(anchor, ci)] = new_span
                for r in range(anchor + 1, anchor + new_span):
                    if r < nr:
                        grid[r][ci] = _SPAN
                        if cgrid[r][ci] is None:
                            cgrid[r][ci] = clr

    # 5. Build Table Data with Auto-fitting Paragraph Styles
    tdata = []
    base_hs = ParagraphStyle('hs', fontName='Helvetica-Bold', fontSize=8.0, leading=10.0, alignment=1)
    base_ts = ParagraphStyle('ts', fontName='Helvetica', fontSize=6.5, leading=8.0, alignment=1)

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
                # Dynamic font size based on lines count to prevent cell overflow
                lines_count = text_html.count('<br/>') + 1
                curr_span = span_map.get((ri, ci), 1)
                # Available vertical height roughly = curr_span * 37 pt
                avail_cell_h = curr_span * 37.0

                if lines_count > 7 or (curr_span == 1 and lines_count > 4):
                    fs = 5.2
                    ld = 6.2
                elif lines_count > 4 or (curr_span == 1 and lines_count > 3):
                    fs = 6.0
                    ld = 7.2
                else:
                    fs = 6.8
                    ld = 8.2

                p_style = ParagraphStyle(
                    f'cs_{ri}_{ci}',
                    fontName='Helvetica',
                    fontSize=fs,
                    leading=ld,
                    alignment=1
                )
                row.append(Paragraph(text_html, p_style))
        tdata.append(row)

    # 6. TableStyle Commands
    cmds = [
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.black),
        ('BACKGROUND',    (0, 0), (-1,  0), colors.HexColor('#D9D9D9')),
        ('BACKGROUND',    (0, 1), ( 0, -1), colors.HexColor('#F2F2F2')),
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

    col_w = [48.0] + [(540.0 - 48.0) / 6.0] * 6
    row_h = [18.0] + [37.0] * len(_HOURS)

    tbl = Table(tdata, colWidths=col_w, rowHeights=row_h, repeatRows=1)
    tbl.setStyle(TableStyle(cmds))
    return tbl

print('build_clean_table defined successfully!')
