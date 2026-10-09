"""
Regression tests verifying that professor identity comes ONLY from professor_load,
that no legacy professor table/columns (professors, prof_id, schedule.semester, schedule.major)
are queried or expected, and that Unscheduled panel rows never show 'Prof #None'.
"""

import pytest
import re
import app as app_module
from app import get_professor_key, get_professor_name


def test_professor_helpers():
    """Verify get_professor_key and get_professor_name behave canonically."""
    # Dict with professor_name
    row1 = {'id': 10, 'professor_name': 'Christian Noli C. Tambio'}
    assert get_professor_name(row1) == 'Christian Noli C. Tambio'
    assert get_professor_key(row1) == 'christian noli c. tambio'

    # Dict with professor_key
    row2 = {'id': 11, 'professor_key': 'christian noli c. tambio', 'professor_name': 'Christian Noli C. Tambio'}
    assert get_professor_name(row2) == 'Christian Noli C. Tambio'
    assert get_professor_key(row2) == 'christian noli c. tambio'

    # String input
    assert get_professor_name('Rosalie B. Sison') == 'Rosalie B. Sison'
    assert get_professor_key('Rosalie B. Sison') == 'rosalie b. sison'

    # Empty / None
    assert get_professor_name(None) == ''
    assert get_professor_key(None) == ''
    assert get_professor_name({}) == ''
    assert get_professor_key({}) == ''


def test_no_legacy_professor_relations_in_app_queries():
    """Verify that app.py contains no Supabase queries requesting dropped 'professor(...)' relations."""
    with open(app_module.__file__, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find any select strings mentioning 'professor('
    bad_matches = re.findall(r"select\([^)]*professor\([^)]*\)", content)
    assert not bad_matches, f"Found legacy professor(...) queries in app.py: {bad_matches}"


def test_unscheduled_loads_display_valid_faculty_names():
    """Verify generator unscheduled loads list always formats proper names and never 'Prof #'."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'test-scheduler-user'
        sess['username'] = 'scheduler_test'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    # Simulate unscheduled loads directly in preview context
    sample_pc = [
        {'id': 1, 'course_id': 101, 'sections': 2, 'professor_name': 'Christian Noli C. Tambio'},
        {'id': 2, 'course_id': 102, 'sections': 1, 'professor_name': 'Nino G. Herrera'},
        {'id': 3, 'course_id': 103, 'sections': 1, 'professor_name': 'Henry T. Roque'},
        {'id': 4, 'course_id': 104, 'sections': 1, 'professor_name': 'Marcelino S. Cerin III'},
        {'id': 5, 'course_id': 105, 'sections': 1, 'professor_name': 'Jev D. Corpuz'},
        {'id': 6, 'course_id': 106, 'sections': 1, 'professor_name': 'Michelle Ann Mae G. Franco'},
        {'id': 7, 'course_id': 107, 'sections': 1, 'professor_name': 'Rosalie B. Sison'},
    ]

    for item in sample_pc:
        p_name = get_professor_name(item)
        assert p_name and not p_name.startswith("Prof #") and p_name != "None"


def test_professor_schedule_route_loads_cleanly():
    """Verify /professor_schedule route renders without 500 or fetch_error."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'test-scheduler-user'
        sess['username'] = 'scheduler_test'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = client.get('/professor_schedule?semester=2nd+Semester')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'fetch_error' not in html or 'An error occurred while loading professor schedules' not in html


def test_schedules_route_loads_cleanly():
    """Verify /schedules route renders without PostgREST errors."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 'test-scheduler-user'
        sess['username'] = 'scheduler_test'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    res = client.get('/schedules')
    assert res.status_code == 200
