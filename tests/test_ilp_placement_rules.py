"""Focused tests for the dedicated ILP placement rules.

Covers the ILP placement checklist:
  - Two courses in the same section get ILP at 7-8 AM and at 11-12 (or after the window end).
  - Two different sections can both have ILP on Monday 7-8 AM (no global ILP limit).
  - No professor or section is ever double-booked.
  - No ILP is placed in the middle of the working-hours window outside zones a/b.
  - ILP never goes before 7 AM, past 8 PM, or overlaps lunch.
  - Lecture/lab placement is identical whether or not ILP exists.
  - Lecture and lab room assignment is unchanged.
"""
from datetime import timedelta

import app as app_module


# ---------------------------------------------------------------------------
# Minimal Supabase stub (supports the equality filters generation relies on)
# ---------------------------------------------------------------------------
class _Query:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, vals):
        self._filters.append((col, ('__in__', list(vals))))
        return self

    def neq(self, *args, **kwargs):
        return self

    def or_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def ilike(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def update(self, payload):
        for row in self._rows:
            if all(str(row.get(c)) == str(v) for c, v in self._filters):
                row.update(payload)
        class _R:
            data = []
        return _R()

    def insert(self, payload):
        rows = payload if isinstance(payload, list) else [payload]
        self._rows.extend(rows)
        class _R:
            data = []
        return _R()

    def delete(self):
        return self

    def execute(self):
        rows = list(self._rows)
        for col, val in self._filters:
            if isinstance(val, tuple) and val and val[0] == '__in__':
                allowed = val[1]
                rows = [r for r in rows if r.get(col) in allowed]
            else:
                rows = [r for r in rows if str(r.get(col)) == str(val)]
        class _R:
            pass
        resp = _R()
        resp.data = rows
        return resp


class MockSupabase:
    def __init__(self, courses=None, loads=None, schedules=None, professors=None, rooms=None, working_hours=None):
        self.tables = {
            'course': courses or [],
            'professor_load': loads or [],
            'schedule': schedules or [],
            'schedule_with_semester': schedules or [],
            'professor': professors or [],
            'room': rooms or [],
            'working_hours': working_hours or [],
        }

    def table(self, name):
        return _Query(self.tables.get(name, []))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _course(cid, code, lec=3, lab=0, ilp=1):
    return {'course_id': cid, 'course_code': code, 'year_level': 1, 'semester': '1st Semester',
            'program_id': 1, 'lecture_hours': lec, 'lab_hours': lab, 'ilp_hours': ilp,
            'units': 3, 'archive': False}


def _room(rid=1, name='Room 101', rtype='Lecture Room'):
    return {'room_id': rid, 'room_name': name, 'room_type': rtype, 'program_id': 1, 'archive': False}


def _prof(pid, first, last):
    return {'prof_id': pid, 'first_name': first, 'last_name': last, 'program_id': 1, 'time_designation': 5}


def _load(lid, pid, cid, sections, name):
    return {'id': lid, 'prof_id': pid, 'course_id': cid, 'sections': sections, 'professor_name': name}


def _day(name, start, end, lunch='12:00:00', wid=1):
    return {'working_hours_id': wid, 'day': name, 'start_time': start, 'end_time': end,
            'lunch_time': lunch, 'archive': False}


def _generate(monkeypatch, courses, profs, loads, rooms, working_hours, schedules=None, students=60):
    db = MockSupabase(courses=courses, professors=profs, loads=loads, rooms=rooms,
                      working_hours=working_hours, schedules=schedules or [])
    monkeypatch.setattr(app_module, 'supabase', db)
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *a, **k: None)

    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s['user_id'] = 1
        s['username'] = 'sched'
        s['role'] = 'Scheduler'
        s['program'] = 'BSIT'
        s['program_id'] = 1

    client.post('/generate_schedule', data={'semester': '1st Semester', 'students[1]': str(students)},
                follow_redirects=True)
    with client.session_transaction() as s:
        pid = s.get('preview_id')
        errors = s.get('ilp_placement_errors') or []
        unscheduled = s.get('unscheduled_loads') or []
    entries = app_module._get_preview_for_user(preview_id=pid)
    return entries, errors, unscheduled


def _td(value):
    return app_module._parse_time(value)


def _ilps(entries):
    return [e for e in entries if e.get('session_type') == 'ILP']


def _assert_no_overlaps(entries):
    from collections import defaultdict
    for key in ('section', 'professor_name'):
        buckets = defaultdict(list)
        for e in entries:
            st, en = _td(e.get('start')), _td(e.get('end'))
            if st is None or en is None:
                continue
            buckets[(e.get(key), e.get('day'))].append((st, en))
        for group in buckets.values():
            group.sort()
            for i in range(1, len(group)):
                assert group[i - 1][1] <= group[i][0], f'{key} conflict detected'


def _window_map(wh):
    return {d['day']: (_td(d['start_time']), _td(d['end_time'])) for d in wh}


def _assert_ilp_bounds(entries, wh):
    lunch_s, lunch_e = _td('12:00:00'), _td('13:00:00')
    for e in _ilps(entries):
        st, en = _td(e['start']), _td(e['end'])
        assert st >= _td('07:00:00'), 'ILP must never start before 7:00 AM'
        assert en <= _td('20:00:00'), 'ILP must never end after 8:00 PM'
        assert (en - st) == timedelta(hours=1)
        assert not (st < lunch_e and lunch_s < en), 'ILP must not overlap lunch'


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_same_section_two_courses_ilp_no_conflict(monkeypatch):
    # Single day: section 1A has IT-A (3h + ILP) and IT-B (3h + ILP), two professors.
    # Expect IT-A ILP at 7-8 AM and IT-B ILP at 11-12 (right after IT-A's 3h lecture).
    wh = [_day('Monday', '07:00:00', '20:00:00', wid=1)]
    rooms = [_room()]
    courses = [_course(100, 'IT-A'), _course(200, 'IT-B')]
    profs = [_prof(10, 'Alan', 'Turing'), _prof(20, 'Grace', 'Hopper')]
    loads = [_load(101, 10, 100, 1, 'Alan Turing'), _load(202, 20, 200, 1, 'Grace Hopper')]
    entries, errors, unscheduled = _generate(monkeypatch, courses, profs, loads, rooms, wh, students=60)

    ilp = _ilps(entries)
    assert errors == []
    assert [e['section'] for e in ilp] == ['1A', '1A']
    assert {(e['day'], e['start']) for e in ilp} == {('Monday', '07:00 AM'), ('Monday', '11:00 AM')}
    assert all(e['room_id'] is None and e['room_name'] == 'No room' for e in ilp)
    _assert_no_overlaps(entries)
    _assert_ilp_bounds(entries, wh)


def test_two_sections_can_both_use_monday_7_8(monkeypatch):
    # Same ILP-only course, two sections with two different professors.
    wh = [_day('Monday', '07:00:00', '20:00:00', wid=1)]
    rooms = [_room()]
    courses = [_course(300, 'ILP-ONLY', lec=0, lab=0, ilp=1)]
    profs = [_prof(10, 'Alan', 'Turing'), _prof(20, 'Grace', 'Hopper')]
    loads = [_load(301, 10, 300, 1, 'Alan Turing'), _load(302, 20, 300, 1, 'Grace Hopper')]
    entries, errors, unscheduled = _generate(monkeypatch, courses, profs, loads, rooms, wh, students=60)

    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 2
    assert {e['section'] for e in ilp} == {'1A', '1B'}
    # Global ILP limit removed: both sections share Monday 7-8 AM.
    assert {(e['section'], e['day'], e['start']) for e in ilp} == {
        ('1A', 'Monday', '07:00 AM'),
        ('1B', 'Monday', '07:00 AM'),
    }
    _assert_no_overlaps(entries)


def test_no_midday_ilp_outside_allowed_zones(monkeypatch):
    # Window 08:00-16:00. Every ILP must be at 7-8, immediately after a 3-hour class,
    # or after the window end -- never in a midday gap like 1-2 PM or 2-3 PM.
    wh = [_day('Monday', '08:00:00', '16:00:00', wid=1),
          _day('Tuesday', '08:00:00', '16:00:00', wid=2)]
    rooms = [_room(), _room(2, 'Room 102', 'Lecture Room')]
    courses = [_course(100, 'IT-A'), _course(200, 'IT-B'), _course(400, 'IT-C')]
    profs = [_prof(10, 'Alan', 'Turing'), _prof(20, 'Grace', 'Hopper'), _prof(30, 'Ada', 'Lovelace')]
    loads = [_load(101, 10, 100, 1, 'Alan Turing'),
             _load(202, 20, 200, 1, 'Grace Hopper'),
             _load(403, 30, 400, 1, 'Ada Lovelace')]
    entries, errors, unscheduled = _generate(monkeypatch, courses, profs, loads, rooms, wh, students=90)

    windows = _window_map(wh)
    lunch_s = _td('12:00:00')
    for e in _ilps(entries):
        st = _td(e['start'])
        win_start, win_end = windows[e['day']]
        if st == _td('07:00:00'):
            continue  # zone (a)
        if st >= win_end:
            continue  # zone (c)
        # zone (b): exactly the hour after a 3-hour class of the section
        after_3h = False
        for other in entries:
            if other.get('section') != e.get('section') or other.get('day') != e.get('day'):
                continue
            ost, oen = _td(other.get('start')), _td(other.get('end'))
            if ost is None or oen is None:
                continue
            if (oen - ost) == timedelta(hours=3) and oen == st:
                after_3h = True
                break
        assert after_3h, f'ILP at {e["day"]} {e["start"]} is not in an allowed zone'
    _assert_ilp_bounds(entries, wh)


def test_ilp_after_window_end_zone_c(monkeypatch):
    # Window 07:00-17:00. Block 7-8 (zone a) with an existing section row and force the
    # 3h lecture to 14:00-17:00, so the only remaining zone is (c) after the window end.
    wh = [_day('Monday', '07:00:00', '17:00:00', wid=1)]
    rooms = [_room()]
    schedules = [
        {'schedule_id': 900, 'section': '9Z', 'room_id': 1, 'day': 'Monday',
         'class_start': '08:00:00', 'class_end': '14:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive': False},
        {'schedule_id': 901, 'section': '1A', 'room_id': None, 'day': 'Monday',
         'class_start': '07:00:00', 'class_end': '08:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive': False},
    ]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-A')], [_prof(10, 'Alan', 'Turing')],
                                             [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh, schedules=schedules)
    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 1
    assert (ilp[0]['day'], ilp[0]['start'], ilp[0]['end']) == ('Monday', '05:00 PM', '06:00 PM')


def test_ilp_cap_failure_is_reported_as_ilp(monkeypatch):
    # Window 08:00-20:00. Block 7-8 (zone a) and force the 3h lecture to 17:00-20:00 so
    # zone (b) is past the window and zone (c) is empty (cap at 8 PM).
    wh = [_day('Monday', '08:00:00', '20:00:00', wid=1)]
    rooms = [_room()]
    schedules = [
        {'schedule_id': 900, 'section': '9Z', 'room_id': 1, 'day': 'Monday',
         'class_start': '08:00:00', 'class_end': '17:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive': False},
        {'schedule_id': 901, 'section': '1A', 'room_id': None, 'day': 'Monday',
         'class_start': '07:00:00', 'class_end': '08:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive': False},
    ]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-A')], [_prof(10, 'Alan', 'Turing')],
                                             [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh, schedules=schedules)
    assert _ilps(entries) == []
    assert len(errors) == 1
    assert '8:00 PM cap' in errors[0]['reason']
    # The unscheduled report tags the failure as ILP (not lecture/lab).
    ilp_rows = [u for u in unscheduled if u.get('session_type') == 'ILP']
    assert len(ilp_rows) == 1
    assert ilp_rows[0]['course'] == 'IT-A'
    assert '8:00 PM cap' in ilp_rows[0]['reason']


def test_lecture_lab_identical_with_and_without_ilp(monkeypatch):
    # The lecture/lab schedule must be byte-for-byte identical whether ILP exists or not.
    wh = [_day('Monday', '07:00:00', '20:00:00', wid=1),
          _day('Tuesday', '08:00:00', '20:00:00', wid=2)]
    rooms = [_room(1, 'Room 101', 'Lecture Room'), _room(2, 'Lab 201', 'Laboratory Room')]

    with_ilp, _, _ = _generate(
        monkeypatch,
        [_course(100, 'IT-LELAB', lec=3, lab=2, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh)

    without_ilp, _, _ = _generate(
        monkeypatch,
        [_course(100, 'IT-LELAB', lec=3, lab=2, ilp=0)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh)

    def _signature(entries):
        return sorted(
            (e['section'], e['course_code'], e['session_type'], e['day'], e['start'], e['end'], e['room_id'])
            for e in entries if e['session_type'] in ('Lecture', 'Laboratory')
        )

    assert _signature(with_ilp) == _signature(without_ilp)
    # ILP is present only in the ILP run.
    assert len(_ilps(with_ilp)) == 1
    assert _ilps(without_ilp) == []


def test_no_ilp_before_8am_on_8am_days(monkeypatch):
    # Monday/Tuesday windows start at 08:00. Zone (a) is not eligible, so ILP must never
    # appear before 08:00, and the 8-9 AM first hour stays reserved for lecture/lab.
    wh = [_day('Monday', '08:00:00', '16:00:00', wid=1),
          _day('Tuesday', '08:00:00', '16:00:00', wid=2)]
    rooms = [_room(), _room(2, 'Room 102', 'Lecture Room')]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-A')],
                                             [_prof(10, 'Alan', 'Turing')],
                                             [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh)
    windows = _window_map(wh)
    ilp = _ilps(entries)
    assert ilp, 'the lecture leaves an 11-12 zone for ILP'
    for e in ilp:
        st = _td(e['start'])
        assert st >= windows[e['day']][0], "ILP must not start before the day's window start"
        assert st >= _td('08:00:00'), 'ILP must never be placed before 8:00 AM on an 8 AM day'
        assert st != _td('08:00:00'), 'the 8-9 AM first hour is reserved for lecture/lab'
    _assert_ilp_bounds(entries, wh)


def test_7am_day_still_allows_first_hour_ilp(monkeypatch):
    # Monday window starts at 07:00, so zone (a) applies and ILP takes 7-8 AM.
    wh = [_day('Monday', '07:00:00', '16:00:00', wid=1)]
    rooms = [_room()]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-A')],
                                             [_prof(10, 'Alan', 'Turing')],
                                             [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh)
    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 1
    assert (ilp[0]['day'], ilp[0]['start'], ilp[0]['end']) == ('Monday', '07:00 AM', '08:00 AM')


def test_lecture_and_lab_room_assignment_unchanged(monkeypatch):
    wh = [_day('Monday', '08:00:00', '20:00:00', wid=1),
          _day('Tuesday', '08:00:00', '20:00:00', wid=2)]
    rooms = [_room(1, 'Room 101', 'Lecture Room'), _room(2, 'Lab 201', 'Laboratory Room')]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-LELAB', lec=3, lab=2, ilp=0)],
                                             [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
                                             rooms, wh)
    lec = [e for e in entries if e['session_type'] == 'Lecture']
    lab = [e for e in entries if e['session_type'] == 'Laboratory']
    assert lec and lab
    assert all(e['room_id'] == 1 for e in lec)
    assert all(e['room_id'] == 2 for e in lab)
    assert all(_td(e['end']) <= _td('20:00:00') for e in entries)
