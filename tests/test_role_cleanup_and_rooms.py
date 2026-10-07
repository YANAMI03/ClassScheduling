"""Comprehensive tests for Dean/Chair role cleanup and Scheduler full management access."""

import pytest
from bs4 import BeautifulSoup
import app as app_module
from tests.test_rbac_permissions import FakeTable, FakeSupabase, _login_as


class FilteringFakeTable(FakeTable):
    def __init__(self, table_name, data=None):
        super().__init__(table_name, data)
        self._current_filters = {}
        self._in_filters = {}
        self._limit_val = None

    def select(self, *args, **kwargs):
        clone = FilteringFakeTable(self.table_name, self.data)
        return clone

    def insert(self, payload):
        if isinstance(payload, list):
            self.data.extend(payload)
        else:
            self.data.append(payload)
        return self

    def update(self, payload):
        for r in self.data:
            match = True
            for col, val in self._current_filters.items():
                if r.get(col) != val:
                    match = False
                    break
            if match:
                r.update(payload)
        return self

    def delete(self):
        to_keep = []
        for r in self.data:
            match = True
            for col, val in self._current_filters.items():
                if r.get(col) != val:
                    match = False
                    break
            if not match:
                to_keep.append(r)
        self.data = to_keep
        return self

    def eq(self, col, val):
        self._current_filters[col] = val
        return self

    def in_(self, col, val_list):
        self._in_filters[col] = list(val_list)
        return self

    def limit(self, count):
        self._limit_val = count
        return self

    def single(self):
        return self

    def execute(self):
        class Resp:
            def __init__(self, d):
                self.data = d
        filtered = list(self.data)
        for col, val in self._current_filters.items():
            filtered = [r for r in filtered if r.get(col) == val]
        for col, vals in self._in_filters.items():
            filtered = [r for r in filtered if r.get(col) in vals]
        if self._limit_val is not None:
            filtered = filtered[:self._limit_val]
        return Resp(filtered)


class ExtendedFakeSupabase(FakeSupabase):
    def __init__(self):
        super().__init__()
        self.tables['room'] = FilteringFakeTable('room', [
            {'room_id': 101, 'room_name': 'Lab 101', 'room_type': 'Laboratory Room', 'program_id': 1},
            {'room_id': 102, 'room_name': 'Lec 102', 'room_type': 'Lecture Room', 'program_id': 1},
            {'room_id': 103, 'room_name': 'Active Room 103', 'room_type': 'Lecture Room', 'program_id': 1},
        ])
        self.tables['course'] = FilteringFakeTable('course', [
            {'course_id': 10, 'course_name': 'IT 101', 'program_id': 1, 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3},
            {'course_id': 20, 'course_name': 'IT 102', 'program_id': 1, 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3},
        ])
        self.tables['professor'] = FilteringFakeTable('professor', [
            {'prof_id': 1, 'first_name': 'John', 'last_name': 'Doe', 'academic_ranking_id': 1, 'program_id': 1},
            {'prof_id': 2, 'first_name': 'Jane', 'last_name': 'Smith', 'academic_ranking_id': 2, 'program_id': 1},
        ])
        self.tables['academic_ranking'] = FilteringFakeTable('academic_ranking', [
            {'academic_ranking_id': 1, 'ranking_name': 'Instructor I', 'name': 'Instructor I', 'max_teaching_load': 18, 'max_preparations': 3, 'rate_per_hour': 200, 'program_id': 1},
            {'academic_ranking_id': 2, 'ranking_name': 'Assistant Professor', 'name': 'Assistant Professor', 'max_teaching_load': 21, 'max_preparations': 4, 'rate_per_hour': 300, 'program_id': 1},
        ])
        self.tables['timeslot'] = FilteringFakeTable('timeslot', [
            {'timeslot_id': 1, 'day': 'Monday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'},
            {'timeslot_id': 2, 'day': 'Saturday', 'start_time': '08:00:00', 'end_time': '12:00:00', 'lunch_time': None},
        ])
        self.tables['professor_load'] = FilteringFakeTable('professor_load', [
            {'id': 1, 'professor_load_id': 1, 'prof_id': 2, 'course_id': 20, 'sections': 1},
            {'id': 2, 'professor_load_id': 2, 'prof_id': 1, 'course_id': 10, 'sections': 1},
        ])
        self.tables['schedule'] = FilteringFakeTable('schedule', [
            {
                'schedule_id': 5001,
                'room_id': 103,
                'course_id': 20,
                'prof_id': 2,
                'professor_load_id': 1,
                'archive': False,
                'section': 'BSIT-1A',
                'day': 'Monday',
                'class_start': '08:00:00',
                'class_end': '11:00:00',
                'program_id': 1,
                'semester': '1st Semester',
            }
        ])


@pytest.fixture
def extended_client(monkeypatch):
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    fake_db = ExtendedFakeSupabase()
    monkeypatch.setattr(app_module, 'supabase', fake_db)
    monkeypatch.setattr(app_module, 'log_activity', lambda *args, **kwargs: None)
    with app_module.app.test_client() as client:
        yield client, fake_db


# ──────────────────────────────────────────────────────────────────────────────
# 1. Rooms (/rooms)
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_rooms_page_ui(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'admin')

    res = client.get('/rooms')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')

    assert 'read-only mode' not in html.lower()
    assert 'restricted to' not in html.lower()
    assert 'dean' not in html.lower()
    assert 'chair' not in html.lower()

    add_form = soup.find('form', id='addRoomForm')
    assert add_form is not None, "Admin should see the Add Room form"
    assert soup.find('input', id='add_room_name') is not None
    assert soup.find('button', id='addRoomBtn') is not None
    assert 'Actions' in html
    edit_buttons = soup.find_all('button', class_='edit-btn')
    assert len(edit_buttons) > 0, "Admin should see Edit buttons"
    delete_links = [a for a in soup.find_all('a', href=True) if '/delete_room/' in a['href']]
    assert len(delete_links) > 0, "Admin should see Delete buttons"


def test_admin_room_write_actions_and_protection(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'admin')

    # Add Room
    res_add = client.post('/add_room', data={'room_name': 'Room 201', 'room_type': 'Lecture Room', 'program_id': 1})
    assert res_add.status_code in (200, 302)

    # Edit Room
    res_edit = client.post('/edit_room/101', data={'room_name': 'Lab 101 Updated', 'room_type': 'Laboratory Room', 'program_id': 1})
    assert res_edit.status_code in (200, 302)

    # Delete unused room (102) -> allowed
    res_del_unused = client.get('/delete_room/102')
    assert res_del_unused.status_code in (200, 302)

    # Delete active room (103) -> blocked
    res_del_active_ajax = client.post('/delete_room/103', headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    assert res_del_active_ajax.status_code == 400
    assert 'active schedule' in res_del_active_ajax.get_json().get('message', '').lower()


def test_scheduler_and_viewer_blocked_from_rooms(extended_client):
    client, _ = extended_client
    _login_as(client, 'scheduler')
    assert client.get('/rooms').status_code == 403
    assert client.post('/add_room', data={'room_name': 'R'}).status_code == 403

    _login_as(client, 'viewer')
    assert client.get('/rooms').status_code == 403


# ──────────────────────────────────────────────────────────────────────────────
# 2. Courses (/courses)
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_courses_crud_and_protection(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'admin')

    # Page view: no read-only banner, no dean/chair
    res = client.get('/courses')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'read-only mode' not in html.lower()
    assert 'dean' not in html.lower()
    assert 'chair' not in html.lower()

    # Add Course
    res_add = client.post('/add_course', data={
        'course_name': 'IT 103',
        'program': 'BSIT',
        'year_level': '1',
        'semester': '1st Semester',
        'lecture_hours': '3',
        'lab_hours': '0',
        'ilp_hours': '0',
        'units': '3',
    })
    assert res_add.status_code in (200, 302)

    # Edit Course
    res_edit = client.post('/edit_course/10', data={
        'course_name': 'IT 101 Updated',
        'program': 'BSIT',
        'year_level': '1',
        'semester': '1st Semester',
        'lecture_hours': '3',
        'lab_hours': '0',
        'ilp_hours': '0',
        'units': '3',
    })
    assert res_edit.status_code in (200, 302)

    # Delete course 20 which is in active schedule -> blocked
    res_del_in_use = client.post('/delete_course/20', headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    assert res_del_in_use.status_code == 400


def test_scheduler_blocked_from_courses(extended_client):
    client, _ = extended_client
    _login_as(client, 'scheduler')
    assert client.get('/courses').status_code == 403
    assert client.post('/add_course', data={'course_name': 'C'}).status_code == 403
    assert client.post('/edit_course/10', data={'course_name': 'C'}).status_code == 403
    assert client.get('/delete_course/10').status_code == 403


# ──────────────────────────────────────────────────────────────────────────────
# 3. Professors (deleted routes return 404)
# ──────────────────────────────────────────────────────────────────────────────

def test_professors_routes_deleted_return_404(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'scheduler')

    # Deleted routes must return 404
    assert client.get('/professors').status_code == 404
    assert client.post('/add_professor', data={'first_name': 'Alice'}).status_code == 404
    assert client.post('/edit_professor/1', data={'first_name': 'Alice'}).status_code == 404
    assert client.get('/delete_professor/1').status_code == 404


def test_admin_blocked_from_professors(extended_client):
    client, _ = extended_client
    _login_as(client, 'admin')
    assert client.get('/professors').status_code == 404
    assert client.post('/add_professor', data={'first_name': 'P'}).status_code == 404
    assert client.post('/edit_professor/1', data={'first_name': 'P'}).status_code == 404
    assert client.get('/delete_professor/1').status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# 4. Academic Ranking (deleted routes return 404)
# ──────────────────────────────────────────────────────────────────────────────

def test_academic_ranking_routes_deleted_return_404(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'scheduler')

    # Deleted routes must return 404
    assert client.get('/academic_ranking').status_code == 404
    assert client.post('/add_academic_ranking', data={'ranking_name': 'Prof'}).status_code == 404
    assert client.post('/edit_academic_ranking/1', data={'ranking_name': 'Prof'}).status_code == 404
    assert client.get('/delete_academic_ranking/1').status_code == 404


def test_admin_blocked_from_academic_ranking(extended_client):
    client, _ = extended_client
    _login_as(client, 'admin')
    assert client.get('/academic_ranking').status_code == 404
    assert client.post('/add_academic_ranking', data={'ranking_name': 'R'}).status_code == 404
    assert client.post('/edit_academic_ranking/1', data={'ranking_name': 'R'}).status_code == 404
    assert client.get('/delete_academic_ranking/1').status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# 5. Timeslots (/timeslot)
# ──────────────────────────────────────────────────────────────────────────────

def test_admin_timeslot_crud_and_protection(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'admin')

    # Page view: no read-only banner, no dean/chair
    res = client.get('/timeslot')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'read-only mode' not in html.lower()
    assert 'dean' not in html.lower()
    assert 'chair' not in html.lower()

    # Add Timeslot
    res_add = client.post('/add_timeslot', data={
        'day': 'Friday',
        'start_time': '08:00',
        'end_time': '17:00',
        'lunch_time': '12:00',
    })
    assert res_add.status_code in (200, 302)

    # Edit Timeslot
    res_edit = client.post('/edit_timeslot/2', data={
        'start_time': '09:00',
        'end_time': '15:00',
    })
    assert res_edit.status_code in (200, 302)

    # Delete Monday (active classes scheduled on Monday) -> blocked
    res_del_in_use = client.post('/delete_timeslot/1', headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    assert res_del_in_use.status_code == 400

    # Delete Saturday (unused) -> allowed
    res_del_unused = client.post('/delete_timeslot/2', headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    assert res_del_unused.status_code == 200


def test_scheduler_blocked_from_timeslot(extended_client):
    client, _ = extended_client
    _login_as(client, 'scheduler')
    assert client.get('/timeslot').status_code == 403
    assert client.post('/add_timeslot', data={'day': 'Sunday'}).status_code == 403
    assert client.post('/edit_timeslot/1', data={'start_time': '08:00'}).status_code == 403
    assert client.get('/delete_timeslot/1').status_code == 403


# ──────────────────────────────────────────────────────────────────────────────
# 6. Professor Load & Generate Schedule
# ──────────────────────────────────────────────────────────────────────────────

def test_scheduler_professor_load_and_generate_schedule(extended_client):
    client, fake_db = extended_client
    _login_as(client, 'scheduler')

    # Professor load page
    res_load = client.get('/professor_load')
    assert res_load.status_code == 200
    html_load = res_load.data.decode('utf-8')
    assert 'dean' not in html_load.lower()
    assert 'chair' not in html_load.lower()

    # Load 1 is assigned in active schedule -> deletion blocked
    res_del_load_active = client.post('/delete_professor_load/1', headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    assert res_del_load_active.status_code == 400

    # Generate schedule page
    res_gen = client.get('/generate_schedule')
    assert res_gen.status_code == 200
    html_gen = res_gen.data.decode('utf-8')
    assert 'dean' not in html_gen.lower()
    assert 'chair' not in html_gen.lower()


def test_admin_blocked_from_load_and_generate(extended_client):
    client, _ = extended_client
    _login_as(client, 'admin')

    assert client.get('/professor_load').status_code == 403
    assert client.post('/delete_professor_load/1').status_code == 403
    assert client.get('/generate_schedule').status_code == 403
    assert client.post('/confirm_preview').status_code == 403
    assert client.post('/discard_preview').status_code == 403


# ──────────────────────────────────────────────────────────────────────────────
# 7. No Leftover Dean/Chair on Any Rendered Management Page
# ──────────────────────────────────────────────────────────────────────────────

def test_all_management_pages_clean_of_dean_and_chair(extended_client):
    client, _ = extended_client

    # Scheduler pages
    _login_as(client, 'scheduler')
    scheduler_pages = [
        '/professor_load',
        '/generate_schedule',
        '/',
        '/schedules',
    ]
    for path in scheduler_pages:
        res = client.get(path)
        assert res.status_code == 200, f"Scheduler page {path} returned status {res.status_code}"
        html = res.data.decode('utf-8')
        assert 'dean' not in html.lower(), f"Found 'dean' on {path}"
        assert 'chair' not in html.lower(), f"Found 'chair' on {path}"

    # Admin pages
    _login_as(client, 'admin')
    admin_pages = [
        '/rooms',
        '/courses',
        '/timeslot',
        '/schedules',
    ]
    for path in admin_pages:
        res = client.get(path)
        assert res.status_code == 200, f"Admin page {path} returned status {res.status_code}"
        html = res.data.decode('utf-8')
        assert 'dean' not in html.lower(), f"Found 'dean' on {path}"
        assert 'chair' not in html.lower(), f"Found 'chair' on {path}"
