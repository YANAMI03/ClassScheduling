"""Focused tests for the dedicated ILP placement rules.

Covers the ILP placement checklist:
  - Two courses in the same section get ILP at 7-8 AM and at 11-12 (or end of day inside window).
  - Two different sections can both have ILP on Monday 7-8 AM (no global ILP limit).
  - No professor or section is ever double-booked.
  - ILP sessions stay strictly inside each day's working_hours window (start_time to end_time, never overlapping lunch).
  - No "up to 2 hours past window end" allowance and no hard 8:00 PM cap: working_hours is the only time limit.
  - When no valid slot exists inside the working_hours window, ILP is left unscheduled and reported with course, section, and professor.
  - Lecture/lab placement is identical whether or not ILP exists.
  - Lecture and lab room assignment is unchanged.
"""
from datetime import timedelta

import app as app_module


# ---------------------------------------------------------------------------
# Minimal Supabase stub (supports the filters generation relies on)
# ---------------------------------------------------------------------------
class _NotProxy:
    def __init__(self, q):
        self._q = q

    def is_(self, col, val):
        self._q._filters.append(('not_is', col, val))
        return self._q


class _Query:
    def __init__(self, rows):
        self._rows = rows
        self._filters = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters.append(('eq', col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(('in', col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(('is', col, val))
        return self

    @property
    def not_(self):
        return _NotProxy(self)

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
            if self._matches(row):
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

    def _matches(self, row):
        for op, col, val in self._filters:
            rv = row.get(col)
            if op == 'eq':
                if str(rv) != str(val):
                    return False
            elif op == 'in':
                if rv not in val:
                    return False
            elif op == 'is':
                if val == 'null' and rv is not None:
                    return False
            elif op == 'not_is':
                if val == 'null' and rv is None:
                    return False
        return True

    def execute(self):
        rows = [r for r in self._rows if self._matches(r)]
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
            'archived_schedule': [],
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
        s['username'] = 'chair'
        s['role'] = 'chair'
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
    windows = _window_map(wh)
    lunches = {d['day']: _td(d.get('lunch_time') or '12:00:00') for d in wh}
    for e in _ilps(entries):
        day = e['day']
        assert day in windows, f'ILP scheduled on non-working day {day}'
        win_s, win_e = windows[day]
        st, en = _td(e['start']), _td(e['end'])
        assert st >= win_s, f'ILP {e["start"]} starts before {day} window start'
        assert en <= win_e, f'ILP {e["end"]} ends after {day} window end'
        assert (en - st) == timedelta(hours=1)
        lunch_s = lunches[day]
        if lunch_s is not None:
            lunch_e = lunch_s + timedelta(hours=1)
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


def test_ilp_stays_inside_working_hours_priority_zones(monkeypatch):
    # Window 08:00-16:00 across Mon & Tue.
    # Every ILP must stay inside [08:00, 16:00], preferring after a 3h class (11-12) then end of day (15-16).
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
    for e in _ilps(entries):
        st, en = _td(e['start']), _td(e['end'])
        win_start, win_end = windows[e['day']]
        assert win_start <= st and en <= win_end, f'ILP at {e["day"]} {e["start"]}-{e["end"]} escaped window'
        if st == win_end - timedelta(hours=1):
            continue  # zone (c): last hour inside window
        after_3h = any(
            other.get('section') == e.get('section') and other.get('day') == e.get('day')
            and _td(other.get('start')) is not None and _td(other.get('end')) is not None
            and (_td(other.get('end')) - _td(other.get('start'))) == timedelta(hours=3)
            and _td(other.get('end')) == st
            for other in entries
        )
        assert after_3h, f'ILP at {e["day"]} {e["start"]} is not in priority zone b or c'
    _assert_ilp_bounds(entries, wh)


def test_ilp_never_placed_past_7pm_window_end_and_reports_unscheduled(monkeypatch):
    # Window 08:00-19:00 (ends at 7:00 PM). Section 1A is booked 08-12 and 13-16,
    # and the 3h lecture takes 16-19. No free hour remains inside [08:00, 19:00].
    # Old rule would have placed ILP at 7:00-8:00 PM; new rule MUST leave it unscheduled
    # and report course code, section, and professor in the generation notice.
    wh = [_day('Monday', '08:00:00', '19:00:00', wid=1)]
    rooms = [_room()]
    schedules = [
        {'schedule_id': 900, 'section': '1A', 'room_id': 1, 'day': 'Monday',
         'class_start': '08:00:00', 'class_end': '12:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
        {'schedule_id': 901, 'section': '1A', 'room_id': 1, 'day': 'Monday',
         'class_start': '13:00:00', 'class_end': '16:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
    ]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-A')], [_prof(10, 'Alan', 'Turing')],
                                             [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh, schedules=schedules)
    assert _ilps(entries) == [], 'ILP must never be placed at 7-8 PM when window ends at 7:00 PM'
    assert len(errors) == 1
    assert errors[0]['course'] == 'IT-A'
    assert errors[0]['section'] == '1A'
    assert errors[0]['professor'] == 'Alan Turing'
    assert 'genuinely has no free hour' in errors[0]['reason']
    ilp_rows = [u for u in unscheduled if u.get('session_type') == 'ILP']
    assert len(ilp_rows) == 1
    assert ilp_rows[0]['course'] == 'IT-A'
    assert ilp_rows[0]['section'] == '1A'
    assert ilp_rows[0]['professor'] == 'Alan Turing'


def test_ilp_failure_reported_with_accurate_diagnostics(monkeypatch):
    # Window 08:00-20:00. Section 1A is booked 08-17, and the 3h lecture is 17-20.
    # Section 1A genuinely has no free hour left inside the window.
    wh = [_day('Monday', '08:00:00', '20:00:00', wid=1)]
    rooms = [_room()]
    schedules = [
        {'schedule_id': 900, 'section': '1A', 'room_id': 1, 'day': 'Monday',
         'class_start': '08:00:00', 'class_end': '17:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
    ]
    entries, errors, unscheduled = _generate(monkeypatch, [_course(100, 'IT-A')], [_prof(10, 'Alan', 'Turing')],
                                             [_load(101, 10, 100, 1, 'Alan Turing')], rooms, wh, schedules=schedules)
    assert _ilps(entries) == []
    assert len(errors) == 1
    assert 'genuinely has no free hour' in errors[0]['reason']
    ilp_rows = [u for u in unscheduled if u.get('session_type') == 'ILP']
    assert len(ilp_rows) == 1
    assert ilp_rows[0]['course'] == 'IT-A'
    assert 'genuinely has no free hour' in ilp_rows[0]['reason']


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


def test_ilp_prefers_last_hour_inside_window_step_c(monkeypatch):
    # Window 08:00-17:00. 2h lecture (no 3h class -> step b skipped), 8 AM start (-> step a skipped).
    # Step c places ILP in the last hour inside the window (16:00-17:00), never past 17:00.
    wh = [_day('Monday', '08:00:00', '17:00:00', wid=1)]
    rooms = [_room()]
    entries, errors, unscheduled = _generate(
        monkeypatch, [_course(100, 'IT-A', lec=2, lab=0, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
        rooms, wh
    )
    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 1
    assert (ilp[0]['day'], ilp[0]['start'], ilp[0]['end']) == ('Monday', '04:00 PM', '05:00 PM')
    _assert_ilp_bounds(entries, wh)


def test_ilp_allows_past_8pm_when_working_hours_ends_at_9pm(monkeypatch):
    # Hard 8:00 PM cap is removed: when working_hours ends at 21:00 (9:00 PM),
    # ILP can be placed at 20:00-21:00 (the last hour inside the window).
    wh = [_day('Monday', '08:00:00', '21:00:00', wid=1)]
    rooms = [_room()]
    entries, errors, unscheduled = _generate(
        monkeypatch, [_course(100, 'IT-A', lec=2, lab=0, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
        rooms, wh
    )
    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 1
    assert (ilp[0]['day'], ilp[0]['start'], ilp[0]['end']) == ('Monday', '08:00 PM', '09:00 PM')
    _assert_ilp_bounds(entries, wh)


def test_ilp_ladder_step_d_earlier_window_gap(monkeypatch):
    # Window 08:00-17:00. Last hour (16:00-17:00) is blocked for section 1A (step c unavailable).
    # Course is 2h lecture (no 3h class -> step b unavailable); 8 AM start (-> step a unavailable).
    # ILP must fall back to step d: the latest earlier free 1-hour gap inside the window (15:00-16:00).
    wh = [_day('Monday', '08:00:00', '17:00:00', wid=1)]
    rooms = [_room()]
    schedules = [
        {'schedule_id': 901, 'section': '1A', 'room_id': None, 'day': 'Monday',
         'class_start': '16:00:00', 'class_end': '17:00:00', 'professor_load_id': None,
         'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
    ]
    entries, errors, unscheduled = _generate(
        monkeypatch, [_course(100, 'IT-A', lec=2, lab=0, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
        rooms, wh, schedules=schedules
    )
    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 1
    assert (ilp[0]['day'], ilp[0]['start'], ilp[0]['end']) == ('Monday', '03:00 PM', '04:00 PM')
    _assert_ilp_bounds(entries, wh)


def test_ilp_ladder_a_to_b_tried_before_end_of_day_and_gap(monkeypatch):
    # When step a (7:00-8:00 AM) is available, ILP must take step a, NOT step c/d.
    wh = [_day('Monday', '07:00:00', '17:00:00', wid=1)]
    rooms = [_room()]
    entries, errors, unscheduled = _generate(
        monkeypatch, [_course(100, 'IT-A', lec=2, lab=0, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
        rooms, wh
    )
    ilp = _ilps(entries)
    assert errors == []
    assert len(ilp) == 1
    assert (ilp[0]['day'], ilp[0]['start']) == ('Monday', '07:00 AM')


def test_ilp_failure_diagnostics_when_professor_fully_booked(monkeypatch):
    # Window 08:00-20:00. Professor Alan Turing is booked during all hours 08:00-20:00.
    wh = [_day('Monday', '08:00:00', '20:00:00', wid=1)]
    rooms = [_room(), _room(2, 'Room 102', 'Lecture Room')]
    schedules = [
        # Alan Turing (prof_id=10) booked 08:00-17:00 with another section
        {'schedule_id': 900, 'section': '9Z', 'room_id': 1, 'day': 'Monday',
         'class_start': '08:00:00', 'class_end': '17:00:00', 'prof_id': 10,
         'professor_load_id': 999, 'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
    ]
    # IT-A lecture (3h) takes 17:00-20:00 for Turing.
    entries, errors, unscheduled = _generate(
        monkeypatch, [_course(100, 'IT-A', lec=3, lab=0, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
        rooms, wh, schedules=schedules
    )
    assert _ilps(entries) == []
    assert len(errors) == 1
    assert 'Professor Alan Turing genuinely has no free hour' in errors[0]['reason']
    assert errors[0]['prof_free'] == 0


def test_ilp_failure_diagnostics_mutual_conflict(monkeypatch):
    # Window 08:00-20:00.
    # Alan Turing is busy 08:00-10:00 and 11:00-20:00 (free ONLY 10:00-11:00, 1h free).
    # Section 1A is busy 10:00-20:00 (free ONLY 08:00-10:00, 2h free).
    # Both have free hours, but no common hour exists!
    wh = [_day('Monday', '08:00:00', '20:00:00', lunch='12:00:00', wid=1)]
    rooms = [_room(), _room(2, 'Room 102')]
    schedules = [
        # Alan Turing (prof 10) busy 08:00-10:00 with section 9Z
        {'schedule_id': 901, 'section': '9Z', 'room_id': 1, 'day': 'Monday',
         'class_start': '08:00:00', 'class_end': '10:00:00', 'prof_id': 10,
         'professor_load_id': 999, 'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
        # Alan Turing (prof 10) busy 11:00-20:00 with section 9Z
        {'schedule_id': 902, 'section': '9Z', 'room_id': 1, 'day': 'Monday',
         'class_start': '11:00:00', 'class_end': '20:00:00', 'prof_id': 10,
         'professor_load_id': 999, 'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
        # Section 1A busy 10:00-20:00 with prof 20
        {'schedule_id': 903, 'section': '1A', 'room_id': 2, 'day': 'Monday',
         'class_start': '10:00:00', 'class_end': '20:00:00', 'prof_id': 20,
         'professor_load_id': 998, 'program_id': 2, 'semester': '1st Semester', 'archive_batch_id': None},
    ]
    # Course is ILP-only (0 lec, 0 lab, 1 ilp)
    entries, errors, unscheduled = _generate(
        monkeypatch, [_course(100, 'ILP-ONLY', lec=0, lab=0, ilp=1)],
        [_prof(10, 'Alan', 'Turing')], [_load(101, 10, 100, 1, 'Alan Turing')],
        rooms, wh, schedules=schedules
    )
    assert _ilps(entries) == []
    assert len(errors) == 1
    assert 'Mutual timeslot conflict' in errors[0]['reason']
    assert errors[0]['prof_free'] > 0
    assert errors[0]['sec_free'] > 0


def test_working_hours_edit_validation_enforces_exact_window(monkeypatch):
    # Monday: 08:00-19:00 (no 7-8 AM, no 7-8 PM).
    # Wednesday: 08:00-21:00 (8-9 PM IS allowed when working_hours allows it).
    wh = [
        _day('Monday', '08:00:00', '19:00:00', lunch='12:00:00', wid=1),
        _day('Wednesday', '08:00:00', '21:00:00', lunch='12:00:00', wid=2),
    ]
    db = MockSupabase(working_hours=wh)
    monkeypatch.setattr(app_module, 'supabase', db)

    assert app_module._validate_working_hours_for_edit('Monday', '07:00:00', '08:00:00') is not None
    assert app_module._validate_working_hours_for_edit('Monday', '19:00:00', '20:00:00') is not None
    assert app_module._validate_working_hours_for_edit('Monday', '18:00:00', '19:00:00') is None
    assert app_module._validate_working_hours_for_edit('Wednesday', '20:00:00', '21:00:00') is None
    assert app_module._validate_working_hours_for_edit('Wednesday', '21:00:00', '22:00:00') is not None

