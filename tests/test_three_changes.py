import pytest
from bs4 import BeautifulSoup
import io
import openpyxl
import app as app_module
from professor_load_importer import validate_import_data, parse_import_file


class MockTable:
    def __init__(self, name, data=None):
        self.name = name
        self.data = list(data or [])
        self.inserted = []
        self.updated = []
        self.deleted = []
        self._filters = {}
        self._in_filters = {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, rows):
        if isinstance(rows, list):
            self.inserted.extend(rows)
            self.data.extend(rows)
        else:
            self.inserted.append(rows)
            self.data.append(rows)
        return self

    def update(self, payload):
        self.updated.append(payload)
        return self

    def delete(self):
        self.deleted.append(dict(self._filters))
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, val):
        self._in_filters[col] = val
        return self

    def ilike(self, col, val):
        return self

    def limit(self, n):
        return self

    def order(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        res = []
        for item in self.data:
            match = True
            for col, val in self._filters.items():
                if str(item.get(col)) != str(val):
                    match = False
                    break
            for col, val_list in self._in_filters.items():
                val_strs = [str(v) for v in val_list]
                if str(item.get(col)) not in val_strs:
                    match = False
                    break
            if match:
                res.append(item)

        class Response:
            pass

        r = Response()
        r.data = res
        return r


class MockSupabase:
    def __init__(self, professors=None, courses=None, professor_loads=None, professor_programs=None, programs=None, users=None):
        self.tables = {
            'professor': MockTable('professor', professors or []),
            'course': MockTable('course', courses or []),
            'professor_load': MockTable('professor_load', professor_loads or []),
            'professor_program': MockTable('professor_program', professor_programs or []),
            'program': MockTable('program', programs or []),
            'program_department': MockTable('program_department', [{'program': 'BSIT', 'department': 'CICT'}, {'program': 'BSDS', 'department': 'CICT'}]),
            'users': MockTable('users', users or []),
            'academic_ranking': MockTable('academic_ranking', [
                {'academic_ranking_id': 1, 'name': 'Instructor', 'min_units': 12, 'max_units': 24, 'min_hours': 15, 'max_hours': 40}
            ]),
            'activity_log': MockTable('activity_log', []),
            'schedule': MockTable('schedule', []),
            'delete_requests': MockTable('delete_requests', []),
        }

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name)
        return self.tables[name]


@pytest.fixture
def test_setup(monkeypatch):
    profs = [
        {'prof_id': 10, 'first_name': 'Alice', 'last_name': 'ITOnly', 'program_id': 1, 'specialization': 'Web'},
        {'prof_id': 20, 'first_name': 'Bob', 'last_name': 'DSOnly', 'program_id': 2, 'specialization': 'Data'},
        {'prof_id': 30, 'first_name': 'Charlie', 'last_name': 'BothProgs', 'program_id': 1, 'specialization': 'AI'},
    ]
    prof_progs = [
        {'prof_id': 10, 'program_id': 1},
        {'prof_id': 20, 'program_id': 2},
        {'prof_id': 30, 'program_id': 1},
        {'prof_id': 30, 'program_id': 2},
    ]
    courses = [
        {'course_id': 101, 'course_code': 'IT-101', 'program_id': 1, 'program': 'BSIT', 'year_level': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3},
        {'course_id': 201, 'course_code': 'DS-101', 'program_id': 2, 'program': 'BSDS', 'year_level': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3},
    ]
    loads = [
        {'id': 1, 'prof_id': 10, 'course_id': 101, 'sections': 1, 'professor': profs[0], 'course': courses[0]},
        {'id': 2, 'prof_id': 20, 'course_id': 201, 'sections': 1, 'professor': profs[1], 'course': courses[1]},
        {'id': 3, 'prof_id': 30, 'course_id': 101, 'sections': 1, 'professor': profs[2], 'course': courses[0]},
        {'id': 4, 'prof_id': 30, 'course_id': 201, 'sections': 1, 'professor': profs[2], 'course': courses[1]},
    ]
    programs = [
        {'id': 1, 'program_name': 'BSIT', 'full_name': 'BS Information Tech'},
        {'id': 2, 'program_name': 'BSDS', 'full_name': 'BS Data Science'},
    ]

    mock_db = MockSupabase(
        professors=profs,
        courses=courses,
        professor_loads=loads,
        professor_programs=prof_progs,
        programs=programs,
    )
    monkeypatch.setattr(app_module, 'supabase', mock_db)
    app = app_module.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'

    return app, mock_db


# ── CHANGE 1 TESTS: TEACHING LOAD SUMMARY RESTORED ──

def test_change1_teaching_load_summary_markup(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        res = client.get('/professor_load')
        assert res.status_code == 200
        html = res.data.decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')

        # Semicircular SVG gauge must NOT exist
        assert soup.find('svg', class_='load-gauge__svg') is None

        # Summary card header with status pill
        pill = soup.find(id='load-status-badge')
        assert pill is not None
        assert 'Awaiting Selection' in pill.text

        # Weekly Hours row (Total Units widget removed)
        assert soup.find(id='lbl-total-hours') is not None
        assert soup.find(id='bar-total-hours') is not None

        # 2-cell box for counts
        assert soup.find(id='lbl-selected-courses-count') is not None
        assert soup.find(id='lbl-selected-sections-count') is not None

        # Breakdown section and info placeholder
        assert soup.find(id='selected-courses-breakdown-card') is not None
        assert soup.find(id='load-feedback-alert') is not None


# ── CHANGE 2 TESTS: PROFESSORS SCOPED PER PROGRAM ──

def test_change2_scheduler_professor_scoping(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        # BSIT Scheduler
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        # /professors route is removed -> returns 404
        res = client.get('/professors')
        assert res.status_code == 404

        # Table in professor_load only includes scoped professors
        res_load = client.get('/professor_load')
        assert res_load.status_code == 200
        load_html = res_load.data.decode('utf-8')
        assert 'Alice ITOnly' in load_html
        assert 'Charlie BothProgs' in load_html
        assert 'Bob DSOnly' not in load_html


def test_change2_scheduler_tampering_403(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        # Removed routes return 404 (never 403)
        assert client.get('/api/professor_load/20').status_code == 404
        assert client.post('/add_professor_load', data={'prof_id': '20', 'course_ids': ['101']}).status_code == 404
        assert client.post('/edit_professor/20', data={'academic_ranking_id': '1'}).status_code == 404
        assert client.post('/delete_professor/20', headers={'X-Requested-With': 'XMLHttpRequest'}).status_code == 404

        # Prof 20 is BSDS-only. Tampering on scoped schedule routes returns 403
        assert client.get('/professor_schedule/20').status_code == 403
        assert client.get('/export/professor_schedule/20').status_code == 403
        assert client.get('/export/professor_schedule/20/pdf').status_code == 403
        assert client.get('/api/professor_availability/20').status_code == 403
        assert client.get('/api/professor_workload/20').status_code == 403


def test_change2_block_unlinking_with_existing_loads(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['role'] = 'admin'

        # /edit_professor was removed with professors entity -> returns 404
        res = client.post(
            '/edit_professor/30',
            data={'academic_ranking_id': '1', 'program_ids': ['2']},
            headers={'X-Requested-With': 'XMLHttpRequest'}
        )
        assert res.status_code == 404


def test_change2_importer_rejects_out_of_program_prof(test_setup):
    courses = [{'course_id': 101, 'course_code': 'IT-101', 'program_id': 1, 'program': 'BSIT'}]
    existing_loads = []

    # All professors in the system
    all_profs = [
        {'prof_id': 10, 'first_name': 'Alice', 'last_name': 'ITOnly', 'program_id': 1},
        {'prof_id': 20, 'first_name': 'Bob', 'last_name': 'DSOnly', 'program_id': 2},
    ]
    # Scoped to BSIT: only Alice (id=10)
    scoped_profs = [all_profs[0]]
    allowed_pids = {10}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['NAME', 'Course Code', 'Number of sections', 'ILP Hours'])
    ws.append(['Bob DSOnly', 'IT-101', 1, 0])
    buf = io.BytesIO()
    wb.save(buf)

    data_rows, dividers, _ = parse_import_file(buf.getvalue(), 'test.xlsx')

    result = validate_import_data(
        data_rows,
        dividers,
        scoped_profs,
        courses,
        existing_loads,
        all_courses_list=courses,
        all_professors_list=all_profs,
        allowed_prof_ids=allowed_pids,
        target_program_name='BSIT'
    )

    assert result['rows'][0]['status'] == 'Error'
    assert 'Professor not in BSIT' in result['rows'][0]['reason']


# ── CHANGE 3 TESTS: PROGRAM TABS ON /courses ──

def test_change3_courses_tabs_admin_vs_scheduler(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        # Admin sees "All Programs" + all program tabs
        with client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['role'] = 'admin'

        res_admin = client.get('/courses')
        assert res_admin.status_code == 200
        soup_admin = BeautifulSoup(res_admin.data.decode('utf-8'), 'html.parser')
        assert soup_admin.find(id='program-tab-all') is not None
        assert soup_admin.find(id='program-tab-1') is not None
        assert soup_admin.find(id='program-tab-2') is not None

        # Scheduler has zero access to courses (receives 403)
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        res_sched = client.get('/courses')
        assert res_sched.status_code == 403


def test_change3_scheduler_tampering_url_returns_403(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        # Schedulers requesting any courses URL must receive 403
        assert client.get('/courses?program=2').status_code == 403
        assert client.get('/courses?program=all').status_code == 403
        assert client.get('/courses?program=BSDS').status_code == 403
        assert client.get('/courses?program=1').status_code == 403


def test_change3_add_course_program_selection(test_setup):
    app, _ = test_setup
    with app.test_client() as client:
        # Scheduler: /courses and /add_course are blocked completely with 403
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        res_get = client.get('/courses')
        assert res_get.status_code == 403

        res_post = client.post('/add_course', data={
            'course_code': 'HACK-101',
            'program_id': '1',
            'year_level': '1',
            'semester': '1st Semester'
        })
        assert res_post.status_code == 403


def test_edit_modal_contains_teaching_load_summary_gauge(test_setup):
    """Verify edit modal and edit panel are completely removed from professor_load page."""
    app, _ = test_setup
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        res = client.get('/professor_load')
        assert res.status_code == 200
        html = res.data.decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')

        # Edit modal and panel must NOT exist
        assert soup.find('div', {'id': 'editModal'}) is None
        assert soup.find('div', {'id': 'edit-load-panel'}) is None


def test_no_loads_banner_uses_d_none_when_loads_exist(test_setup, monkeypatch):
    app, _ = test_setup
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'

        # 1. When professor loads exist: has d-none class and style="display: none;"
        monkeypatch.setattr(app_module, '_check_has_professor_loads', lambda prog_id=None: True)
        res_true = client.get('/')
        soup_true = BeautifulSoup(res_true.data.decode('utf-8'), 'html.parser')
        banner_true = soup_true.find(id='no-loads-banner')
        assert banner_true is not None
        classes_true = banner_true.get('class', [])
        assert 'd-none' in classes_true
        assert 'd-flex' not in classes_true
        assert 'display: none;' in (banner_true.get('style') or '')

        # 2. When professor loads do NOT exist: has d-flex class and NOT d-none
        monkeypatch.setattr(app_module, '_check_has_professor_loads', lambda prog_id=None: False)
        res_false = client.get('/')
        soup_false = BeautifulSoup(res_false.data.decode('utf-8'), 'html.parser')
        banner_false = soup_false.find(id='no-loads-banner')
        assert banner_false is not None
        classes_false = banner_false.get('class', [])
        assert 'd-flex' in classes_false
        assert 'd-none' not in classes_false
        assert 'display: none;' not in (banner_false.get('style') or '')

