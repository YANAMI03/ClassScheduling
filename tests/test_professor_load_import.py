import io
import os
import openpyxl
import pytest
import professor_load_importer
import app as app_module
from bs4 import BeautifulSoup


class MockTable:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self.data = [dict(d) for d in (data or [])]
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._filters = {}
        self._in_filters = {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, rows):
        self._is_insert = True
        self._last_inserted = []
        if isinstance(rows, list):
            for r in rows:
                new_row = dict(r)
                if 'id' not in new_row:
                    new_row['id'] = len(self.data) + len(self.inserted) + 1
                if self.table_name == 'professor' and 'prof_id' not in new_row:
                    new_row['prof_id'] = new_row['id']
                self.inserted.append(new_row)
                self.data.append(new_row)
                self._last_inserted.append(new_row)
        else:
            new_row = dict(rows)
            if 'id' not in new_row:
                new_row['id'] = len(self.data) + len(self.inserted) + 1
            if self.table_name == 'professor' and 'prof_id' not in new_row:
                new_row['prof_id'] = new_row['id']
            self.inserted.append(new_row)
            self.data.append(new_row)
            self._last_inserted.append(new_row)
        return self

    def update(self, payload):
        self.updated.append((dict(self._filters), dict(payload)))
        for item in self.data:
            match = True
            for col, val in self._filters.items():
                if str(item.get(col)) != str(val):
                    match = False
                    break
            if match:
                item.update(payload)
        return self

    def delete(self):
        self.deleted.append(dict(self._filters))
        new_data = []
        for item in self.data:
            match = True
            for col, val in self._filters.items():
                if str(item.get(col)) != str(val):
                    match = False
                    break
            for col, val_list in self._in_filters.items():
                if item.get(col) not in val_list and str(item.get(col)) not in [str(x) for x in val_list]:
                    match = False
                    break
            if not match:
                new_data.append(item)
        self.data = new_data
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, val):
        self._in_filters[col] = val
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if getattr(self, '_is_insert', False):
            self._is_insert = False
            res = list(self._last_inserted)
            class Response:
                pass
            r = Response()
            r.data = res
            return r

        res = []
        for item in self.data:
            match = True
            for col, val in self._filters.items():
                if str(item.get(col)) != str(val):
                    match = False
                    break
            for col, val_list in self._in_filters.items():
                if item.get(col) not in val_list and str(item.get(col)) not in [str(x) for x in val_list]:
                    match = False
                    break
            if match:
                res.append(dict(item))

        self._filters = {}
        self._in_filters = {}

        class Response:
            pass

        r = Response()
        r.data = res
        return r


class MockSupabase:
    def __init__(self, professors=None, courses=None, professor_loads=None, programs=None):
        self.tables = {
            'professor': MockTable('professor', professors or []),
            'course': MockTable('course', courses or []),
            'professor_load': MockTable('professor_load', professor_loads or []),
            'academic_ranking': MockTable('academic_ranking', [
                {'id': 1, 'name': 'Instructor', 'min_units': 12, 'max_units': 30, 'min_hours': 15, 'max_hours': 50}
            ]),
            'activity_log': MockTable('activity_log', []),
            'program': MockTable('program', programs or []),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name)
        return self.tables[name]


def _setup_import_test_environment(client, monkeypatch):
    """Build mock professors, courses, and loads matching import.xlsx."""
    with open('import.xlsx', 'rb') as f:
        file_bytes = f.read()

    data_rows, dividers, _ = professor_load_importer.parse_import_file(file_bytes, 'import.xlsx')

    # Build unique professors
    unique_prof_names = sorted(set(r['raw_professor'] for r in data_rows))
    professors = []
    for i, name in enumerate(unique_prof_names, 1):
        parts = name.split()
        first = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
        last = parts[-1] if len(parts) > 1 else ""
        professors.append({
            'prof_id': i,
            'first_name': first,
            'last_name': last,
            'department': 'CICT',
            'specialization': 'IT',
            'program_id': 1,
            'academic_ranking_id': 1,
            'academic_ranking': {
                'name': 'Instructor',
                'min_units': 6.0,
                'max_units': 30.0,
                'min_hours': 10.0,
                'max_hours': 50.0,
            }
        })

    # Build unique courses (resolving (WST)1 to (WST))
    raw_courses = sorted(set(r['raw_course'] for r in data_rows))
    cleaned_courses = set()
    for c in raw_courses:
        cleaned = c.replace('(WST)1\xa0', '(WST)').replace('(WST)1', '(WST)').replace('IT-HC101', 'IT-HCI01').strip()
        cleaned_courses.add(cleaned)

    courses = []
    for j, c_code in enumerate(sorted(cleaned_courses), 1):
        courses.append({
            'course_id': j,
            'course_name': c_code,
            'program_id': 1,
            'year_level': 1,
            'lecture_hours': 3,
            'lab_hours': 0,
            'ilp_hours': 0,
            'units': 3,
            'semester': '1st Semester'
        })

    mock_db = MockSupabase(professors=professors, courses=courses, professor_loads=[])
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)

    return mock_db, file_bytes, professors, courses


@pytest.fixture
def test_client():
    app_module.app.config['TESTING'] = True
    app_module.app.config['SECRET_KEY'] = 'test-secret'
    with app_module.app.test_client() as client:
        yield client


# =============================================================================
# 1. TEMPLATE DOWNLOAD
# =============================================================================
def test_template_download(test_client, monkeypatch):
    """Confirm /professor_load/import/template returns a valid .xlsx template."""
    with test_client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'scheduler'

    res = test_client.get('/professor_load/import/template')
    assert res.status_code == 200
    assert 'spreadsheetml' in res.content_type

    # Verify workbook structure
    wb = openpyxl.load_workbook(io.BytesIO(res.data))
    sheet = wb.active
    header_vals = [sheet.cell(1, c).value for c in range(1, 4)]
    assert header_vals == ['NAME', 'Course Code', 'Number of sections']

    # Confirm year divider rows exist
    divider_labels = [sheet.cell(r, 1).value for r in range(2, sheet.max_row + 1) if sheet.cell(r, 2).value is None and sheet.cell(r, 1).value]
    assert any('1st Year' in str(x) for x in divider_labels)


# =============================================================================
# 2. PREVIEW WITH REAL import.xlsx
# =============================================================================
def test_import_preview_with_real_import_xlsx(test_client, monkeypatch):
    """
    Step 3 Verification:
    - 63 data rows read
    - 3 year dividers detected and skipped
    - 39 professors affected
    - 21 course codes
    - 5 rows with '(WST)1' auto-cleaned with visible note
    - Multiple-course professors (Michelle Ann Mae G. Franco, Mariah Nikka A. Bautista) processed correctly
    """
    mock_db, file_bytes, profs, courses = _setup_import_test_environment(test_client, monkeypatch)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    data = {
        'file': (io.BytesIO(file_bytes), 'import.xlsx')
    }
    res = test_client.post('/professor_load/import/preview', data=data, content_type='multipart/form-data')
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data['success'] is True

    summary = json_data['summary']
    assert summary['total_rows'] == 63
    assert summary['year_dividers_count'] == 3
    assert summary['professors_affected'] == 39
    assert summary['courses_affected'] == 21
    assert summary['valid_count'] == 63
    assert summary['error_count'] == 0

    # Verify auto-cleaned (WST)1 rows
    wst1_cleaned_rows = [r for r in json_data['rows'] if '(WST)1' in r.get('raw_course_code', '') and any('auto-cleaned' in w.lower() for w in r.get('warnings', []))]
    assert len(wst1_cleaned_rows) == 5
    for r in wst1_cleaned_rows:
        assert r['course_code'] == 'IT-CAP01 (WST)'
        assert '(WST)1' in r['raw_course_code']

    # Verify multi-course professors
    franco_rows = [r for r in json_data['rows'] if 'franco' in r['professor_name'].lower()]
    assert len(franco_rows) == 2
    bautista_rows = [r for r in json_data['rows'] if 'bautista' in r['professor_name'].lower()]
    assert len(bautista_rows) == 2

    # Verify Course Section Totals tab
    c_totals = json_data['course_section_totals']
    assert len(c_totals) == 21
    cap01_total = next(c for c in c_totals if c['course_code'] == 'IT-CAP01 (WST)')
    assert cap01_total['professors_count'] == 5

    # Verify Professor Workloads tab
    p_loads = json_data['professor_workloads']
    assert len(p_loads) == 39
    for p in p_loads:
        assert p['total_hours'] >= 0
        assert p['total_units'] >= 0


# =============================================================================
# 3. CONFIRM IMPORT & IDEMPOTENCE (UPDATE VS DUPLICATE)
# =============================================================================
def test_confirm_import_and_idempotence(test_client, monkeypatch):
    """
    Step 3 Verification:
    - Confirm Import saves 63 rows.
    - Re-importing the same file UPDATES existing loads with ZERO duplicates.
    """
    mock_db, file_bytes, profs, courses = _setup_import_test_environment(test_client, monkeypatch)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'scheduler'

    # Step 1: Preview
    res_prev = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(file_bytes), 'import.xlsx')},
        content_type='multipart/form-data'
    )
    token = res_prev.get_json()['token']

    # Step 2: Confirm
    res_conf = test_client.post('/professor_load/import/confirm', data={'token': token})
    assert res_conf.status_code == 200
    conf_json = res_conf.get_json()
    assert conf_json['success'] is True
    assert conf_json['imported_count'] == 63
    assert conf_json['new_count'] == 63
    assert conf_json['updated_count'] == 0

    # Verify database has 63 rows
    current_loads = mock_db.table('professor_load').data
    assert len(current_loads) == 63

    # Step 3: Re-import same file (Idempotent update)
    res_prev2 = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(file_bytes), 'import.xlsx')},
        content_type='multipart/form-data'
    )
    token2 = res_prev2.get_json()['token']
    prev2_rows = res_prev2.get_json()['rows']
    # All rows should now be 'Updated'
    updated_rows = [r for r in prev2_rows if r['status'] == 'Updated']
    assert len(updated_rows) == 63

    # Confirm second import
    res_conf2 = test_client.post('/professor_load/import/confirm', data={'token': token2})
    assert res_conf2.status_code == 200
    conf2_json = res_conf2.get_json()
    assert conf2_json['imported_count'] == 63
    assert conf2_json['new_count'] == 0
    assert conf2_json['updated_count'] == 63

    # Verify database STILL has exactly 63 rows (zero duplicates created)
    current_loads_after = mock_db.table('professor_load').data
    assert len(current_loads_after) == 63


# =============================================================================
# 4. UNTOUCHED EXISTING ASSIGNMENTS REMAIN UNCHANGED
# =============================================================================
def test_untouched_assignments_preserved(test_client, monkeypatch):
    """Manual assignments not mentioned in the file are left untouched."""
    mock_db, file_bytes, profs, courses = _setup_import_test_environment(test_client, monkeypatch)

    # Insert an existing assignment for prof_id 1 with a course not in import.xlsx
    unrelated_course = {
        'course_id': 999,
        'course_name': 'CS-SPECIAL999',
        'lecture_hours': 3,
        'lab_hours': 0,
        'ilp_hours': 0,
        'units': 3
    }
    mock_db.table('course').insert(unrelated_course)
    mock_db.table('professor_load').insert({
        'id': 777,
        'prof_id': 1,
        'course_id': 999,
        'sections': 2,
        'course': unrelated_course
    })

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    res_prev = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(file_bytes), 'import.xlsx')},
        content_type='multipart/form-data'
    )
    token = res_prev.get_json()['token']
    res_conf = test_client.post('/professor_load/import/confirm', data={'token': token})
    assert res_conf.status_code == 200

    # Ensure assignment 777 is still in database!
    all_loads = mock_db.table('professor_load').data
    untouched = [l for l in all_loads if l.get('course_id') == 999]
    assert len(untouched) == 1
    assert untouched[0]['id'] == 777
    assert untouched[0]['sections'] == 2


# =============================================================================
# 5. ERROR HANDLING & BAD FILES
# =============================================================================
def test_bad_files_extension(test_client, monkeypatch):
    """Wrong extension (.txt or .pdf) rejected."""
    with test_client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'scheduler'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(b'some text'), 'import.txt')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 400
    assert 'Invalid file format' in res.get_json()['error']


def test_bad_files_empty(test_client, monkeypatch):
    """Empty file rejected."""
    with test_client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'scheduler'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(b''), 'empty.xlsx')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 400
    assert 'empty' in res.get_json()['error'].lower()


def test_bad_files_missing_headers(test_client, monkeypatch):
    """Missing required header columns rejected."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['ColA', 'ColB', 'ColC'])
    ws.append(['John Doe', 'CC-101', 3])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'admin-1'
        sess['role'] = 'scheduler'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (buf, 'bad_headers.xlsx')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 400
    assert 'Missing required header' in res.get_json()['error']


def test_bad_data_rows_flagged_in_preview(test_client, monkeypatch):
    """
    Verify row-level errors:
    - Unknown professor
    - Unknown course
    - Section = 0, blank, text, decimal
    - Duplicate prof+course row in file
    - Ranking max hours/units exceeded
    """
    mock_db = MockSupabase(
        professors=[
            {
                'prof_id': 1,
                'first_name': 'Alan',
                'last_name': 'Turing',
                'department': 'CICT',
                'academic_ranking': {
                    'name': 'Instructor',
                    'min_units': 6.0,
                    'max_units': 12.0,  # Max 12 units
                    'min_hours': 10.0,
                    'max_hours': 20.0,  # Max 20 hours
                }
            }
        ],
        courses=[
            {
                'course_id': 101,
                'course_name': 'CS-101',
                'lecture_hours': 3,
                'lab_hours': 0,
                'ilp_hours': 0,
                'units': 3,
                'year_level': 1
            },
            {
                'course_id': 102,
                'course_name': 'CS-102',
                'lecture_hours': 10,
                'lab_hours': 5,
                'ilp_hours': 0,
                'units': 10,
                'year_level': 1
            }
        ]
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NAME', 'Course Code', 'Number of sections'])
    ws.append(['Unknown Prof', 'CS-101', 2])       # Unknown professor
    ws.append(['Alan Turing', 'NONEXISTENT', 2])    # Unknown course
    ws.append(['Alan Turing', 'CS-101', 0])         # Section = 0
    ws.append(['Alan Turing', 'CS-101', ''])        # Blank section
    ws.append(['Alan Turing', 'CS-101', 'four'])    # Text section
    ws.append(['Alan Turing', 'CS-101', 2.5])       # Decimal section
    ws.append(['Alan Turing', 'CS-101', 1])         # Valid row
    ws.append(['Alan Turing', 'CS-101', 2])         # Duplicate row in file!
    ws.append(['Alan Turing', 'CS-102', 3])         # Exceeds max ranking hours/units!

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (buf, 'bad_rows.xlsx')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data['success'] is True
    rows = json_data['rows']

    # Row 1 (Unknown Prof - auto-created in preview)
    assert rows[0]['status'] == 'Ready'
    assert rows[0].get('is_new_professor') is True

    # Row 2 (Unknown Course)
    assert rows[1]['status'] == 'Error'
    assert 'Course not found' in rows[1]['reason']

    # Row 3 (Section = 0)
    assert rows[2]['status'] == 'Error'
    assert 'at least 1' in rows[2]['reason'] or 'whole number' in rows[2]['reason']

    # Row 4 (Blank section)
    assert rows[3]['status'] == 'Error'
    assert 'blank' in rows[3]['reason'].lower() or 'whole number' in rows[3]['reason'].lower()

    # Row 5 (Text section)
    assert rows[4]['status'] == 'Error'
    assert 'whole number' in rows[4]['reason'] or 'four' in rows[4]['reason']

    # Row 6 (Decimal section)
    assert rows[5]['status'] == 'Error'
    assert 'whole number' in rows[5]['reason']

    # Row 8 (Duplicate prof+course in file)
    assert rows[7]['status'] == 'Error'
    assert 'Duplicate' in rows[7]['reason']

    # Row 9 (CS-102 with 3 sections - ranking limit checks removed, valid)
    assert rows[8]['status'] in ('Ready', 'Updated')


# =============================================================================
# 6. DOWNLOAD ERROR REPORT (.XLSX)
# =============================================================================
def test_download_error_report(test_client, monkeypatch):
    """Download error report returns valid Excel file listing skipped rows."""
    mock_db = MockSupabase(professors=[], courses=[])
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NAME', 'Course Code', 'Number of sections'])
    ws.append(['Ghost Professor', 'GHOST-101', 3])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    res_prev = test_client.post(
        '/professor_load/import/preview',
        data={'file': (buf, 'error_file.xlsx')},
        content_type='multipart/form-data'
    )
    token = res_prev.get_json()['token']

    res_err = test_client.post('/professor_load/import/error-report', data={'token': token})
    assert res_err.status_code == 200
    assert 'spreadsheetml' in res_err.content_type

    err_wb = openpyxl.load_workbook(io.BytesIO(res_err.data))
    sheet = err_wb.active
    assert 'Row' in str(sheet.cell(1, 1).value)
    assert sheet.cell(2, 2).value == 'Ghost Professor'
    assert 'not found' in str(sheet.cell(2, 6).value)


# =============================================================================
# 7. ROLE ACCESS RESTRICTIONS (VIEWER BLOCKED)
# =============================================================================
def test_viewer_role_access_blocked(test_client, monkeypatch):
    """Viewer-role user is blocked from template, preview, confirm, error-report."""
    with test_client.session_transaction() as sess:
        sess['user_id'] = 'viewer-1'
        sess['role'] = 'viewer'

    # Template
    res = test_client.get('/professor_load/import/template')
    assert res.status_code in (302, 403)

    # Preview
    res = test_client.post('/professor_load/import/preview')
    assert res.status_code == 403

    # Confirm
    res = test_client.post('/professor_load/import/confirm')
    assert res.status_code == 403

    # Error report
    res = test_client.post('/professor_load/import/error-report')
    assert res.status_code == 403


# =============================================================================
# 8. CSV IMPORT COMPATIBILITY
# =============================================================================
def test_csv_import_support(test_client, monkeypatch):
    """Confirm CSV format is seamlessly parsed and validated."""
    mock_db = MockSupabase(
        professors=[{'prof_id': 1, 'first_name': 'Ada', 'last_name': 'Lovelace', 'department': 'CICT'}],
        courses=[{'course_id': 1, 'course_name': 'CC-101', 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3, 'year_level': 1}]
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    csv_content = "NAME,Course Code,Number of sections\n1st Year,,\nAda Lovelace,CC-101,3\n".encode('utf-8')

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(csv_content), 'import.csv')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data['success'] is True
    assert json_data['summary']['total_rows'] == 1
    assert json_data['summary']['valid_count'] == 1
    assert json_data['rows'][0]['course_code'] == 'CC-101'
    assert json_data['rows'][0]['sections'] == 3


# =============================================================================
# 9. STEP 3 VERIFICATION TESTS: SAME PREVIEW, CSV VARIANTS & BAD FILES
# =============================================================================
def test_import_xlsx_and_csv_same_preview(test_client, monkeypatch):
    """
    Upload import.xlsx and import.csv and confirm both give the SAME preview:
    63 data rows, 3 year dividers skipped, 39 professors, 21 course codes,
    and 5 '(WST)1' rows auto-cleaned with a note.
    """
    _setup_import_test_environment(test_client, monkeypatch)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    # 1. Preview import.xlsx
    with open('import.xlsx', 'rb') as f:
        xlsx_bytes = f.read()
    res_xlsx = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(xlsx_bytes), 'import.xlsx')},
        content_type='multipart/form-data'
    )
    assert res_xlsx.status_code == 200
    assert 'application/json' in res_xlsx.content_type
    data_xlsx = res_xlsx.get_json()
    assert data_xlsx['ok'] is True
    assert data_xlsx['success'] is True

    # 2. Preview import.csv (binary zip with .csv extension)
    with open('import.csv', 'rb') as f:
        csv_bytes = f.read()
    res_csv = test_client.post(
        '/professor_load/import/preview',
        data={'file': (io.BytesIO(csv_bytes), 'import.csv')},
        content_type='multipart/form-data'
    )
    assert res_csv.status_code == 200
    assert 'application/json' in res_csv.content_type
    data_csv = res_csv.get_json()
    assert data_csv['ok'] is True
    assert data_csv['success'] is True

    # Verify counts on both
    for data in (data_xlsx, data_csv):
        summary = data['summary']
        assert summary['total_rows'] == 63
        assert summary['year_dividers_count'] == 3
        assert summary['professors_affected'] == 39
        assert summary['courses_affected'] == 21
        assert len(data['rows']) == 63
        assert len(data['professor_workloads']) == 39
        assert len(data['course_section_totals']) == 21

        # Check 5 (WST)1 rows auto-cleaned
        wst_cleaned = [r for r in data['rows'] if r.get('was_cleaned') and '(WST)' in r.get('course_code', '')]
        assert len(wst_cleaned) == 5
        for wr in wst_cleaned:
            assert wr['course_code'] == 'IT-CAP01 (WST)'
            assert 'Auto-cleaned' in wr['cleaning_note']

    # Confirm rows match exactly between xlsx and csv
    assert len(data_xlsx['rows']) == len(data_csv['rows'])
    for r_x, r_c in zip(data_xlsx['rows'], data_csv['rows']):
        assert r_x['professor_name'] == r_c['professor_name']
        assert r_x['course_code'] == r_c['course_code']
        assert r_x['sections'] == r_c['sections']
        assert r_x['year_level'] == r_c['year_level']
        assert r_x['status'] == r_c['status']


def test_csv_variants(test_client, monkeypatch):
    """
    Test CSV variants:
    - saved from Excel as 'CSV UTF-8'
    - plain 'CSV (Comma delimited)' (cp1252)
    - semicolon-delimited
    - tab-delimited
    - with a BOM
    - uppercase extension (.CSV / .XLSX)
    - file with blank lines at the end
    """
    mock_db = MockSupabase(
        professors=[
            {'prof_id': 1, 'first_name': 'Alexander S.', 'last_name': 'Cochanco', 'department': 'CICT'},
            {'prof_id': 2, 'first_name': 'René', 'last_name': 'Descartes', 'department': 'CICT'},
        ],
        courses=[
            {'course_id': 1, 'course_name': 'CC-102', 'units': 3, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'year_level': 1}
        ]
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    variants = [
        # 1. Plain CSV UTF-8
        ('plain_utf8.csv', "NAME,Course Code,Number of sections\n1st Year,,\nAlexander S. Cochanco,CC-102,4\n".encode('utf-8')),
        # 2. CSV UTF-8 with BOM
        ('bom.csv', ('\ufeff' + "NAME,Course Code,Number of sections\n1st Year,,\nAlexander S. Cochanco,CC-102,4\n").encode('utf-8')),
        # 3. cp1252 with accented character
        ('cp1252.csv', "NAME,Course Code,Number of sections\n1st Year,,\nRené Descartes,CC-102,4\n".encode('cp1252')),
        # 4. Semicolon-delimited
        ('semicolon.csv', "NAME;Course Code;Number of sections\n1st Year;;\nAlexander S. Cochanco;CC-102;4\n".encode('utf-8')),
        # 5. Tab-delimited
        ('tab.csv', "NAME\tCourse Code\tNumber of sections\n1st Year\t\t\nAlexander S. Cochanco\tCC-102\t4\n".encode('utf-8')),
        # 6. Uppercase .CSV extension
        ('UPPER.CSV', "NAME,Course Code,Number of sections\n1st Year,,\nAlexander S. Cochanco,CC-102,4\n".encode('utf-8')),
        # 7. Blank trailing lines
        ('trailing_blanks.csv', "NAME,Course Code,Number of sections\n1st Year,,\nAlexander S. Cochanco,CC-102,4\n\n  \n\n".encode('utf-8')),
    ]

    for fname, content in variants:
        res = test_client.post(
            '/professor_load/import/preview',
            data={'file': (io.BytesIO(content), fname)},
            content_type='multipart/form-data'
        )
        assert res.status_code == 200, f"Failed on variant {fname}: {res.get_json()}"
        data = res.get_json()
        assert data['ok'] is True
        assert data['success'] is True
        assert len(data['rows']) == 1, f"Expected 1 row for {fname}, got {len(data['rows'])}"
        assert data['rows'][0]['course_code'] == 'CC-102'


def test_bad_files_comprehensive(test_client, monkeypatch):
    """
    Test bad files:
    .json, .txt, .pdf, an empty file, a file with no header, and a renamed-but-fake .xlsx.
    Each must show a clear, friendly JSON error and never an HTML page or a raw parse error.
    """
    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'

    bad_files = [
        ('test.json', b'{"name": "test"}', 400, 'Unsupported file type'),
        ('test.txt', b'some plain text', 400, 'Unsupported file type'),
        ('test.pdf', b'%PDF-1.4 binary data', 400, 'Unsupported file type'),
        ('empty.csv', b'', 400, 'empty'),
        ('empty.xlsx', b'', 400, 'empty'),
        ('no_header.csv', b'Random Text,123,456\nAnother line,789,0', 400, 'Missing required header row'),
        ('fake_renamed.xlsx', b'This is just a text file named as xlsx', 400, 'Failed to read Excel file'),
    ]

    for fname, content, expected_status, expected_snippet in bad_files:
        res = test_client.post(
            '/professor_load/import/preview',
            data={'file': (io.BytesIO(content), fname)},
            content_type='multipart/form-data'
        )
        assert 'application/json' in res.content_type, f"Expected JSON content-type for {fname}, got {res.content_type}"
        assert res.status_code == expected_status, f"Expected {expected_status} for {fname}, got {res.status_code}"
        data = res.get_json()
        assert data['ok'] is False
        assert data['success'] is False
        assert expected_snippet.lower() in data['error'].lower(), f"Expected '{expected_snippet}' in error '{data['error']}' for {fname}"


def test_auto_create_professors_on_import(test_client, monkeypatch):
    """Confirm unknown professors in import file are auto-created in professor table with program_id."""
    mock_db = MockSupabase(
        professors=[
            {'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1}
        ],
        courses=[
            {'course_id': 101, 'course_name': 'CC-101', 'program_id': 1, 'units': 3, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'year_level': 1}
        ],
        professor_loads=[]
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NAME', 'Course Code', 'Number of sections'])
    ws.append(['Grace Hopper', 'CC-101', 2])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-1'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1

    # 1. Preview
    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (buf, 'new_prof.xlsx')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 200
    preview_data = res.get_json()
    assert preview_data['success'] is True
    assert preview_data['summary']['new_professors_count'] == 1
    assert preview_data['rows'][0]['is_new_professor'] is True
    assert preview_data['rows'][0]['status'] == 'Ready'
    token = preview_data['token']

    # 2. Confirm
    res_confirm = test_client.post(
        '/professor_load/import/confirm',
        data={'token': token}
    )
    assert res_confirm.status_code == 200
    confirm_data = res_confirm.get_json()
    assert confirm_data['success'] is True
    assert confirm_data['new_professors_count'] == 1

    # Verify load was assigned with professor_name attribute
    load_table = mock_db.table('professor_load').data
    assert len(load_table) == 1
    assert load_table[0]['professor_name'] == 'Grace Hopper'
    assert load_table[0]['course_id'] == 101
    assert load_table[0]['sections'] == 2


def test_scheduler_scoped_to_own_program_cross_program_error(test_client, monkeypatch):
    """Confirm Schedulers receive exact error 'Course not offered in <Target_Program>' on cross-program courses."""
    mock_db = MockSupabase(
        professors=[{'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing'}],
        courses=[
            {'course_id': 101, 'course_name': 'CS-101', 'program_id': 1, 'program': 'BSIT', 'year_level': 1},
            {'course_id': 201, 'course_name': 'BA-101', 'program_id': 2, 'program': 'BSBA', 'year_level': 1},
        ],
        programs=[
            {'id': 1, 'program_name': 'BSIT'},
            {'id': 2, 'program_name': 'BSBA'},
        ]
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NAME', 'Course Code', 'Number of sections'])
    ws.append(['Alan Turing', 'BA-101', 2])  # Course belonging to BSBA, not BSIT!

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'sched-1'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1
        sess['program'] = 'BSIT'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (buf, 'cross_prog.xlsx')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data['success'] is True
    rows = json_data['rows']
    assert len(rows) == 1
    assert rows[0]['status'] == 'Error'
    assert 'Course not offered in BSIT' in rows[0]['reason']


def test_import_ilp_hours_parsing_and_validation(test_client, monkeypatch):
    """Confirm ILP hours in (0, 1) are accepted and invalid values are rejected."""
    mock_db = MockSupabase(
        professors=[{'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'program_id': 1}],
        courses=[
            {'course_id': 101, 'course_name': 'CS-101', 'program_id': 1, 'program': 'BSIT', 'year_level': 1},
            {'course_id': 102, 'course_name': 'CS-102', 'program_id': 1, 'program': 'BSIT', 'year_level': 1},
            {'course_id': 103, 'course_name': 'CS-103', 'program_id': 1, 'program': 'BSIT', 'year_level': 1},
        ],
        programs=[{'id': 1, 'program_name': 'BSIT'}]
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NAME', 'Course Code', 'Number of sections', 'ILP Hours'])
    ws.append(['Alan Turing', 'CS-101', 1, 1])   # Valid: ilp_hours = 1
    ws.append(['Alan Turing', 'CS-102', 1, 0])   # Valid: ilp_hours = 0
    ws.append(['Alan Turing', 'CS-103', 1, 3])   # Invalid: ilp_hours = 3 (must be 0 or 1)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 'sched-1'
        sess['role'] = 'scheduler'
        sess['program_id'] = 1
        sess['program'] = 'BSIT'

    res = test_client.post(
        '/professor_load/import/preview',
        data={'file': (buf, 'ilp_test.xlsx')},
        content_type='multipart/form-data'
    )
    assert res.status_code == 200
    json_data = res.get_json()
    rows = json_data['rows']
    assert rows[0]['status'] == 'Ready'
    assert rows[0]['ilp_hours'] == 1
    assert rows[1]['status'] == 'Ready'
    assert rows[1]['ilp_hours'] == 0
    assert rows[2]['status'] == 'Error'
    assert 'ILP hours must be 0 or 1' in rows[2]['reason']


