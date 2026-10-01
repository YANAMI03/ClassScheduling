import sys, os
sys.path.insert(0, os.path.abspath('.'))
import pytest
import re
import tests.test_change_requirements as tcr

def test_room_schedule_badges_render_correctly(monkeypatch):
    """Test that room_schedule tab renders 'Laboratory Room' in purple and 'Lecture Room' in blue, with fallback."""
    client = tcr.app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['username'] = 'sched'
        session['role'] = 'Scheduler'
        session['program'] = 'BSIT'
        session['program_id'] = 1

    rooms = [
        {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture Room', 'program_id': 1},
        {'room_id': 2, 'room_name': 'Lab 1', 'room_type': 'Laboratory Room', 'program_id': 1},
        {'room_id': 3, 'room_name': 'Lab 2', 'room_type': 'Laboratory', 'program_id': 1},
        {'room_id': 4, 'room_name': 'Open Space', 'room_type': 'Outdoor Hall', 'program_id': 1},
    ]
    schedules = [
        {'schedule_id': 1, 'room_id': 1, 'section': '1A', 'semester': '1st Semester', 'day': 'Monday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'archive': False, 'program_id': 1},
        {'schedule_id': 2, 'room_id': 2, 'section': '1B', 'semester': '1st Semester', 'day': 'Tuesday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'archive': False, 'program_id': 1},
        {'schedule_id': 3, 'room_id': 3, 'section': '1C', 'semester': '1st Semester', 'day': 'Wednesday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'archive': False, 'program_id': 1},
        {'schedule_id': 4, 'room_id': 4, 'section': '1D', 'semester': '1st Semester', 'day': 'Thursday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'archive': False, 'program_id': 1},
    ]

    db = tcr.MockSupabase(rooms=rooms, schedules=schedules)
    monkeypatch.setattr(tcr.app_module, 'supabase', db)
    monkeypatch.setattr(tcr.app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(tcr.app_module, '_get_active_semester', lambda pid: {'term': '1st Semester', 'school_year': '2026-2027'})
    monkeypatch.setattr(tcr.app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(tcr.app_module, 'log_activity', lambda *args, **kwargs: None)

    resp = client.get('/room_schedule')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')

    # 1. Room 101 -> Lecture Room (blue pill)
    assert 'Room 101' in html
    assert 'Lecture Room' in html
    assert 'bg-primary-subtle' in html
    assert 'text-primary' in html

    # 2. Lab 1 ('Laboratory Room') -> Laboratory Room (purple pill)
    assert 'Lab 1' in html
    assert 'Laboratory Room' in html
    assert 'badge-purple-subtle' in html or 'bg-purple-subtle' in html
    assert '#f3e8ff' in html

    # 3. Lab 2 ('Laboratory') -> normalized to Laboratory Room
    assert 'Lab 2' in html

    # 4. Open Space ('Outdoor Hall') -> fallback to raw text in neutral color
    assert 'Open Space' in html
    assert 'Outdoor Hall' in html
    assert 'bg-secondary-subtle' in html
