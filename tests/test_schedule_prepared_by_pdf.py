import io
import os
import pytest
from reportlab.platypus import KeepTogether
import app as app_module
import pdf_export


# ---------------------------------------------------------------------------
# Test Case 1: Section PDF confirmed by BSIT scheduler and exported by admin
# ---------------------------------------------------------------------------
def test_section_pdf_confirmed_by_scheduler_exported_by_admin(monkeypatch):
    """
    Requirement: The name is the user who confirmed the schedule, not the person exporting it.
    When an admin exports a section PDF confirmed by a BSIT scheduler ('MICKO LA MADRID'),
    the PDF must display the scheduler's name ('MICKO LA MADRID') and title ('Program Scheduler - BSIT'),
    never the exporting admin's name.
    """
    client = app_module.app.test_client()

    # Log in as academic admin
    with client.session_transaction() as sess:
        sess['user_id'] = '033cabba-8da2-47ba-aa0b-30d4262debf7'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'
        sess['username'] = 'admin'
        sess['first_name'] = 'Admin'
        sess['last_name'] = 'User'

    # Mock database active schedule rows with snapshot preparer = MICKO LA MADRID
    sched_rows = [
        {
            'schedule_id': 101,
            'day': 'Monday',
            'class_start': '08:00:00',
            'class_end': '10:00:00',
            'session_type': 'Lecture',
            'semester': '1st Semester',
            'major': 'General',
            'section': 'BSIT-1A',
            'program_id': 1,
            'program': {'id': 1, 'program_name': 'BSIT'},
            'professor_load_id': 1,
            'prepared_by_user_id': '077438bb-a507-4c8b-a947-e1a08a7092e4',
            'preparer': {
                'first_name': 'Micko',
                'last_name': 'La Madrid',
                'role': 'Scheduler',
                'program_id': 1,
            },
            'professor_load': {
                'id': 1,
                'prof_id': 1,
                'course_id': 1,
                'course': {'course_id': 1, 'course_code': 'IT101 - Intro to Computing'},
                'professor': {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing'}
            },
            'room': {'room_name': 'Lab 1'}
        }
    ]

    class MockQuery:
        def __init__(self, table):
            self.table = table
        def select(self, *args, **kwargs):
            return self
        def eq(self, *args, **kwargs):
            return self
        def execute(self):
            if self.table == 'schedule':
                return type('Resp', (), {'data': sched_rows})()
            if self.table == 'working_hours':
                return type('Resp', (), {'data': []})()
            return type('Resp', (), {'data': []})()

    class MockSupabase:
        def table(self, tbl):
            return MockQuery(tbl)

    monkeypatch.setattr(app_module, 'supabase', MockSupabase())
    monkeypatch.setattr(app_module, '_get_active_semester', lambda pid: {'school_year': '2026-2027', 'term': '1st Semester'})

    resp = client.get('/export/section_schedule/BSIT-1A/pdf')
    assert resp.status_code == 200
    pdf_bytes = resp.data
    assert pdf_bytes.startswith(b'%PDF-')

    # The PDF must contain the confirmer's name and title, NOT the admin's name
    assert b'MICKO LA MADRID' in pdf_bytes
    assert b'Program Scheduler - BSIT' in pdf_bytes
    assert b'ADMIN USER' not in pdf_bytes


# ---------------------------------------------------------------------------
# Test Case 2: Room PDF containing BSIT and BSDS classes shows two blocks
# ---------------------------------------------------------------------------
def test_room_pdf_multiple_programs_shows_two_blocks():
    """
    Requirement: Room Schedule PDFs can include classes from several programs.
    Show one "Prepared by" block per program that appears in the exported data,
    arranged side by side if they fit.
    """
    room = {'room_id': 5, 'room_name': 'CL 1'}
    entries = [
        {
            'day': 'Monday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '10:00:00',
            'course_code': 'IT101 - Intro to Computing',
            'section': 'BSIT 1A',
            'program': 'BSIT',
            'preparer_name': 'MICKO LA MADRID',
            'preparer_title': 'Program Scheduler - BSIT',
        },
        {
            'day': 'Monday',
            'start_time_raw': '10:00:00',
            'end_time_raw': '12:00:00',
            'course_code': 'DS101 - Intro to Data Science',
            'section': 'BSDS 1A',
            'program': 'BSDS',
            'preparer_name': 'BSDS USER',
            'preparer_title': 'Program Scheduler - BSDS',
        }
    ]

    # Verify extractor extracts 2 distinct program blocks
    preparers = pdf_export._extract_preparers('room', room, entries, {})
    assert len(preparers) == 2
    assert preparers[0]['name'] == 'MICKO LA MADRID'
    assert preparers[0]['title'] == 'Program Scheduler - BSIT'
    assert preparers[1]['name'] == 'BSDS USER'
    assert preparers[1]['title'] == 'Program Scheduler - BSDS'

    # Generate timetable PDF
    buf = pdf_export.generate_timetable_pdf('room', room, entries)
    data = buf.getvalue()
    assert data.startswith(b'%PDF-')
    assert b'MICKO LA MADRID' in data
    assert b'Program Scheduler - BSIT' in data
    assert b'BSDS USER' in data
    assert b'Program Scheduler - BSDS' in data

    # Verify flowable dimensions accommodate both side-by-side blocks
    sig_flowable = pdf_export.SignatureAreaFlowable(preparers=preparers)
    assert sig_flowable.height == 84.0  # side-by-side fits in 1 row


# ---------------------------------------------------------------------------
# Test Case 3: Archived schedule keeps original preparer
# ---------------------------------------------------------------------------
def test_archived_schedule_keeps_original_preparer():
    """
    Requirement: Archived schedules exported from the archive use the preparer
    stored on those rows, not the current scheduler.
    """
    archived_entries = [
        {
            'day': 'Tuesday',
            'start_time_raw': '09:00:00',
            'end_time_raw': '11:00:00',
            'course_code': 'IT201 - OOP',
            'section': 'BSIT 2A',
            'program': 'BSIT',
            'archive': True,
            'preparer_name': 'OLD SCHEDULER NAME',
            'preparer_title': 'Program Scheduler - BSIT',
        }
    ]

    buf = pdf_export.generate_timetable_pdf('section', {'section_name': 'BSIT 2A'}, archived_entries)
    data = buf.getvalue()
    assert data.startswith(b'%PDF-')
    assert b'OLD SCHEDULER NAME' in data
    assert b'Program Scheduler - BSIT' in data


# ---------------------------------------------------------------------------
# Test Case 4: Old schedule with NULL fields shows the fallback
# ---------------------------------------------------------------------------
def test_preparer_display_uses_linked_user_fields():
    """
    A linked user supplies the current name and role used to render the signature.
    """
    name, title = app_module._preparer_display_details(
        {'first_name': 'Sole', 'last_name': 'Scheduler', 'role': 'Scheduler', 'program_id': 1},
        'BSIT'
    )
    assert name == 'SOLE SCHEDULER'
    assert title == 'Program Scheduler - BSIT'


def test_missing_preparer_user_is_not_guessed():
    """
    When the referenced user no longer exists, do not attribute the schedule to another account.
    """
    name, title = app_module._preparer_display_details(None, 'BSIT')
    assert name == 'Preparer account unavailable'
    assert title == 'Role/title unavailable'

    # Export PDF with NULL name
    entries = [
        {
            'day': 'Wednesday',
            'start_time_raw': '08:00:00',
            'end_time_raw': '10:00:00',
            'course_code': 'IT102 - Discrete Math',
            'section': 'BSIT 1B',
            'program': 'BSIT',
            'preparer_name': name,
            'preparer_title': title,
        }
    ]
    buf = pdf_export.generate_timetable_pdf('section', {'section_name': 'BSIT 1B'}, entries)
    data = buf.getvalue()
    assert data.startswith(b'%PDF-')
    assert b'Role/title unavailable' in data
    # No made-up name should appear
    assert b'ANDREW CAEZAR' not in data
    assert b'Alice One' not in data


# ---------------------------------------------------------------------------
# Test Case 5: Long name and title does not overflow
# ---------------------------------------------------------------------------
def test_long_name_and_title_does_not_overflow():
    """
    Requirement: If the name or title is very long, shrink or wrap it, and never let it overflow.
    """
    long_name = "DR. CHRISTOPHER ALEXANDER MONTGOMERY-WELLINGTON III, PH.D., POST-DOC"
    long_title = "Program Scheduler - Bachelor of Science in Information Technology and Artificial Intelligence"

    entries = [
        {
            'day': 'Thursday',
            'start_time_raw': '13:00:00',
            'end_time_raw': '16:00:00',
            'course_code': 'IT401 - Capstone',
            'section': 'BSIT 4A',
            'program': 'BSIT',
            'preparer_name': long_name,
            'preparer_title': long_title,
        }
    ]

    buf = pdf_export.generate_timetable_pdf('section', {'section_name': 'BSIT 4A'}, entries)
    data = buf.getvalue()
    assert data.startswith(b'%PDF-')
    # The name appears in PDF and generation completed without clipping exception
    assert b'MONTGOMERY' in data


# ---------------------------------------------------------------------------
# Test Case 6: SafeKeepTogether wraps signature area
# ---------------------------------------------------------------------------
def test_safekeeptogether_wraps_signature_area():
    """
    Requirement: Wrap the whole signature area in KeepTogether so it never splits across pages.
    """
    preparers = [{'name': 'MICKO LA MADRID', 'title': 'Program Scheduler - BSIT'}]
    sig_flowable = pdf_export.SignatureAreaFlowable(preparers=preparers)
    kt = pdf_export.SafeKeepTogether([sig_flowable])

    # Must be an instance of reportlab.platypus.KeepTogether
    assert isinstance(kt, KeepTogether)

    # Test wrap and drawOn on canvas
    import reportlab.pdfgen.canvas as canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdf_export.PAGE_WIDTH, pdf_export.PAGE_HEIGHT))
    w, h = kt.wrapOn(c, 540.0, 300.0)
    assert w == 540.0
    assert h == 84.0
    kt.drawOn(c, 36.0, 100.0)
    c.save()
    assert len(buf.getvalue()) > 500


# ---------------------------------------------------------------------------
# Test Case 7: Migration SQL file integrity and idempotency
# ---------------------------------------------------------------------------
def test_migration_file_integrity():
    """The historical migration added the fields; the new migration removes them safely."""
    migration_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'migrations',
        '20261007_schedule_prepared_by.sql'
    )
    assert os.path.exists(migration_path), "Migration file must exist"

    with open(migration_path, 'r', encoding='utf-8') as f:
        sql = f.read()

    assert 'BEGIN;' in sql
    assert 'COMMIT;' in sql
    assert 'ALTER TABLE public.schedule' in sql
    assert 'ADD COLUMN IF NOT EXISTS prepared_by_user_id' in sql
    assert 'ADD COLUMN IF NOT EXISTS prepared_by_name' in sql
    assert 'ADD COLUMN IF NOT EXISTS prepared_by_title' in sql
    assert 'REFERENCES public.users(id) ON DELETE SET NULL' in sql
    assert 'CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction' in sql
    assert 'p_prepared_by_user_id' in sql
    assert 'p_prepared_by_name' in sql
    assert 'p_prepared_by_title' in sql
    assert 'NOTIFY pgrst, \'reload schema\';' in sql

    removal_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'supabase',
        'migrations',
        '20261010052106_remove_prepared_by_snapshot_columns.sql'
    )
    assert os.path.exists(removal_path), "Removal migration must exist"
    with open(removal_path, 'r', encoding='utf-8') as f:
        removal_sql = f.read()

    assert 'DROP COLUMN IF EXISTS prepared_by_name' in removal_sql
    assert 'DROP COLUMN IF EXISTS prepared_by_title' in removal_sql
    assert 'LEFT JOIN public.users u ON u.id = sc.prepared_by_user_id' in removal_sql
    assert 'sc.prepared_by_name' not in removal_sql
    assert 'sc.prepared_by_title' not in removal_sql
