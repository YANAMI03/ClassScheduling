import sys
sys.path.insert(0, '.')
from room_utilization_pdf import _DAYS, _HOURS, _secs

entries = [
    # Suppose entry 1 is 3:00pm - 5:00pm (15:00 - 17:00)
    {'day': 'Monday', 'start_time': '15:00', 'end_time': '17:00', 'course': 'IT-HCI01', 'sec': '2H', 'room': '308'},
    # And what if there is an overlapping entry or duplicate entries?
    {'day': 'Monday', 'start_time': '15:00', 'end_time': '18:00', 'course': 'IT-HCI01', 'sec': '2H', 'room': '308'},
    {'day': 'Monday', 'start_time': '17:00', 'end_time': '20:00', 'course': 'IT-HCI01', 'sec': '2H', 'room': 'Lab 5'},
]

_SPAN = object()
nr = len(_HOURS) + 1
nc = 1 + len(_DAYS)
grid = [[''] * nc for _ in range(nr)]
spans = []

for e in entries:
    st = _secs(e['start_time'])
    et = _secs(e['end_time'])
    rs = re_ = None
    for ri, h in enumerate(_HOURS, 1):
        if st < (h + 1) * 3600 and et > h * 3600:
            if rs is None:
                rs = ri
            re_ = ri
    rowspan = re_ - rs + 1
    anchor = None
    for r in range(rs, re_ + 1):
        if grid[r][1] is not _SPAN:
            anchor = r
            break
    print(f"Entry {e['start_time']}-{e['end_time']}: rs={rs}, re_={re_}, anchor={anchor}, rowspan={rowspan}")
    if anchor is not None:
        cell = grid[anchor][1]
        if cell == '' or cell is None:
            grid[anchor][1] = f"{e['course']} {e['sec']} {e['room']}"
            if rowspan > 1:
                spans.append((anchor, 1, rowspan))
                for r in range(anchor + 1, anchor + rowspan):
                    if r < nr:
                        grid[r][1] = _SPAN
        else:
            grid[anchor][1] = cell + '\n—\n' + f"{e['course']} {e['sec']} {e['room']}"

print("Spans created:", spans)
for ri in range(nr):
    print(f"Row {ri} ({_HOURS[ri-1] if ri > 0 else 'H'}): grid={grid[ri][1]}")
