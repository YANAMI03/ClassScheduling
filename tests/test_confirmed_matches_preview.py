"""
Automated regression tests verifying that the confirmed (saved) schedule
matches the schedule preview exactly, upholding the single source of truth invariant.
"""

import copy
import threading
from unittest.mock import MagicMock
import pytest

import app as app_module
from app import (
    _derive_section_track,
    _to_norm_tuple,
    verify_staged_vs_saved_schedule,
    _group_preview_sections,
    _active_confirmations,
    _active_confirmations_lock,
)


# ---------------------------------------------------------------------------
# 1. Pure Function Invariant Verification Tests
# ---------------------------------------------------------------------------

def test_verify_staged_vs_saved_schedule_identical():
    """Identical staged and saved rows must pass verification."""
    staged = [
        {
            'section': '3A-Database Systems',
            'day': 'Monday',
            'start': '08:00 AM',
            'end': '10:00 AM',
            'session_type': 'Lecture',
            'room_id': 10,
            'professor_load_id': 101,
            'course_id': 55,
            'course_code': 'IT-IM02',
            'major': 'Database Systems',
            'semester': '2nd Semester',
        },
        {
            'section': '3A-Database Systems',
            'day': 'Wednesday',
            'start': '10:00 AM',
            'end': '12:00 PM',
            'session_type': 'Lecture',
            'room_id': 10,
            'professor_load_id': 102,
            'course_id': 50,
            'course_code': 'IT-IAS02',
            'major': 'General',
            'semester': '2nd Semester',
        },
    ]

    saved = [
        {
            'section': '3A-Database Systems',
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'session_type': 'Lecture',
            'room_id': 10,
            'professor_load_id': 101,
            'course_id': 55,
            'course_code': 'IT-IM02',
            'major': 'Database Systems',
            'semester': '2nd Semester',
        },
        {
            'section': '3A-Database Systems',
            'day': 'Wednesday',
            'class_start': '10:00:00',
            'class_end': '12:00:00',
            'session_type': 'Lecture',
            'room_id': 10,
            'professor_load_id': 102,
            'course_id': 50,
            'course_code': 'IT-IAS02',
            'major': 'General',
            'semester': '2nd Semester',
        },
    ]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is True
    assert msg == ""


def test_verify_staged_vs_saved_detects_row_count_mismatch():
    """Fails if saved has fewer or more rows."""
    staged = [
        {'section': '1A', 'day': 'Monday', 'start': '08:00:00', 'end': '10:00:00', 'room_id': 1, 'professor_load_id': 10, 'session_type': 'Lecture'},
        {'section': '1A', 'day': 'Tuesday', 'start': '08:00:00', 'end': '10:00:00', 'room_id': 1, 'professor_load_id': 11, 'session_type': 'Lecture'},
    ]
    saved = [copy.deepcopy(staged[0])]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is False
    assert "Row count mismatch" in msg


def test_verify_staged_vs_saved_detects_tuple_mismatch():
    """Fails if a room or time or day was mutated between staged and saved."""
    staged = [
        {'section': '1A', 'day': 'Monday', 'start': '08:00:00', 'end': '10:00:00', 'room_id': 1, 'professor_load_id': 10, 'session_type': 'Lecture'},
    ]
    saved = [
        {'section': '1A', 'day': 'Monday', 'class_start': '08:00:00', 'class_end': '10:00:00', 'room_id': 999, 'professor_load_id': 10, 'session_type': 'Lecture'},
    ]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is False
    assert "Tuple mismatch" in msg


def test_verify_staged_vs_saved_detects_section_renumbering():
    """Fails if sections were redistributed or split."""
    staged = [
        {'section': '3A-Database Systems', 'day': 'Monday', 'start': '08:00:00', 'end': '10:00:00', 'room_id': 1, 'professor_load_id': 10, 'course_id': 50, 'session_type': 'Lecture'},
    ]
    saved = [
        {'section': '3B-Database Systems', 'day': 'Monday', 'class_start': '08:00:00', 'class_end': '10:00:00', 'room_id': 1, 'professor_load_id': 10, 'course_id': 50, 'session_type': 'Lecture'},
    ]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is False


# ---------------------------------------------------------------------------
# 2. Specialized Term with 2 Tracks & IT-IAS Taught by 2 Professors
# ---------------------------------------------------------------------------

def test_specialized_term_dst_section_count_equals_two(monkeypatch):
    """
    Test specialized term with DST (2 sections) and WST (3 sections),
    where IT-IAS02 (General) is taught across sections by 2 different professors.
    Assert that saved section count equals preview section count (DST=2, WST=3).
    """
    monkeypatch.setattr(app_module, '_get_specializations', lambda include_archived=True: [
        {'specialization_id': 1, 'name': 'Database Systems', 'code': 'DST', 'archive': False},
        {'specialization_id': 2, 'name': 'Web Systems', 'code': 'WST', 'archive': False},
        {'specialization_id': 3, 'name': 'Networking', 'code': 'NST', 'archive': False},
    ])
    # 2 sections for Database Systems: 3A-Database Systems, 3B-Database Systems
    # 3 sections for Web Systems: 3A-Web Systems, 3B-Web Systems, 3C-Web Systems
    staged_preview = []

    # Database Systems sections (each has 1 track course + 1 General IT-IAS02 course)
    for sec_name in ['3A-Database Systems', '3B-Database Systems']:
        staged_preview.append({
            'section': sec_name,
            'day': 'Monday',
            'start': '08:00:00',
            'end': '11:00:00',
            'session_type': 'Lecture',
            'room_id': 1,
            'professor_load_id': 101,
            'course_id': 55,
            'course_code': 'IT-IM02',
            'professor_name': 'Prof Track',
            'major': 'Database Systems',
            'semester': '2nd Semester',
            'program': 'BSIT',
        })
        # IT-IAS02 taught by Prof General 1 for 3A, Prof General 2 for 3B
        prof_lid = 201 if '3A' in sec_name else 202
        staged_preview.append({
            'section': sec_name,
            'day': 'Wednesday',
            'start': '08:00:00',
            'end': '11:00:00',
            'session_type': 'Lecture',
            'room_id': 2,
            'professor_load_id': prof_lid,
            'course_id': 50,
            'course_code': 'IT-IAS02',
            'professor_name': 'Prof General 1' if prof_lid == 201 else 'Prof General 2',
            'major': 'General',
            'semester': '2nd Semester',
            'program': 'BSIT',
        })

    # Web Systems sections
    for sec_name in ['3A-Web Systems', '3B-Web Systems', '3C-Web Systems']:
        staged_preview.append({
            'section': sec_name,
            'day': 'Tuesday',
            'start': '08:00:00',
            'end': '11:00:00',
            'session_type': 'Lecture',
            'room_id': 3,
            'professor_load_id': 301,
            'course_id': 52,
            'course_code': 'IT-WS03',
            'professor_name': 'Prof Web',
            'major': 'Web Systems',
            'semester': '2nd Semester',
            'program': 'BSIT',
        })
        staged_preview.append({
            'section': sec_name,
            'day': 'Thursday',
            'start': '08:00:00',
            'end': '11:00:00',
            'session_type': 'Lecture',
            'room_id': 2,
            'professor_load_id': 201,
            'course_id': 50,
            'course_code': 'IT-IAS02',
            'professor_name': 'Prof General 1',
            'major': 'General',
            'semester': '2nd Semester',
            'program': 'BSIT',
        })

    # Total staged rows: 2*2 + 3*2 = 10 rows
    assert len(staged_preview) == 10

    # Simulate saving exactly these rows to the DB
    saved_rows = []
    for r in staged_preview:
        saved_rows.append({
            'schedule_id': len(saved_rows) + 1,
            'section': r['section'],
            'day': r['day'],
            'class_start': r['start'],
            'class_end': r['end'],
            'session_type': r['session_type'],
            'room_id': r['room_id'],
            'professor_load_id': r['professor_load_id'],
            'archive': False,
            'semester': r['semester'],
            'major': r['major'],
            'course_code': r['course_code'],
            'professor_load': {
                'id': r['professor_load_id'],
                'course_id': r['course_id'],
                'professor_name': r['professor_name'],
                'course': {
                    'course_id': r['course_id'],
                    'course_code': r['course_code'],
                    'specialization': r['major'],
                    'semester': r['semester'],
                }
            }
        })

    # Verify invariants between staged and saved
    is_valid, err_msg = verify_staged_vs_saved_schedule(staged_preview, saved_rows)
    assert is_valid is True, err_msg

    # Verify display grouping: DST must have exactly 2 section cards, NOT 4!
    # Regroup as schedules() does:
    sections_by_key = {}
    for r in saved_rows:
        sec = r['section']
        sem = r['semester']
        key = (sec, sem)
        sec_track = _derive_section_track(sec, [r]) or r['major']
        if key not in sections_by_key:
            sections_by_key[key] = {
                'section': {
                    'section': sec,
                    'section_name': sec,
                    'semester': sem,
                    'major': sec_track,
                    'year_level': '3',
                },
                'entries': []
            }
        sections_by_key[key]['entries'].append(r)

    sections_with_entries = list(sections_by_key.values())
    year_groups = _group_preview_sections(sections_with_entries)

    dst_group = next((g for g in year_groups if g['id'] == '3rd-year-dst'), None)
    assert dst_group is not None
    assert len(dst_group['sections']) == 2
    assert [s['section']['section'] for s in dst_group['sections']] == ['3A-Database Systems', '3B-Database Systems']

    wst_group = next((g for g in year_groups if g['id'] == '3rd-year-wst'), None)
    assert wst_group is not None
    assert len(wst_group['sections']) == 3

    # In section 3A-Database Systems, verify IT-IAS02 is present alongside IT-IM02
    sec_3a = next(s for s in dst_group['sections'] if s['section']['section'] == '3A-Database Systems')
    courses_in_3a = [e['course_code'] for e in sec_3a['entries']]
    assert 'IT-IM02' in courses_in_3a
    assert 'IT-IAS02' in courses_in_3a


# ---------------------------------------------------------------------------
# 3. Manual Edit in Preview (Changed Room, Day, Time) Preserved
# ---------------------------------------------------------------------------

def test_manual_edit_in_preview_persists_exactly():
    """Manual edits made via edit_preview_entry must survive and persist without revert."""
    client = app_module.app.test_client()

    # Pre-edited staged preview
    preview_rows = [
        {
            'id': 1,
            'course_id': 55,
            'course_code': 'IT-IM02',
            'section': '3A-Database Systems',
            'prof_id': 1,
            'room_id': 10,
            'room_name': 'Room 10',
            'day': 'Friday',               # Manually edited day
            'start': '01:00 PM',            # Manually edited start
            'end': '04:00 PM',              # Manually edited end
            'session_type': 'Laboratory',   # Manually edited session type
            'semester': '2nd Semester',
            'major': 'Database Systems',
            'program': 'BSIT',
            'professor_load_id': 101,
        }
    ]

    saved_row = {
        'section': '3A-Database Systems',
        'day': 'Friday',
        'class_start': '13:00:00',
        'class_end': '16:00:00',
        'session_type': 'Laboratory',
        'room_id': 10,
        'professor_load_id': 101,
    }

    # Verify manual edit matches saved
    is_valid, msg = verify_staged_vs_saved_schedule(preview_rows, [saved_row])
    assert is_valid is True, msg


# ---------------------------------------------------------------------------
# 4. Non-Specialized Term (Year 1 and Year 2) Remains Unchanged
# ---------------------------------------------------------------------------

def test_non_specialized_term_unaffected():
    """Non-specialized terms (Year 1 & Year 2) without major track remain standard."""
    staged = [
        {
            'section': '1A',
            'day': 'Monday',
            'start': '07:00 AM',
            'end': '10:00 AM',
            'session_type': 'Lecture',
            'room_id': 5,
            'professor_load_id': 1,
            'course_id': 10,
            'course_code': 'IT101',
            'major': None,
            'semester': '1st Semester',
        },
        {
            'section': '2B',
            'day': 'Tuesday',
            'start': '10:00 AM',
            'end': '01:00 PM',
            'session_type': 'Lecture',
            'room_id': 6,
            'professor_load_id': 2,
            'course_id': 20,
            'course_code': 'IT201',
            'major': None,
            'semester': '1st Semester',
        },
    ]

    saved = [
        {
            'section': '1A',
            'day': 'Monday',
            'class_start': '07:00:00',
            'class_end': '10:00:00',
            'session_type': 'Lecture',
            'room_id': 5,
            'professor_load_id': 1,
            'course_id': 10,
            'course_code': 'IT101',
            'major': None,
            'semester': '1st Semester',
        },
        {
            'section': '2B',
            'day': 'Tuesday',
            'class_start': '10:00:00',
            'class_end': '13:00:00',
            'session_type': 'Lecture',
            'room_id': 6,
            'professor_load_id': 2,
            'course_id': 20,
            'course_code': 'IT201',
            'major': None,
            'semester': '1st Semester',
        },
    ]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is True, msg

    sections_with_entries = [
        {'section': {'section': '1A', 'section_name': '1A', 'major': None, 'year_level': '1'}, 'entries': [saved[0]]},
        {'section': {'section': '2B', 'section_name': '2B', 'major': None, 'year_level': '2'}, 'entries': [saved[1]]},
    ]
    groups = _group_preview_sections(sections_with_entries)
    y1 = next(g for g in groups if g['id'] == '1st-year')
    y2 = next(g for g in groups if g['id'] == '2nd-year')
    assert len(y1['sections']) == 1
    assert len(y2['sections']) == 1


# ---------------------------------------------------------------------------
# 5. First, Second, and Third Year in Same Program across Semesters
# ---------------------------------------------------------------------------

def test_multi_year_multi_semester_integrity():
    """Ensure multi-year schedule across 1st and 2nd semesters maintains section integrity."""
    staged = [
        {'section': '1A', 'day': 'Monday', 'start': '08:00:00', 'end': '11:00:00', 'room_id': 1, 'professor_load_id': 11, 'course_id': 1, 'session_type': 'Lecture', 'semester': '1st Semester'},
        {'section': '2A', 'day': 'Tuesday', 'start': '08:00:00', 'end': '11:00:00', 'room_id': 2, 'professor_load_id': 21, 'course_id': 2, 'session_type': 'Lecture', 'semester': '1st Semester'},
        {'section': '3A-Database Systems', 'day': 'Wednesday', 'start': '08:00:00', 'end': '11:00:00', 'room_id': 3, 'professor_load_id': 31, 'course_id': 3, 'session_type': 'Lecture', 'semester': '2nd Semester', 'major': 'Database Systems'},
    ]

    saved = [
        {'section': '1A', 'day': 'Monday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'room_id': 1, 'professor_load_id': 11, 'course_id': 1, 'session_type': 'Lecture', 'semester': '1st Semester'},
        {'section': '2A', 'day': 'Tuesday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'room_id': 2, 'professor_load_id': 21, 'course_id': 2, 'session_type': 'Lecture', 'semester': '1st Semester'},
        {'section': '3A-Database Systems', 'day': 'Wednesday', 'class_start': '08:00:00', 'class_end': '11:00:00', 'room_id': 3, 'professor_load_id': 31, 'course_id': 3, 'session_type': 'Lecture', 'semester': '2nd Semester', 'major': 'Database Systems'},
    ]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is True, msg


# ---------------------------------------------------------------------------
# 6. TBA Room and NULL professor_load_id Survive Unchanged
# ---------------------------------------------------------------------------

def test_tba_room_and_null_professor_load_id_survive():
    """TBA rooms (None or empty) and unassigned professors (NULL professor_load_id) survive unchanged."""
    staged = [
        {
            'section': '3A-Database Systems',
            'day': 'Monday',
            'start': '08:00 AM',
            'end': '10:00 AM',
            'session_type': 'Lecture',
            'room_id': None,                 # TBA room
            'room_name': 'TBA',
            'professor_load_id': None,       # Unassigned professor
            'prof_id': None,
            'course_id': 55,
            'course_code': 'IT-IM02',
            'major': 'Database Systems',
            'semester': '2nd Semester',
        }
    ]

    saved = [
        {
            'section': '3A-Database Systems',
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'session_type': 'Lecture',
            'room_id': None,
            'professor_load_id': None,
            'course_id': 55,
            'course_code': 'IT-IM02',
            'major': 'Database Systems',
            'semester': '2nd Semester',
        }
    ]

    is_valid, msg = verify_staged_vs_saved_schedule(staged, saved)
    assert is_valid is True, msg
    assert _to_norm_tuple(staged[0]) == _to_norm_tuple(saved[0])


# ---------------------------------------------------------------------------
# 7. Double-Click Confirm Does Not Insert Duplicates
# ---------------------------------------------------------------------------

def test_double_click_confirm_blocked_by_lock():
    """Verify that rapid concurrent confirm_preview calls are safely blocked by user lock."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 999
        sess['username'] = 'lock_test_user'
        sess['role'] = 'scheduler'

    # Simulate an active confirmation lock for this user
    user_key = 999
    with _active_confirmations_lock:
        _active_confirmations.add(user_key)

    try:
        # Second rapid POST request while lock is held
        resp = client.post('/confirm_preview', follow_redirects=False)
        assert resp.status_code == 302
        assert '/schedules' in resp.headers.get('Location', '')
    finally:
        with _active_confirmations_lock:
            _active_confirmations.discard(user_key)
