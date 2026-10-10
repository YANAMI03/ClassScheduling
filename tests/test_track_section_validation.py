"""Tests for track section count validation, General course balancing, and detailed error messages."""

import pytest
from unittest.mock import MagicMock
import app as app_module


def test_specialized_term_sum_matches_2_5_6_equals_13():
    """Verify that when Database=2, Web=5, Networking=6, General IT-IAS02=13 passes with no General course error."""
    courses = [
        # Database Systems (2 sections each)
        {'course_id': 55, 'course_code': 'IT-IM02', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 56, 'course_code': 'IT-IM03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 57, 'course_code': 'IT-IM04', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 73, 'course_code': 'IT-CAP01 (DST)', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        # Web Systems (5 sections each)
        {'course_id': 52, 'course_code': 'IT-WS03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 53, 'course_code': 'IT-WS04', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 54, 'course_code': 'IT-WS05', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 74, 'course_code': 'IT-CAP01 (WST)', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        # Networking (6 sections each)
        {'course_id': 58, 'course_code': 'IT-NET03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 59, 'course_code': 'IT-NET04', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 60, 'course_code': 'IT-NET05', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 51, 'course_code': 'IT-CAP01 (NST)', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        # General Course (13 sections)
        {'course_id': 50, 'course_code': 'IT-IAS02', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'General', 'program_id': 1},
    ]

    loads = [
        # Database (2 each)
        {'id': 1, 'prof_id': 1, 'course_id': 55, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 56, 'sections': 2},
        {'id': 3, 'prof_id': 3, 'course_id': 57, 'sections': 2},
        {'id': 4, 'prof_id': 4, 'course_id': 73, 'sections': 2},
        # Web (5 each)
        {'id': 5, 'prof_id': 5, 'course_id': 52, 'sections': 5},
        {'id': 6, 'prof_id': 6, 'course_id': 53, 'sections': 5},
        {'id': 7, 'prof_id': 7, 'course_id': 54, 'sections': 5},
        {'id': 8, 'prof_id': 8, 'course_id': 74, 'sections': 5},
        # Networking (6 each)
        {'id': 9, 'prof_id': 9, 'course_id': 58, 'sections': 6},
        {'id': 10, 'prof_id': 10, 'course_id': 59, 'sections': 6},
        {'id': 11, 'prof_id': 11, 'course_id': 60, 'sections': 6},
        {'id': 12, 'prof_id': 12, 'course_id': 51, 'sections': 6},
        # General (13 total)
        {'id': 13, 'prof_id': 13, 'course_id': 50, 'sections': 13},
    ]

    class MockTable:
        def __init__(self, data):
            self._data = data
        def select(self, *args, **kwargs):
            return self
        def eq(self, col, val):
            if col == 'semester':
                return MockTable([d for d in self._data if d.get('semester') == val])
            if col == 'archive':
                return MockTable([])
            return self
        def execute(self):
            class Resp:
                def __init__(self, d): self.data = d
            return Resp(list(self._data))

    class MockSupabase:
        def table(self, name):
            if name == 'course':
                return MockTable(courses)
            if name == 'professor_load':
                return MockTable(loads)
            return MockTable([])

    orig_supabase = app_module.supabase
    try:
        app_module.supabase = MockSupabase()
        res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
        assert res['valid'] is True
        assert len(res['errors']) == 0
        bd = res['breakdown'][0]
        assert bd['general_total_required'] == 13
        spec_map = {g['specialization']: g['section_count'] for g in bd['specialization_groups']}
        assert spec_map == {'Database Systems': 2, 'Web Systems': 5, 'Networking': 6}
    finally:
        app_module.supabase = orig_supabase


def test_purposeful_mismatch_shows_detailed_breakdown():
    """Test intentional mismatch where General course IT-IAS02 has 10 sections instead of 13.
    Verifies error triggers and displays detailed course code counts per track."""
    courses = [
        {'course_id': 55, 'course_code': 'IT-IM02', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 52, 'course_code': 'IT-WS03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 58, 'course_code': 'IT-NET03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 50, 'course_code': 'IT-IAS02', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'General', 'program_id': 1},
    ]

    # Database: 2, Web: 5, Net: 6. General: 10 (mismatch! should be 13)
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 55, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 52, 'sections': 5},
        {'id': 3, 'prof_id': 3, 'course_id': 58, 'sections': 6},
        {'id': 4, 'prof_id': 4, 'course_id': 50, 'sections': 10},
    ]

    class MockTable:
        def __init__(self, data):
            self._data = data
        def select(self, *args, **kwargs):
            return self
        def eq(self, col, val):
            if col == 'semester':
                return MockTable([d for d in self._data if d.get('semester') == val])
            return MockTable([])
        def execute(self):
            class Resp:
                def __init__(self, d): self.data = d
            return Resp(list(self._data))

    class MockSupabase:
        def table(self, name):
            if name == 'course': return MockTable(courses)
            if name == 'professor_load': return MockTable(loads)
            return MockTable([])

    orig_supabase = app_module.supabase
    try:
        app_module.supabase = MockSupabase()
        res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
        assert res['valid'] is False
        assert any('General course IT-IAS02 has 10 sections, but requires 13 sections' in e for e in res['errors'])
        # Check that detailed course breakdown is included
        err_msg = [e for e in res['errors'] if 'IT-IAS02' in e][0]
        assert 'Database Systems: 2 (IT-IM02: 2)' in err_msg
        assert 'Web Systems: 5 (IT-WS03: 5)' in err_msg
        assert 'Networking: 6 (IT-NET03: 6)' in err_msg
    finally:
        app_module.supabase = orig_supabase


def test_internal_track_mismatch_preserves_representative_track_count():
    """Test that when an internal course in Web Systems has 2 sections while others have 5,
    Web Systems is counted as 5 (not 0) for the General course required total,
    while still reporting the internal mismatch error."""
    courses = [
        {'course_id': 55, 'course_code': 'IT-IM02', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 52, 'course_code': 'IT-WS03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 53, 'course_code': 'IT-WS04', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 54, 'course_code': 'IT-WS05', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 58, 'course_code': 'IT-NET03', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 50, 'course_code': 'IT-IAS02', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'General', 'program_id': 1},
    ]

    # Web Systems: IT-WS03=2, IT-WS04=5, IT-WS05=5 -> representative = 5
    # Database: 2, Networking: 6. Total required = 2 + 5 + 6 = 13.
    # General course has 13 sections.
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 55, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 52, 'sections': 2},
        {'id': 3, 'prof_id': 3, 'course_id': 53, 'sections': 5},
        {'id': 4, 'prof_id': 4, 'course_id': 54, 'sections': 5},
        {'id': 5, 'prof_id': 5, 'course_id': 58, 'sections': 6},
        {'id': 6, 'prof_id': 6, 'course_id': 50, 'sections': 13},
    ]

    class MockTable:
        def __init__(self, data):
            self._data = data
        def select(self, *args, **kwargs):
            return self
        def eq(self, col, val):
            if col == 'semester':
                return MockTable([d for d in self._data if d.get('semester') == val])
            return MockTable([])
        def execute(self):
            class Resp:
                def __init__(self, d): self.data = d
            return Resp(list(self._data))

    class MockSupabase:
        def table(self, name):
            if name == 'course': return MockTable(courses)
            if name == 'professor_load': return MockTable(loads)
            return MockTable([])

    orig_supabase = app_module.supabase
    try:
        app_module.supabase = MockSupabase()
        res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
        # Internal mismatch error is reported
        assert any('3rd Year (Web Systems): mismatched section counts' in e for e in res['errors'])
        # But General course check does NOT fail with 'requires 8 sections (sum of Database Systems: 2 + Web Systems: 0 + ...)'!
        assert not any('General course IT-IAS02' in e for e in res['errors'])
        bd = res['breakdown'][0]
        assert bd['general_total_required'] == 13
    finally:
        app_module.supabase = orig_supabase
