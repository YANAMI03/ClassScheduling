import io
import pytest
from datetime import timedelta
import app as app_module
import pdf_export


def test_parse_time_preserves_pm_and_am():
    """Verify that PM and AM times are parsed correctly with zero AM/PM confusion."""
    # 1:00 PM -> 13:00 (46800 sec)
    sec_1pm = pdf_export._parse_time_to_seconds('01:00 PM')
    assert sec_1pm == 13 * 3600

    # 1:00 AM -> 01:00 (3600 sec)
    sec_1am = pdf_export._parse_time_to_seconds('01:00 AM')
    assert sec_1am == 1 * 3600
    assert sec_1pm != sec_1am

    # 12:00 PM -> 12:00 (43200 sec)
    sec_12pm = pdf_export._parse_time_to_seconds('12:00 PM')
    assert sec_12pm == 12 * 3600

    # 12:00 AM -> 00:00 (0 sec)
    sec_12am = pdf_export._parse_time_to_seconds('12:00 AM')
    assert sec_12am == 0

    # 7:30 AM -> 7 * 3600 + 1800
    sec_730am = pdf_export._parse_time_to_seconds('07:30 AM')
    assert sec_730am == 7 * 3600 + 1800

    # Test reverse display formatting
    assert pdf_export._seconds_to_display_time(sec_1pm) == '01:00 PM'
    assert pdf_export._seconds_to_display_time(sec_1am) == '01:00 AM'
    assert pdf_export._seconds_to_display_time(sec_12pm) == '12:00 PM'


def test_generate_pdf_room_schedule():
    """Verify room timetable PDF generation with lecture and lab classes."""
    room = {'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture'}
    entries = [
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '10:00:00',
            'course_name': 'IT101 - Intro to Computing',
            'section': '1A',
            'professor': 'Dr. Alan Turing',
            'session_type': 'Lecture',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('room', room, entries, filter_metadata={'semester': '1st Semester'})
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_generate_pdf_section_schedule():
    """Verify section timetable PDF generation."""
    section = {'section_name': 'BSIT 2A', 'year_level': '2', 'semester': '2nd Semester', 'major': 'Network'}
    entries = [
        {
            'day': 'Tuesday',
            'start_time_raw': '09:00:00',
            'end_time_raw': '12:00:00',
            'course_name': 'NET201 - Advanced Networking',
            'professor': 'Grace Hopper',
            'room': 'Cisco Lab',
            'session_type': 'Laboratory',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('section', section, entries, filter_metadata={'semester': '2nd Semester', 'year': '2', 'major': 'Network'})
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_generate_pdf_professor_schedule():
    """Verify professor timetable PDF generation."""
    professor = {'prof_id': 1, 'first_name': 'Ada', 'last_name': 'Lovelace', 'department': 'Computer Science'}
    entries = [
        {
            'day': 'Wednesday',
            'start_time_raw': '13:00:00',
            'end_time_raw': '15:00:00',
            'course_name': 'CS102 - Data Structures',
            'section': '1B',
            'room': 'Room 305',
            'session_type': 'Lecture',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('professor', professor, entries, filter_metadata={'semester': '1st Semester'})
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000


def test_generate_pdf_multiple_classes_same_slot():
    """Requirement 13: If multiple classes occur on the same day/time, display them properly without losing information."""
    section = {'section_name': 'Irregular-Combo'}
    entries = [
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '09:00:00',
            'course_name': 'MATH101 - Calculus 1',
            'professor': 'Prof Gauss',
            'room': 'Room 101',
            'session_type': 'Lecture',
        },
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '09:00:00',
            'course_name': 'HIST101 - Philippine History',
            'professor': 'Prof Rizal',
            'room': 'Room 102',
            'session_type': 'Lecture',
        }
    ]
    buf = pdf_export.generate_timetable_pdf('section', section, entries)
    data = buf.read()
    assert data.startswith(b'%PDF-')
    assert len(data) > 1000
