import app as app_module
from unittest.mock import MagicMock


class FakeResponse:
    def __init__(self, data=None):
        self.data = data if data is not None else []


class FakeArchiveQuery:
    def __init__(self, table_name):
        self.table_name = table_name
        self._filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def or_(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def delete(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def is_(self, col, val):
        self._filters[f"{col}__is"] = val
        return self

    def execute(self):
        if self.table_name in ('schedule_archive', 'schedule'):
            if self._filters.get('archive') is True or self._filters.get('batch_id') == 'batch-1' or self.table_name == 'schedule_archive':
                if self._filters.get('batch_id') == 'batch-1' or self._filters.get('archive') is True:
                    return FakeResponse([
                        {
                            'schedule_id': 1,
                            'archive_id': 1,
                            'batch_id': 'batch-1',
                            'course_id': 101,
                            'prof_id': 1,
                            'room_id': 1,
                            'day': 'Monday',
                            'class_start': '08:00:00',
                            'class_end': '09:00:00',
                            'session_type': 'Lecture',
                            'section': '1A',
                            'semester': '1st Semester',
                            'major': None,
                            'program': 'BSIT',
                            'archive': True,
                            'archived_at': '2026-09-01T12:00:00Z',
                            'archived_by': 'Scheduler',
                            'archive_reason': 'Replaced on confirmation',
                            'course': {'course_name': 'IT101 - Intro to Computing'},
                            'professor': {'first_name': 'Alan', 'last_name': 'Turing'},
                            'room': {'room_name': 'Room 101'},
                        }
                    ])
                return FakeResponse([
                    {
                        'schedule_id': 1,
                        'archive_id': 1,
                        'batch_id': 'batch-1',
                        'course_id': 101,
                        'section': '1A',
                        'semester': '1st Semester',
                        'major': None,
                        'program': 'BSIT',
                        'archive': True,
                        'archived_at': '2026-09-01T12:00:00Z',
                        'archived_by': 'Scheduler',
                        'archive_reason': 'Replaced on confirmation',
                    }
                ])
            return FakeResponse([])
        elif self.table_name == 'program_department':
            return FakeResponse({'department_name': 'CICT'})
        return FakeResponse([])


class FakeArchiveSupabase:
    def __init__(self, batches=None):
        self.batches = batches

    def table(self, table_name):
        return FakeArchiveQuery(table_name)

    def rpc(self, func_name, params=None):
        batches = self.batches
        class FakeRpc:
            def execute(self):
                if func_name == 'restore_archived_schedule_batch':
                    return FakeResponse({
                        'success': True,
                        'restored_batch_id': params.get('p_batch_id'),
                        'archived_current_count': 1,
                        'restored_count': 1,
                        'semester': '1st Semester',
                        'program': 'BSIT'
                    })
                elif func_name == 'confirm_schedule_transaction':
                    return FakeResponse({
                        'success': True,
                        'batch_id': 'batch-new',
                        'archived_count': 1,
                        'deleted_count': 1,
                        'inserted_count': 1,
                        'semester': '1st Semester',
                        'program': 'BSIT'
                    })
                elif func_name == 'get_schedule_archive_batches':
                    if batches is not None:
                        return FakeResponse(batches)
                    return FakeResponse([
                        {
                            'batch_id': 'batch-1',
                            'semester': '1st Semester',
                            'program': 'BSIT',
                            'archived_at': '2026-09-01T10:00:00Z',
                            'archived_by': 'Admin',
                            'archive_reason': 'Replaced on confirmation',
                            'entry_count': 1,
                            'section_count': 1,
                            'sections': ['1A']
                        }
                    ])
                elif func_name == 'archive_active_schedule':
                    return FakeResponse({
                        'success': True,
                        'batch_id': 'batch-newly-archived',
                        'archived_at': '2026-10-02T01:00:00Z',
                        'archived_count': 12
                    })
                return FakeResponse({'success': True})
        return FakeRpc()


def test_schedule_archive_list_page(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    assert b'Schedule Archive' in response.data
    assert b'batch-1' in response.data or b'batch-1'[:8] in response.data.decode('utf-8')


def test_view_schedule_archive_batch_detail(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive/batch-1')
    assert response.status_code == 200
    assert b'Archived Schedule Details' in response.data
    assert b'1A' in response.data
    assert b'IT101' in response.data


def test_restore_schedule_archive_rpc_success(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/restore_schedule_archive/batch-1', follow_redirects=False)
    assert response.status_code == 302
    assert '/schedules' in response.headers.get('Location', '')


def test_restore_schedule_archive_viewer_forbidden(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 2
        session['program'] = 'BSIT'
        session['username'] = 'viewer'
        session['role'] = 'Viewer'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.post('/restore_schedule_archive/batch-1', follow_redirects=False)
    assert response.status_code == 403


def test_delete_schedule_archive_permissions(monkeypatch):
    client = app_module.app.test_client()

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    # Scheduler CAN delete
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'scheduler'
        session['role'] = 'Scheduler'

    res_sched = client.post('/delete_schedule_archive/batch-1', follow_redirects=False)
    assert res_sched.status_code == 302

    # Admin CANNOT delete (Admin is read-only on archive, writes are 403)
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['role'] = 'Admin'

    res_admin = client.post('/delete_schedule_archive/batch-1', follow_redirects=False)
    assert res_admin.status_code == 403


def test_confirm_preview_second_semester_sets_active_semester_and_redirects(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'
        session['preview_id'] = 'prev_2nd_sem'
        session['schedule_preview'] = [
            {
                'course_id': 21,
                'course_name': 'IT202 - Object Oriented Programming',
                'section': '2A',
                'prof_id': 54,
                'room_id': 21,
                'day': 'Wednesday',
                'start': '10:00 AM',
                'end': '12:00 PM',
                'session_type': 'Lecture',
                'semester': '2nd Semester',
                'major': None,
                'program': 'BSIT',
            }
        ]

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/confirm_preview', follow_redirects=False)
    assert response.status_code == 302
    assert 'semester=2nd+Semester' in response.headers.get('Location', '') or 'semester=2nd%20Semester' in response.headers.get('Location', '')

    with client.session_transaction() as session:
        assert session.get('active_semester') == '2nd Semester'
        assert len(session.get('generated_sections', [])) == 1
        assert session['generated_sections'][0]['semester'] == '2nd Semester'


def test_multiple_archive_batches_distinct_and_sorted_pht(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    batches = [
        {
            'batch_id': 'batch-old-1234',
            'semester': '1st Semester',
            'program': 'BSIT',
            'archived_at': '2026-09-01T00:00:00Z',
            'archived_by': 'Admin',
            'archive_reason': 'First manual archive',
            'entry_count': 10,
            'section_count': 2,
            'sections': ['1A', '1B']
        },
        {
            'batch_id': 'batch-new-5678',
            'semester': '2nd Semester',
            'program': 'BSIT',
            'archived_at': '2026-10-01T04:00:00Z',
            'archived_by': 'Scheduler',
            'archive_reason': 'Second manual archive',
            'entry_count': 25,
            'section_count': 4,
            'sections': ['2A', '2B', '2C', '2D']
        }
    ]

    fake_sb = FakeArchiveSupabase(batches=batches)
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # 1. Distinct batches (never merged): both batch IDs appear
    assert 'batch-old-1234'[:8] in html
    assert 'batch-new-5678'[:8] in html

    # 2. Both counts are displayed accurately and separately
    assert '10' in html and '25' in html

    # 3. Newest batch appears first in the HTML table
    pos_new = html.find('batch-new-5678'[:8])
    pos_old = html.find('batch-old-1234'[:8])
    assert pos_new < pos_old, "Newer batch should appear before older batch"

    # 4. PHT formatting is present (UTC+8, formatted like "Oct 2, 2026, 12:33 AM")
    # 2026-10-01T04:00:00Z + 8h = 2026-10-01 12:00 PM PHT
    assert 'Oct 1, 2026, 12:00 PM' in html
    # 2026-09-01T00:00:00Z + 8h = 2026-09-01 08:00 AM PHT
    assert 'Sep 1, 2026, 8:00 AM' in html


def test_schedule_archive_date_filtering(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    batches = [
        {
            'batch_id': 'batch-sep-01',
            'semester': '1st Semester',
            'program': 'BSIT',
            'archived_at': '2026-09-01T00:00:00Z',
            'archived_by': 'Admin',
            'archive_reason': 'September archive',
            'entry_count': 5,
            'section_count': 1,
            'sections': ['1A']
        },
        {
            'batch_id': 'batch-oct-01',
            'semester': '2nd Semester',
            'program': 'BSIT',
            'archived_at': '2026-10-01T04:00:00Z',
            'archived_by': 'Admin',
            'archive_reason': 'October archive',
            'entry_count': 8,
            'section_count': 1,
            'sections': ['2A']
        }
    ]

    fake_sb = FakeArchiveSupabase(batches=batches)
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Filter for October only
    resp = client.get('/schedule_archive?date_from=2026-09-15&date_to=2026-10-05')
    assert resp.status_code == 200
    html = resp.data.decode('utf-8')
    assert 'batch-oct-01'[:8] in html
    assert 'batch-sep-01'[:8] not in html
    assert 'Clear' in html

    # Filter for future date with no matches -> Empty state with Clear Filters
    resp_empty = client.get('/schedule_archive?date_from=2026-11-01')
    assert resp_empty.status_code == 200
    html_empty = resp_empty.data.decode('utf-8')
    assert 'No Archived Schedules Match Your Filters' in html_empty
    assert 'Clear Filters' in html_empty


def test_archive_schedule_route_calls_rpc_and_flashes(monkeypatch):
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    fake_sb = FakeArchiveSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/archive_schedule', follow_redirects=True)
    assert response.status_code == 200
    assert b'Active schedule has been archived successfully' in response.data


def test_legacy_archived_rows_appear_as_single_legacy_archive_entry(monkeypatch):
    """Legacy archived rows (archive=True with NULL archive_batch_id/archived_at) must group into 'Legacy archive' with no fake date."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class FakeLegacyQuery(FakeArchiveQuery):
        def execute(self):
            # Return 5 legacy rows across 2 sections with no batch_id or timestamp
            return FakeResponse([
                {'schedule_id': 1, 'semester': '1st Semester', 'section': '1A', 'archive': True, 'program_id': 1, 'program': {'program_name': 'BSIT'}},
                {'schedule_id': 2, 'semester': '1st Semester', 'section': '1A', 'archive': True, 'program_id': 1, 'program': {'program_name': 'BSIT'}},
                {'schedule_id': 3, 'semester': '1st Semester', 'section': '1B', 'archive': True, 'program_id': 1, 'program': {'program_name': 'BSIT'}},
                {'schedule_id': 4, 'semester': '1st Semester', 'section': '1B', 'archive': True, 'program_id': 1, 'program': {'program_name': 'BSIT'}},
                {'schedule_id': 5, 'semester': '1st Semester', 'section': '1B', 'archive': True, 'program_id': 1, 'program': {'program_name': 'BSIT'}},
            ])

    class FakeLegacySupabase:
        def table(self, table_name):
            return FakeLegacyQuery(table_name)
        def rpc(self, *args, **kwargs):
            raise Exception("RPC not installed")

    monkeypatch.setattr(app_module, 'supabase', FakeLegacySupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Must show Legacy Archive label and badge
    assert 'Legacy Archive' in html
    assert 'Legacy' in html
    # 5 classes aggregated, 2 sections
    assert '5' in html
    assert '2 section(s)' in html
    # Must NOT have any fake date like 1970 or arbitrary date
    assert '1970' not in html


def test_empty_filter_values_are_ignored_as_no_filter(monkeypatch):
    """Empty filter query params like semester='' or program='' must be treated as no filter."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    batches = [
        {
            'batch_id': 'batch-all-filters',
            'semester': '1st Semester',
            'program': 'BSIT',
            'archived_at': '2026-09-01T00:00:00Z',
            'archived_by': 'Admin',
            'archive_reason': 'Test',
            'entry_count': 10,
            'section_count': 1,
            'sections': ['1A']
        }
    ]

    fake_sb = FakeArchiveSupabase(batches=batches)
    monkeypatch.setattr(app_module, 'supabase', fake_sb)
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    # Pass empty filters as well as 'All Semesters' / 'All Programs'
    response = client.get('/schedule_archive?semester=&program=&date_from=&date_to=')
    assert response.status_code == 200
    assert 'batch-all-filters'[:8] in response.data.decode('utf-8')

    response2 = client.get('/schedule_archive?semester=All+Semesters&program=All+Programs')
    assert response2.status_code == 200
    assert 'batch-all-filters'[:8] in response2.data.decode('utf-8')


def test_archive_fetch_error_displays_visible_alert_banner(monkeypatch):
    """If database query errors, display visible error alert and NOT false 'No Archived Schedules Found'."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    class CrashingSupabase:
        def table(self, table_name):
            raise RuntimeError("Database connection pool exhausted")
        def rpc(self, *args, **kwargs):
            raise RuntimeError("RPC error")

    monkeypatch.setattr(app_module, 'supabase', CrashingSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Must display the prominent error alert banner
    assert 'Failed to load archived schedules' in html
    # Must NOT show the misleading empty state message
    assert 'No Archived Schedules Found' not in html


def test_large_schedule_archive_pagination_not_capped_at_1000(monkeypatch):
    """Pagination in schedule_archive must correctly fetch all rows when batch has >1000 classes."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'admin'
        session['role'] = 'Admin'

    batch_uuid = '11111111-2222-3333-4444-555555555555'

    class PaginatedQuery(FakeArchiveQuery):
        def __init__(self, table_name):
            super().__init__(table_name)
            self._range_calls = []

        def range(self, start, end):
            self._range_calls.append((start, end))
            self._last_start = start
            self._last_end = end
            return self

        def execute(self):
            # Page 1 (0 to 999): 1000 rows
            # Page 2 (1000 to 1999): 1000 rows
            # Page 3 (2000 to 2999): 500 rows (total 2500 rows)
            start = getattr(self, '_last_start', 0)
            if start == 0:
                rows = [{'schedule_id': i, 'semester': '1st Semester', 'section': f'Sec-{i%10}', 'archive': True, 'archive_batch_id': batch_uuid, 'archived_at': '2026-10-01T08:00:00Z', 'program': {'program_name': 'BSIT'}} for i in range(1000)]
            elif start == 1000:
                rows = [{'schedule_id': i, 'semester': '1st Semester', 'section': f'Sec-{i%10}', 'archive': True, 'archive_batch_id': batch_uuid, 'archived_at': '2026-10-01T08:00:00Z', 'program': {'program_name': 'BSIT'}} for i in range(1000, 2000)]
            else:
                rows = [{'schedule_id': i, 'semester': '1st Semester', 'section': f'Sec-{i%10}', 'archive': True, 'archive_batch_id': batch_uuid, 'archived_at': '2026-10-01T08:00:00Z', 'program': {'program_name': 'BSIT'}} for i in range(2000, 2500)]
            return FakeResponse(rows)

    class PaginatedSupabase:
        def table(self, table_name):
            return PaginatedQuery(table_name)
        def rpc(self, *args, **kwargs):
            raise Exception("RPC not installed")

    monkeypatch.setattr(app_module, 'supabase', PaginatedSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Verified: all 2500 rows are captured, not truncated at 1000!
    assert '2500' in html
    assert batch_uuid[:8] in html


def test_two_archive_batches_same_day_distinct_entries(monkeypatch):
    """Two archive actions occurring on the same day minutes apart must NEVER be merged."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'tester'
        session['role'] = 'Scheduler'

    batch_id_1 = '11111111-aaaa-bbbb-cccc-111111111111'
    batch_id_2 = '22222222-aaaa-bbbb-cccc-222222222222'

    # Same calendar day (Oct 2, 2026), 5 minutes apart in Philippine Time (UTC+8)
    # Batch 1: 00:30 UTC -> 08:30 AM PHT
    # Batch 2: 00:35 UTC -> 08:35 AM PHT
    ts_1 = '2026-10-02T00:30:00+00:00'
    ts_2 = '2026-10-02T00:35:00+00:00'

    table_rows = [
        # Batch 1: 15 classes, Section 1A
        *[{'schedule_id': 100 + i, 'semester': '1st Semester', 'section': '1A', 'archive': True, 'archive_batch_id': batch_id_1, 'archived_at': ts_1, 'program': {'program_name': 'BSIT'}} for i in range(15)],
        # Batch 2: 25 classes, Section 2A
        *[{'schedule_id': 200 + i, 'semester': '1st Semester', 'section': '2A', 'archive': True, 'archive_batch_id': batch_id_2, 'archived_at': ts_2, 'program': {'program_name': 'BSIT'}} for i in range(25)],
    ]

    class TableDirectQuery(FakeArchiveQuery):
        def range(self, start, end):
            self._range = (start, end)
            return self
        def execute(self):
            return FakeResponse(table_rows)

    class TableDirectSupabase:
        def table(self, name):
            return TableDirectQuery(name)
        def rpc(self, *args, **kwargs):
            raise Exception("No RPC")

    monkeypatch.setattr(app_module, 'supabase', TableDirectSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # Both batch IDs are rendered separately
    assert batch_id_1[:8] in html
    assert batch_id_2[:8] in html

    # Both class counts are separate and accurate (15 and 25, NOT stacked into 40)
    assert '15' in html and '25' in html

    # Both timestamps are formatted as "Oct 2, 2026, 8:30 AM" and "Oct 2, 2026, 8:35 AM"
    assert 'Oct 2, 2026, 8:30 AM' in html
    assert 'Oct 2, 2026, 8:35 AM' in html

    # Sorted newest first: Batch 2 (8:35 AM) appears BEFORE Batch 1 (8:30 AM)
    pos_b2 = html.find(batch_id_2[:8])
    pos_b1 = html.find(batch_id_1[:8])
    assert pos_b2 != -1 and pos_b1 != -1
    assert pos_b2 < pos_b1, "Newer batch (8:35 AM) must appear before older batch (8:30 AM)"


def test_large_archive_5000_plus_classes_pagination(monkeypatch):
    """Schedule archive must accurately retrieve 5000+ classes without capping at 1000."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'admin'
        session['role'] = 'Admin'

    batch_uuid = '99999999-8888-7777-6666-555555555555'
    total_classes = 5676
    total_sections = 55

    class BigArchiveQuery(FakeArchiveQuery):
        def range(self, start, end):
            self._start = start
            self._end = end
            return self

        def execute(self):
            start = getattr(self, '_start', 0)
            page_size = 1000
            end = min(start + page_size, total_classes)
            if start >= total_classes:
                return FakeResponse([])
            rows = [
                {
                    'schedule_id': i,
                    'semester': '1st Semester',
                    'section': f'SEC_{i % total_sections}',
                    'archive': True,
                    'archive_batch_id': batch_uuid,
                    'archived_at': '2026-10-01T12:00:00Z',
                    'program': {'program_name': 'BSIT'}
                }
                for i in range(start, end)
            ]
            return FakeResponse(rows)

    class BigArchiveSupabase:
        def table(self, table_name):
            return BigArchiveQuery(table_name)
        def rpc(self, *args, **kwargs):
            raise Exception("No RPC")

    monkeypatch.setattr(app_module, 'supabase', BigArchiveSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')

    response = client.get('/schedule_archive')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    # All 5676 classes and 55 sections displayed accurately
    assert '5676' in html
    assert f'{total_sections} section(s)' in html


def test_archive_schedule_payload_only_valid_columns(monkeypatch):
    """archive_schedule fallback must only update archive, archive_batch_id, and archived_at."""
    client = app_module.app.test_client()

    with client.session_transaction() as session:
        session['user_id'] = 1
        session['program'] = 'BSIT'
        session['username'] = 'scheduler'
        session['role'] = 'Scheduler'

    updated_payload = {}

    class InterceptUpdateQuery(FakeArchiveQuery):
        def update(self, payload):
            updated_payload.update(payload)
            return self

    class InterceptSupabase:
        def table(self, name):
            return InterceptUpdateQuery(name)
        def rpc(self, *args, **kwargs):
            raise Exception("No RPC")

    monkeypatch.setattr(app_module, 'supabase', InterceptSupabase())
    monkeypatch.setattr(app_module, '_get_department', lambda: 'CICT')
    monkeypatch.setattr(app_module, '_get_user_program_id', lambda: 1)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    response = client.post('/archive_schedule', follow_redirects=False)
    assert response.status_code == 302

    # Verify only valid columns are in the update payload
    assert updated_payload.get('archive') is True
    assert 'archive_batch_id' in updated_payload
    assert 'archived_at' in updated_payload
    # Must NOT contain non-existent columns
    assert 'archived_by' not in updated_payload
    assert 'archive_reason' not in updated_payload




