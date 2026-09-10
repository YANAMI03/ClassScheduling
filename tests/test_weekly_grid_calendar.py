import pytest
from datetime import timedelta
import app as app_module


def test_section_availability_dynamic_vertical_spanning():
    """Verify that a 2-hour block visually spans twice the duration of a 1-hour block."""
    entries = [
        # Monday 1-hour class: 08:00 AM - 09:00 AM
        {
            'day': 'Monday',
            'start_time': '08:00:00',
            'end_time': '09:00:00',
            'course_name': 'CC-102',
            'session_type': 'Lecture',
            'professor': 'Bernadette Aquino',
            'section': '1A',
        },
        # Tuesday 2-hour class: 08:00 AM - 10:00 AM
        {
            'day': 'Tuesday',
            'start_time': '08:00:00',
            'end_time': '10:00:00',
            'course_name': 'IT-WS05',
            'session_type': 'Lecture',
            'professor': 'Diana Navarro',
            'section': '1A',
        },
    ]
    timeslots = [
        {'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}
    ]

    avail = app_module._calculate_section_availability(entries, timeslots=timeslots, section='1A')

    # Operating days should include Monday through Saturday
    assert 'Monday' in avail['operating_days']
    assert 'Tuesday' in avail['operating_days']
    assert 'Saturday' in avail['operating_days']

    # Check 1-hour block on Monday
    mon_blocks = [b for b in avail['day_calendar_blocks']['Monday'] if b['type'] == 'occupied']
    assert len(mon_blocks) == 1
    mon_1hr = mon_blocks[0]
    assert mon_1hr['course_name'] == 'CC-102'
    assert mon_1hr['duration_minutes'] == 60.0
    assert mon_1hr['duration_hours'] == 1.0

    # Check 2-hour block on Tuesday
    tue_blocks = [b for b in avail['day_calendar_blocks']['Tuesday'] if b['type'] == 'occupied']
    assert len(tue_blocks) == 1
    tue_2hr = tue_blocks[0]
    assert tue_2hr['course_name'] == 'IT-WS05'
    assert tue_2hr['duration_minutes'] == 120.0
    assert tue_2hr['duration_hours'] == 2.0

    # Vertical duration must be exactly 2x
    assert tue_2hr['duration_minutes'] == mon_1hr['duration_minutes'] * 2
    assert tue_2hr['duration_hours'] == mon_1hr['duration_hours'] * 2


def test_section_schedule_data_mapping():
    """Verify Section Schedule displays Subject Code, Class Type, Time Range, and Professor Name."""
    entries = [
        {
            'day': 'Monday',
            'start_time': '08:00:00',
            'end_time': '10:00:00',
            'course_name': 'CC-102',
            'session_type': 'Lecture',
            'professor': 'Bernadette Aquino',
            'section': '1A',
            'room_name': 'Room 101',
        }
    ]
    timeslots = [
        {'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}
    ]

    avail = app_module._calculate_section_availability(entries, timeslots=timeslots, section='1A')
    mon_blocks = [b for b in avail['day_calendar_blocks']['Monday'] if b['type'] == 'occupied']
    assert len(mon_blocks) == 1
    card = mon_blocks[0]

    # Required data mapping for Section Schedule:
    assert card['course_name'] == 'CC-102'          # Subject Code
    assert card['session_type'] == 'Lecture'        # Class Type
    assert card['time_range'] == '08:00 AM - 10:00 AM'  # Time Range
    assert card['professor'] == 'Bernadette Aquino' # Professor Name


def test_professor_schedule_data_mapping():
    """Verify Professor Schedule displays Subject Code, Class Type, Time Range, and Section Name/Room."""
    entries = [
        {
            'day': 'Wednesday',
            'start_time': '13:00:00',
            'end_time': '15:00:00',
            'course_name': 'IT-PF02',
            'session_type': 'Lecture',
            'section': '2F',
            'room_name': 'CL 1',
            'professor': 'Nicole Domingo',
        }
    ]
    timeslots = [
        {'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}
    ]

    avail = app_module._calculate_professor_availability(entries, timeslots=timeslots, professor='Nicole Domingo')
    wed_blocks = [b for b in avail['day_calendar_blocks']['Wednesday'] if b['type'] == 'occupied']
    assert len(wed_blocks) == 1
    card = wed_blocks[0]

    # Required data mapping for Professor Schedule:
    assert card['course_name'] == 'IT-PF02'         # Subject Code
    assert card['session_type'] == 'Lecture'        # Class Type
    assert card['time_range'] == '01:00 PM - 03:00 PM'  # Time Range
    assert card['section'] == '2F'                  # Section Name
    assert card['room_name'] == 'CL 1'              # Room


def test_available_slots_and_lunch_break_generation():
    """Verify unused time gaps render as Available cards and 12:00 PM - 01:00 PM renders as Lunch Break."""
    # Class from 08:00 AM to 10:00 AM.
    # Day is 08:00 AM to 08:00 PM (20:00).
    # Lunch is 12:00 PM to 01:00 PM.
    # Gaps should be:
    # 1. Available: 10:00 AM - 12:00 PM (2 hrs free)
    # 2. Lunch Break: 12:00 PM - 01:00 PM (Unavailable)
    # 3. Available: 01:00 PM - 08:00 PM (7 hrs free)
    entries = [
        {
            'day': 'Monday',
            'start_time': '08:00:00',
            'end_time': '10:00:00',
            'course_name': 'CC-102',
            'session_type': 'Lecture',
            'professor': 'Bernadette Aquino',
            'section': '1A',
        }
    ]
    timeslots = [
        {'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}
    ]

    avail = app_module._calculate_section_availability(entries, timeslots=timeslots, section='1A')
    mon_blocks = avail['day_calendar_blocks']['Monday']

    # Occupied block
    occ = [b for b in mon_blocks if b['type'] == 'occupied']
    assert len(occ) == 1
    assert occ[0]['time_range'] == '08:00 AM - 10:00 AM'

    # Lunch block
    lunch = [b for b in mon_blocks if b['type'] == 'lunch']
    assert len(lunch) == 1
    assert lunch[0]['time_range'] == '12:00 PM - 01:00 PM'
    assert 'Unavailable' in lunch[0]['label']

    # Available blocks
    avail_blocks = [b for b in mon_blocks if b['type'] == 'available']
    assert len(avail_blocks) == 2

    # First gap: 10:00 AM to 12:00 PM (2 hrs free)
    gap1 = avail_blocks[0]
    assert gap1['time_range'] == '10:00 AM - 12:00 PM'
    assert gap1['duration_hours'] == 2.0
    assert gap1['duration_text'] == '2 hrs'

    # Second gap: 01:00 PM to 08:00 PM (7 hrs free)
    gap2 = avail_blocks[1]
    assert gap2['time_range'] == '01:00 PM - 08:00 PM'
    assert gap2['duration_hours'] == 7.0
    assert gap2['duration_text'] == '7 hrs'


def test_header_status_indicators():
    """Verify utilization status indicator for each day in header (Fully Free, Partially Used, Fully Booked)."""
    entries = [
        # Monday partially used
        {
            'day': 'Monday',
            'start_time': '08:00:00',
            'end_time': '10:00:00',
            'course_name': 'CC-102',
            'session_type': 'Lecture',
            'section': '1A',
        },
        # Friday fully booked (08:00 to 12:00 and 13:00 to 20:00)
        {
            'day': 'Friday',
            'start_time': '08:00:00',
            'end_time': '12:00:00',
            'course_name': 'IT-CAP01',
            'session_type': 'Lecture',
            'section': '1A',
        },
        {
            'day': 'Friday',
            'start_time': '13:00:00',
            'end_time': '20:00:00',
            'course_name': 'IT-CAP02',
            'session_type': 'Lecture',
            'section': '1A',
        },
    ]
    timeslots = [
        {'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}
    ]

    avail = app_module._calculate_section_availability(entries, timeslots=timeslots, section='1A')
    day_map = avail['day_status_map']

    # Monday has 2 hours class -> Partially Used
    assert day_map['Monday']['status'] == 'partial'
    assert day_map['Monday']['label'] == 'Partially Used'
    assert day_map['Monday']['badge_class'] == 'badge-warning'

    # Tuesday has 0 hours class -> Fully Free
    assert day_map['Tuesday']['status'] == 'free'
    assert day_map['Tuesday']['label'] == 'Fully Free'
    assert day_map['Tuesday']['badge_class'] == 'badge-success'

    # Friday has all 11 usable hours occupied -> Fully Booked
    assert day_map['Friday']['status'] == 'booked'
    assert day_map['Friday']['label'] == 'Fully Booked'
    assert day_map['Friday']['badge_class'] == 'badge-danger'


def test_time_markers_hourly_intervals():
    """Verify time axis markers display single hourly intervals from start to end."""
    timeslots = [
        {'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}
    ]
    avail = app_module._calculate_schedule_availability([], timeslots=timeslots)
    markers = avail['time_markers']
    assert len(markers) == 13  # 08:00 AM to 08:00 PM inclusive = 13 hourly markers
    labels = [m['label'] for m in markers]
    assert labels[0] == '08:00 AM'
    assert labels[1] == '09:00 AM'
    assert labels[4] == '12:00 PM'
    assert labels[5] == '01:00 PM'
    assert labels[-1] == '08:00 PM'


def test_api_availability_endpoints(monkeypatch):
    """Test API availability endpoints for section and professor."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    # Mock supabase responses for professor and section
    class FakeTable:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'professor':
                return type('Resp', (), {'data': [{'prof_id': 1, 'first_name': 'Alan', 'last_name': 'Turing', 'department': 'CS', 'max_hours': 30}]})()
            elif self.table_name == 'prof_course':
                return type('Resp', (), {'data': []})()
            elif self.table_name == 'timeslot':
                return type('Resp', (), {'data': [{'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}]})()
            elif self.table_name == 'schedule':
                return type('Resp', (), {'data': []})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeTable(name))

    # Test /api/professor_availability/1
    resp_prof = client.get('/api/professor_availability/1')
    assert resp_prof.status_code == 200
    data_prof = resp_prof.get_json()
    assert data_prof['success'] is True
    assert 'availability' in data_prof
    assert 'day_calendar_blocks' in data_prof['availability']

    # Test /api/section_availability/1A
    resp_sec = client.get('/api/section_availability/1A')
    assert resp_sec.status_code == 200
    data_sec = resp_sec.get_json()
    assert data_sec['success'] is True
    assert 'availability' in data_sec
    assert 'day_calendar_blocks' in data_sec['availability']


def test_section_schedule_page_renders_weekly_grid(monkeypatch):
    """Test that /schedule/<section_name> renders the weekly grid calendar and required elements."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeTable:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 1,
                        'day': 'Monday',
                        'class_start': '08:00:00',
                        'class_end': '10:00:00',
                        'session_type': 'Lecture',
                        'semester': '1st Semester',
                        'major': 'CS',
                        'section': '1A',
                        'prof_course': {
                            'prof_course_id': 1,
                            'course': {'course_name': 'CC-102'},
                            'professor': {'first_name': 'Bernadette', 'last_name': 'Aquino'},
                        },
                        'room': {'room_name': 'Room 101'},
                    }
                ]})()
            elif self.table_name == 'timeslot':
                return type('Resp', (), {'data': [{'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}]})()
            elif self.table_name == 'room':
                return type('Resp', (), {'data': [{'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture'}]})()
            elif self.table_name == 'prof_course':
                return type('Resp', (), {'data': []})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeTable(name))

    resp = client.get('/schedule/1A')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Check structural rules
    assert 'Weekly Timetable Grid' in html
    assert 'calendar-grid-container' in html
    assert 'calendar-time-axis' in html
    assert 'calendar-days-grid' in html
    assert 'Monday' in html
    assert 'Saturday' in html

    # Check Section Data Mapping in HTML
    assert 'CC-102' in html
    assert 'Bernadette Aquino' in html
    assert '08:00 AM - 10:00 AM' in html

    # Check Lunch and Available blocks
    assert 'Lunch Break' in html
    assert 'Available' in html
    assert 'free' in html


def test_professor_schedule_page_renders_weekly_grid(monkeypatch):
    """Test that /professor_schedule/<id> renders the weekly grid calendar and required elements."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeTable:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def in_(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'professor':
                return type('Resp', (), {'data': [{'prof_id': 1, 'first_name': 'Nicole', 'last_name': 'Domingo', 'department': 'IT', 'max_hours': 30}]})()
            elif self.table_name == 'prof_course':
                return type('Resp', (), {'data': [
                    {'prof_course_id': 10, 'course_id': 5, 'course': {'course_name': 'IT-PF02'}}
                ]})()
            elif self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 100,
                        'prof_course_id': 10,
                        'room_id': 1,
                        'day': 'Wednesday',
                        'class_start': '13:00:00',
                        'class_end': '15:00:00',
                        'section': '2F',
                        'semester': '1st Semester',
                        'major': 'IT',
                        'session_type': 'Lecture',
                        'prof_course': {
                            'prof_course_id': 10,
                            'course': {'course_name': 'IT-PF02'}
                        },
                        'room': {'room_name': 'CL 1'}
                    }
                ]})()
            elif self.table_name == 'timeslot':
                return type('Resp', (), {'data': [{'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeTable(name))

    resp = client.get('/professor_schedule/1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Check structural rules
    assert 'Weekly Timetable Grid' in html
    assert 'calendar-grid-container' in html
    assert 'calendar-time-axis' in html
    assert 'calendar-days-grid' in html
    assert 'Wednesday' in html

    # Check Professor Data Mapping in HTML: Subject, Section Name, Room
    assert 'IT-PF02' in html
    assert '2F' in html
    assert 'CL 1' in html
    assert '01:00 PM - 03:00 PM' in html

    # Check Lunch and Available blocks
    assert 'Lunch Break' in html
    assert 'Available' in html


def test_section_schedule_cleanup_and_consolidated_view(monkeypatch):
    """Verify Section Schedule cleanup: no tabs, no standalone availability container, correct info bar & circular legend."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeTable:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def or_(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 1,
                        'day': 'Monday',
                        'class_start': '08:00:00',
                        'class_end': '10:00:00',
                        'session_type': 'Lecture',
                        'semester': '1st Semester',
                        'major': 'CS',
                        'section': '1A',
                        'prof_course': {
                            'prof_course_id': 1,
                            'course': {'course_name': 'CC-102'},
                            'professor': {'first_name': 'Bernadette', 'last_name': 'Aquino'},
                        },
                        'room': {'room_name': 'Room 101'},
                    }
                ]})()
            elif self.table_name == 'timeslot':
                return type('Resp', (), {'data': [{'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}]})()
            elif self.table_name == 'room':
                return type('Resp', (), {'data': [{'room_id': 1, 'room_name': 'Room 101', 'room_type': 'Lecture'}]})()
            elif self.table_name == 'prof_course':
                return type('Resp', (), {'data': []})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeTable(name))

    resp = client.get('/schedule/1A')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # 1. Standalone Day-by-Day Availability Status container must be completely removed
    assert 'Day-by-Day Availability Status' not in html
    assert '4h used • 7h free' not in html

    # 2. View Mode Switcher tabs and alternative list views must be completely removed
    assert 'sectionScheduleTabs' not in html
    assert 'classes-list-view' not in html
    assert 'free-periods-view' not in html

    # 3. Top Info Bar with exact text & circular legend
    assert 'Click on any class card to inspect details or edit entry. Free periods are marked in green.' in html
    assert 'calendar-info-bar' in html
    assert 'calendar-legend-container' in html
    assert 'calendar-legend-circle--blue' in html
    assert 'calendar-legend-circle--green-dotted' in html
    assert 'calendar-legend-circle--grey-dotted' in html
    assert 'Scheduled Class (Blue)' in html
    assert 'Available Free (Green)' in html
    assert 'Lunch Break (Grey)' in html

    # 4. Weekly grid calendar rendered directly as primary view
    assert 'calendar-grid-container' in html
    assert 'calendar-block-occupied' in html
    assert 'CC-102' in html
    assert 'Bernadette Aquino' in html


def test_professor_schedule_cleanup_and_consolidated_view(monkeypatch):
    """Verify Professor Schedule cleanup: no tabs, no standalone availability container, correct info bar & circular legend."""
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 'test-user-id'
        sess['role'] = 'admin'
        sess['email'] = 'admin@example.com'

    class FakeTable:
        def __init__(self, table_name):
            self.table_name = table_name

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def in_(self, *args, **kwargs):
            return self

        def execute(self):
            if self.table_name == 'professor':
                return type('Resp', (), {'data': [{'prof_id': 1, 'first_name': 'Nicole', 'last_name': 'Domingo', 'department': 'IT', 'max_hours': 30}]})()
            elif self.table_name == 'prof_course':
                return type('Resp', (), {'data': [
                    {'prof_course_id': 10, 'course_id': 5, 'course': {'course_name': 'IT-PF02'}}
                ]})()
            elif self.table_name == 'schedule':
                return type('Resp', (), {'data': [
                    {
                        'schedule_id': 100,
                        'prof_course_id': 10,
                        'room_id': 1,
                        'day': 'Wednesday',
                        'class_start': '13:00:00',
                        'class_end': '15:00:00',
                        'section': '2F',
                        'semester': '1st Semester',
                        'major': 'IT',
                        'session_type': 'Lecture',
                        'prof_course': {
                            'prof_course_id': 10,
                            'course': {'course_name': 'IT-PF02'}
                        },
                        'room': {'room_name': 'CL 1'}
                    }
                ]})()
            elif self.table_name == 'timeslot':
                return type('Resp', (), {'data': [{'start_day': 'Monday', 'end_day': 'Saturday', 'start_time': '08:00:00', 'end_time': '20:00:00', 'lunch_time': '12:00:00'}]})()
            return type('Resp', (), {'data': []})()

    monkeypatch.setattr(app_module.supabase, 'table', lambda name: FakeTable(name))

    resp = client.get('/professor_schedule/1')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # 1. Standalone Day-by-Day Availability Status container must be completely removed
    assert 'Day-by-Day Availability Status' not in html

    # 2. View Mode Switcher tabs and alternative list views must be completely removed
    assert 'professorScheduleTabs' not in html
    assert 'classes-list-view' not in html
    assert 'free-periods-view' not in html

    # 3. Top Info Bar with exact text & circular legend
    assert 'Click on any class card to inspect details or edit entry. Free periods are marked in green.' in html
    assert 'calendar-info-bar' in html
    assert 'calendar-legend-container' in html
    assert 'calendar-legend-circle--blue' in html
    assert 'calendar-legend-circle--green-dotted' in html
    assert 'calendar-legend-circle--grey-dotted' in html
    assert 'Scheduled Class (Blue)' in html
    assert 'Available Free (Green)' in html
    assert 'Lunch Break (Grey)' in html

    # 4. Weekly grid calendar rendered directly as primary view
    assert 'calendar-grid-container' in html
    assert 'calendar-block-occupied' in html
    assert 'IT-PF02' in html
    assert '2F' in html
    assert 'CL 1' in html


def test_calendar_css_styling_rules():
    """Verify CSS rules for calendar: no vertical scrollbar, dotted borders for lunch/available, solid light-blue cards without left accent."""
    with open('static/style.css', 'r', encoding='utf-8') as f:
        css = f.read()

    # Verify no vertical scroll in container
    assert '.calendar-scroll-container' in css
    assert 'overflow-y: visible;' in css

    # Verify card styling - solid light-blue and no accent left border
    assert '.calendar-block-occupied' in css
    assert 'background: #eff6ff !important;' in css
    assert 'border-left: 1px solid #bfdbfe !important;' in css

    # Verify dotted borders
    assert 'border: 1.5px dotted #94a3b8;' in css  # Lunch
    assert 'border: 1.5px dotted #22c55e;' in css  # Available

    # Verify circular legend classes
    assert '.calendar-legend-circle--blue' in css
    assert '.calendar-legend-circle--green-dotted' in css
    assert '.calendar-legend-circle--grey-dotted' in css


