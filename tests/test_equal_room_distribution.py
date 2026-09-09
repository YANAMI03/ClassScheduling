import pytest
import app as app_module
from datetime import time


class FakeResponse:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def or_(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        if self._data is not None:
            return FakeResponse(self._data.get(self.table_name, []))
        return FakeResponse([])


class FakeSupabase:
    def __init__(self, table_data=None):
        self.table_data = table_data or {}

    def table(self, table_name):
        return FakeQuery(table_name, self.table_data)


# --------------------------------------------------------------------------------------------------
# Unit Tests for _select_least_used_room
# --------------------------------------------------------------------------------------------------

def test_1_equal_distribution_unit():
    """
    Test 1: Given 4 rooms and 20 compatible classes:
    Expected: 5 / 5 / 5 / 5 distribution (diff == 0).
    """
    rooms = [
        {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'},
        {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture'},
        {'room_id': 103, 'room_name': 'Room 103', 'room_type': 'Lecture'},
        {'room_id': 104, 'room_name': 'Room 104', 'room_type': 'Lecture'},
    ]
    room_usage = {r['room_id']: 0 for r in rooms}
    room_last_used = {r['room_id']: 0 for r in rooms}
    room_order = {r['room_id']: idx for idx, r in enumerate(rooms)}
    room_bookings = {}

    assignment_step = 0
    # Simulate scheduling 20 classes across different timeslots where all rooms are valid
    for i in range(20):
        # Unique timeslot for each class so there are no hard conflicts among them
        day = f"Day_{i // 4}"
        start_t = time(8 + (i % 4) * 2, 0)
        end_t = time(10 + (i % 4) * 2, 0)

        selected = app_module._select_least_used_room(
            rooms, day, start_t, end_t,
            room_bookings, room_usage, room_last_used, room_order
        )
        assert selected is not None
        rk = selected['room_id']
        assignment_step += 1
        room_usage[rk] += 1
        room_last_used[rk] = assignment_step
        room_bookings.setdefault(rk, []).append((day, start_t, end_t))

    # All rooms should have exactly 5 assignments
    assert room_usage == {101: 5, 102: 5, 103: 5, 104: 5}
    diff = max(room_usage.values()) - min(room_usage.values())
    assert diff == 0


def test_2_uneven_number_of_classes_unit():
    """
    Test 2: Given 4 rooms and 22 classes:
    Expected distribution: 6 / 6 / 5 / 5 (max_usage - min_usage <= 1).
    """
    rooms = [
        {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'},
        {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture'},
        {'room_id': 103, 'room_name': 'Room 103', 'room_type': 'Lecture'},
        {'room_id': 104, 'room_name': 'Room 104', 'room_type': 'Lecture'},
    ]
    room_usage = {r['room_id']: 0 for r in rooms}
    room_last_used = {r['room_id']: 0 for r in rooms}
    room_order = {r['room_id']: idx for idx, r in enumerate(rooms)}
    room_bookings = {}

    assignment_step = 0
    for i in range(22):
        day = f"Day_{i // 4}"
        start_t = time(7 + (i % 5), 0)
        end_t = time(8 + (i % 5), 0)

        selected = app_module._select_least_used_room(
            rooms, day, start_t, end_t,
            room_bookings, room_usage, room_last_used, room_order
        )
        assert selected is not None
        rk = selected['room_id']
        assignment_step += 1
        room_usage[rk] += 1
        room_last_used[rk] = assignment_step
        room_bookings.setdefault(rk, []).append((day, start_t, end_t))

    counts = sorted(room_usage.values(), reverse=True)
    assert counts == [6, 6, 5, 5]
    diff = max(room_usage.values()) - min(room_usage.values())
    assert diff <= 1


def test_3_room_conflict_priority_unit():
    """
    Test 3: If Room A is occupied during a time slot, the algorithm must not assign
    another class to Room A during that same time slot even if Room A currently has
    the lowest usage.
    """
    rooms = [
        {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'},
        {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture'},
    ]
    # Room 101 has lower usage (1) than Room 102 (5)
    room_usage = {101: 1, 102: 5}
    room_last_used = {101: 1, 102: 5}
    room_order = {101: 0, 102: 1}

    # Room 101 is already booked on Monday 8:00 AM - 10:00 AM
    room_bookings = {
        101: [('Monday', time(8, 0), time(10, 0))],
        102: [],
    }

    # Try to schedule a class on Monday 9:00 AM - 11:00 AM (overlaps with Room 101)
    selected = app_module._select_least_used_room(
        rooms, 'Monday', time(9, 0), time(11, 0),
        room_bookings, room_usage, room_last_used, room_order
    )

    # Must choose Room 102 because Room 101 has a conflict, despite Room 101 having lower usage
    assert selected is not None
    assert selected['room_id'] == 102


def test_4_different_room_eligibility_unit():
    """
    Test 4: If a particular course can only use Rooms A and B:
    Room A -> 10 classes, Room B -> 5 classes, Room C -> 1 class.
    The algorithm should only compare A and B for that course.
    It must NOT select Room C just because Room C has fewer assignments.
    """
    room_a = {'room_id': 'A', 'room_name': 'Room A', 'room_type': 'Lecture'}
    room_b = {'room_id': 'B', 'room_name': 'Room B', 'room_type': 'Lecture'}
    room_c = {'room_id': 'C', 'room_name': 'Room C', 'room_type': 'Laboratory'}

    room_usage = {'A': 10, 'B': 5, 'C': 1}
    room_last_used = {'A': 10, 'B': 5, 'C': 1}
    room_order = {'A': 0, 'B': 1, 'C': 2}
    room_bookings = {'A': [], 'B': [], 'C': []}

    # For a Lecture class, candidate rooms only include Lecture rooms (A and B)
    candidate_rooms = [room_a, room_b]

    selected = app_module._select_least_used_room(
        candidate_rooms, 'Tuesday', time(8, 0), time(10, 0),
        room_bookings, room_usage, room_last_used, room_order
    )

    assert selected is not None
    # Must select Room B (5 classes < 10 classes), never Room C (even though C has 1 class)
    assert selected['room_id'] == 'B'
    assert selected['room_id'] != 'C'


def test_5_no_available_room_unit():
    """
    Test 5: If no valid room exists, preserve existing behavior (returns None, handling unscheduled).
    Do not silently assign an invalid room.
    """
    rooms = [
        {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'},
        {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture'},
    ]
    room_usage = {101: 2, 102: 2}
    # Both rooms are occupied Monday 8:00 AM - 11:00 AM
    room_bookings = {
        101: [('Monday', time(8, 0), time(11, 0))],
        102: [('Monday', time(8, 0), time(11, 0))],
    }

    selected = app_module._select_least_used_room(
        rooms, 'Monday', time(9, 0), time(10, 0),
        room_bookings, room_usage
    )

    assert selected is None


# --------------------------------------------------------------------------------------------------
# End-to-End Schedule Generation Integration Tests
# --------------------------------------------------------------------------------------------------

def test_6_multiple_sections_global_distribution(monkeypatch):
    """
    Test 6: Generate multiple sections and verify that room utilization is balanced
    across the WHOLE generation batch, rather than being reset for every section.
    """
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    # Mock curriculum with 2 lecture courses per year
    courses = [
        {'course_id': 1, 'course_name': 'IT101', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
        {'course_id': 2, 'course_name': 'IT102', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
        {'course_id': 3, 'course_name': 'IT201', 'year_level': 2, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
        {'course_id': 4, 'course_name': 'IT202', 'year_level': 2, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
    ]

    # 4 distinct lecture rooms
    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
        {'room_id': 2, 'room_name': 'Room 102', 'room_type': 'Lecture', 'department': 'CICT'},
        {'room_id': 3, 'room_name': 'Room 103', 'room_type': 'Lecture', 'department': 'CICT'},
        {'room_id': 4, 'room_name': 'Room 104', 'room_type': 'Lecture', 'department': 'CICT'},
    ]

    profs = [
        {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40, 'department': 'CICT'},
        {'prof_id': 2, 'first_name': 'Grace', 'last_name': 'Hopper', 'max_hours': 40, 'department': 'CICT'},
        {'prof_id': 3, 'first_name': 'Ada', 'last_name': 'Lovelace', 'max_hours': 40, 'department': 'CICT'},
        {'prof_id': 4, 'first_name': 'Linus', 'last_name': 'Torvalds', 'max_hours': 40, 'department': 'CICT'},
    ]

    prof_course = [
        {'course_id': 1, 'prof_id': 1, 'professor': profs[0]},
        {'course_id': 2, 'prof_id': 2, 'professor': profs[1]},
        {'course_id': 3, 'prof_id': 3, 'professor': profs[2]},
        {'course_id': 4, 'prof_id': 4, 'professor': profs[3]},
    ]

    timeslots = [
        {'timeslot_id': 1, 'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00'},
    ]

    table_data = {
        'course': courses,
        'room': rooms,
        'professor': profs,
        'prof_course': prof_course,
        'timeslot': timeslots,
        'schedule': [],
    }

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(table_data))
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    # Generate schedules for Year 1 (2 sections: 1A, 1B) and Year 2 (2 sections: 2A, 2B)
    # Total sections = 4. Each section has 2 courses. Total classes scheduled = 8 classes.
    # With 4 rooms, expected distribution across all sections is exactly 2 / 2 / 2 / 2!
    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '2',
        'sections[2]': '2',
        'sections[3]': '0',
        'sections[4]': '0',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) >= 8

        # Count classes per room in the preview
        room_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for entry in preview:
            rid = entry.get('room_id')
            if rid in room_counts:
                room_counts[rid] += 1

        # Check balance across the entire batch
        max_c = max(room_counts.values())
        min_c = min(room_counts.values())
        assert max_c - min_c <= 1, f"Room distribution was not balanced across batch: {room_counts}"
        # Each room should have received classes, not just the first room
        assert all(count > 0 for count in room_counts.values()), f"Some rooms received 0 classes: {room_counts}"


def test_7_existing_schedule_no_conflicts(monkeypatch):
    """
    Test 7: If schedules already exist, verify that the new generation logic does not
    create room conflicts with existing schedules when the application is supposed
    to consider them.
    """
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    courses = [
        {'course_id': 1, 'course_name': 'IT101', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program': 'BSIT'},
    ]

    rooms = [
        {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture', 'department': 'CICT'},
        {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture', 'department': 'CICT'},
    ]

    profs = [
        {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'max_hours': 40, 'department': 'CICT'},
    ]

    prof_course = [
        {'course_id': 1, 'prof_id': 1, 'professor': profs[0]},
    ]

    timeslots = [
        {'timeslot_id': 1, 'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '07:00:00', 'end_time': '19:00:00', 'lunch_time': '12:00:00'},
    ]

    # Existing booking in Room 101 on Monday from 07:00 to 12:00 for another program (BSIS)
    existing_schedule = [
        {
            'section': 'IS-1A',
            'room_id': 101,
            'day': 'Monday',
            'class_start': '07:00:00',
            'class_end': '12:00:00',
            'prof_id': 99,
            'semester': '1st Semester',
            'program': 'BSIS',
            'major': None
        }
    ]

    table_data = {
        'course': courses,
        'room': rooms,
        'professor': profs,
        'prof_course': prof_course,
        'timeslot': timeslots,
        'schedule': existing_schedule,
    }

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(table_data))
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_ensure_course_semester_column', lambda: None)

    response = client.post('/generate_schedule', data={
        'semester': '1st Semester',
        'sections[1]': '1',
    })

    assert response.status_code == 200

    with client.session_transaction() as session:
        preview = app_module._get_preview_for_user(session.get('user_id'), session.get('preview_id'))
        assert len(preview) > 0

        # Check each generated entry: if scheduled on Monday between 07:00 and 12:00, it must NOT be in Room 101
        for entry in preview:
            if entry.get('day') == 'Monday' and entry.get('room_id') == 101:
                start_t = app_module._parse_time(entry.get('start'))
                end_t = app_module._parse_time(entry.get('end'))
                conflict = app_module._has_conflict(
                    'Monday', start_t, end_t,
                    [('Monday', time(7, 0), time(12, 0))]
                )
                assert not conflict, f"Room conflict introduced with existing schedule booking: {entry}"


def test_preview_context_contains_room_utilization(monkeypatch):
    """
    Verify that _build_preview_context includes room_utilization list with counts.
    """
    preview_entries = [
        {'id': 1, 'course_id': 1, 'section': '1A', 'room_id': 101, 'room_name': 'Room 101', 'day': 'Monday', 'start': '08:00 AM', 'end': '10:00 AM', 'session_type': 'Lecture', 'semester': '1st Semester'},
        {'id': 2, 'course_id': 2, 'section': '1A', 'room_id': 102, 'room_name': 'Room 102', 'day': 'Tuesday', 'start': '08:00 AM', 'end': '10:00 AM', 'session_type': 'Lecture', 'semester': '1st Semester'},
        {'id': 3, 'course_id': 1, 'section': '1B', 'room_id': 101, 'room_name': 'Room 101', 'day': 'Wednesday', 'start': '08:00 AM', 'end': '10:00 AM', 'session_type': 'Lecture', 'semester': '1st Semester'},
    ]

    table_data = {
        'course': [],
        'room': [
            {'room_id': 101, 'room_name': 'Room 101', 'room_type': 'Lecture'},
            {'room_id': 102, 'room_name': 'Room 102', 'room_type': 'Lecture'},
            {'room_id': 103, 'room_name': 'Room 103', 'room_type': 'Lecture'},
        ],
        'prof_course': []
    }

    monkeypatch.setattr(app_module, 'supabase', FakeSupabase(table_data))
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    with app_module.app.test_request_context('/preview_schedule'):
        from flask import session
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

        context = app_module._build_preview_context(preview_entries)
        assert 'room_utilization' in context
        ru_map = {item['room_name']: item['count'] for item in context['room_utilization']}
        assert ru_map.get('Room 101') == 2
        assert ru_map.get('Room 102') == 1
        assert ru_map.get('Room 103') == 0
