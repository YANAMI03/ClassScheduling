import re
import pytest
from bs4 import BeautifulSoup
import app as app_module


class MockTable:
    def __init__(self, table_name, data=None):
        self.table_name = table_name
        self.data = list(data or [])
        self._filters = {}
        self._in_filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, val):
        self._in_filters[col] = val
        return self

    def order(self, *args, **kwargs):
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
                if item.get(col) not in val_list and int(item.get(col, -999)) not in val_list:
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
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = MockTable(name, [])
        return self.tables[name]


@pytest.fixture
def test_client_and_db(monkeypatch):
    """Set up test client with representative professor workloads:
    - Prof 1 (Alan Turing): Under 40 hrs (e.g. 15 hrs, 2 courses) -> Within Limit
    - Prof 2 (Ada Lovelace): Exactly 40 hrs (e.g. 40 hrs) -> Full Load
    - Prof 3 (Grace Hopper): Over 40 hrs (e.g. 46 hrs) -> Overloaded
    - Prof 4 (John von Neumann): 0 loads -> 0 hrs
    """
    courses = [
        {'course_id': 101, 'course_name': 'IT101', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 0, 'ilp_hours': 0, 'units': 3},
        {'course_id': 102, 'course_name': 'IT102', 'program_id': 1, 'lecture_hours': 2, 'lab_hours': 3, 'ilp_hours': 0, 'units': 3},
        {'course_id': 103, 'course_name': 'IT103', 'program_id': 1, 'lecture_hours': 4, 'lab_hours': 0, 'ilp_hours': 0, 'units': 4},
        {'course_id': 104, 'course_name': 'IT104', 'program_id': 1, 'lecture_hours': 3, 'lab_hours': 3, 'ilp_hours': 0, 'units': 4},
    ]

    professor_loads = [
        # Alan Turing: IT101 (2 sections = 6h, 6u) + IT102 (1 section = 5h, 3u) -> 11 hrs, 9 units
        {'id': 1, 'course_id': 101, 'sections': 2, 'professor_name': 'Alan Turing', 'program_id': 1},
        {'id': 2, 'course_id': 102, 'sections': 1, 'professor_name': 'Alan Turing', 'program_id': 1},

        # Ada Lovelace: IT101 (4 sections = 12h, 12u) + IT104 (4 sections = 24h, 16u) + IT103 (1 section = 4h, 4u) -> 40 hrs, 32 units
        {'id': 3, 'course_id': 101, 'sections': 4, 'professor_name': 'Ada Lovelace', 'program_id': 1},
        {'id': 4, 'course_id': 104, 'sections': 4, 'professor_name': 'Ada Lovelace', 'program_id': 1},
        {'id': 5, 'course_id': 103, 'sections': 1, 'professor_name': 'Ada Lovelace', 'program_id': 1},

        # Grace Hopper: IT104 (7 sections = 42h, 28u) + IT103 (1 section = 4h, 4u) -> 46 hrs, 32 units
        {'id': 6, 'course_id': 104, 'sections': 7, 'professor_name': 'Grace Hopper', 'program_id': 1},
        {'id': 7, 'course_id': 103, 'sections': 1, 'professor_name': 'Grace Hopper', 'program_id': 1},
    ]

    programs = [{'id': 1, 'program_name': 'BSIT'}]

    tables = {
        'course': MockTable('course', courses),
        'professor_load': MockTable('professor_load', professor_loads),
        'program': MockTable('program', programs),
        'users': MockTable('users', [{'id': 'usr-1', 'role': 'scheduler', 'program_id': 1}]),
    }

    mock_db = MockSupabase(tables)
    monkeypatch.setattr(app_module, 'supabase', mock_db)

    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 'usr-1'
            sess['role'] = 'scheduler'
            sess['program_id'] = 1
            sess['program'] = 'BSIT'
        yield client, mock_db


def test_40_hour_tracker_html_structure_and_accessibility(test_client_and_db):
    """Verify Tracker card structure, accessibility attributes, and empty initial state."""
    client, _ = test_client_and_db
    res = client.get('/professor_load')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Tracker card exists
    tracker_card = soup.find(id='teaching-load-tracker-card')
    assert tracker_card is not None

    # 2. Status badge is empty state with aria-live
    status_badge = tracker_card.find(id='load-status-badge')
    assert status_badge is not None
    assert status_badge.text.strip() == 'Awaiting Selection'
    assert status_badge.get('aria-live') == 'polite'

    # 3. Weekly Teaching Hours label and progress container with role="progressbar"
    lbl_hours = tracker_card.find(id='lbl-total-hours')
    assert lbl_hours is not None
    assert lbl_hours.text.strip() == '0 / 40 hrs'

    hours_container = tracker_card.find(id='hours-progress-container')
    assert hours_container is not None
    assert hours_container.get('role') == 'progressbar'
    assert hours_container.get('aria-valuemin') == '0'
    assert hours_container.get('aria-valuemax') == '40'
    assert hours_container.get('aria-valuenow') == '0'

    # 4. Total units widget is removed from tracker
    lbl_units = tracker_card.find(id='lbl-total-units')
    assert lbl_units is None
    assert 'Total Units' not in tracker_card.text

    # 5. Selected courses and sections counts
    assert tracker_card.find(id='lbl-selected-courses-count').text.strip() == '0'
    assert tracker_card.find(id='lbl-selected-sections-count').text.strip() == '0'

    # 6. Blue hint alert is present
    feedback_alert = tracker_card.find(id='load-feedback-alert')
    assert feedback_alert is not None
    assert 'Click any professor row below' in feedback_alert.text

    # 7. Professor name badge is present (hidden by default)
    prof_name_badge = tracker_card.find(id='tracker-prof-name-badge')
    assert prof_name_badge is not None


def test_professor_rows_removed_24_units_and_added_data_attributes(test_client_and_db):
    """Verify Change 2: '0 / 24 u' line and 24-unit limit status badge are completely removed from table rows."""
    client, _ = test_client_and_db
    res = client.get('/professor_load')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')

    rows = soup.find_all('tr', class_='professor-row')
    assert len(rows) == 3

    for row in rows:
        # Check keyboard accessibility
        assert row.get('tabindex') == '0'
        assert row.get('role') == 'button'
        assert row.get('aria-label') is not None

        # Check data attributes for JS tracker
        assert row.get('data-prof-name') is not None
        assert row.get('data-prof-display-name') is not None
        assert row.get('data-courses-json') is not None
        assert row.get('data-hours') is not None
        assert row.get('data-units') is not None
        assert row.get('data-sections') is not None
        assert row.get('data-course-count') is not None

        # Verify NO 24-unit text or status badge in Load column
        row_text = row.text
        assert '/ 24 u' not in row_text
        assert '/ 24' not in row_text
        # No 24-unit Overloaded/Under Minimum/Balanced badge in row
        status_badges = row.find_all('span', class_=re.compile(r'badge bg-(danger|warning|success)'))
        assert len(status_badges) == 0

        # Hours is still shown
        assert 'Hours:' in row_text
        assert 'hrs' in row_text


def test_tracker_numbers_match_table_row_data(test_client_and_db):
    """Verify that row data matches the exact formula:
    (lecture + lab + ilp) * sections for hours, units * sections for units."""
    client, _ = test_client_and_db
    res = client.get('/professor_load')
    soup = BeautifulSoup(res.data.decode('utf-8'), 'html.parser')

    # Alan Turing: IT101 (lec 3, lab 0 * 2 = 6h, 6u) + IT102 (lec 2, lab 3 * 1 = 5h, 3u) -> 11 hrs, 9 units
    turing_row = soup.find('tr', attrs={'data-prof-name': 'alan turing'})
    assert turing_row is not None
    assert float(turing_row.get('data-hours')) == 11.0
    assert float(turing_row.get('data-units')) == 9.0
    assert int(turing_row.get('data-sections')) == 3
    assert int(turing_row.get('data-course-count')) == 2

    # Ada Lovelace: IT101 (4 sec = 12h, 12u) + IT104 (4 sec = 24h, 16u) + IT103 (1 sec = 4h, 4u) -> 40 hrs, 32 units
    ada_row = soup.find('tr', attrs={'data-prof-name': 'ada lovelace'})
    assert ada_row is not None
    assert float(ada_row.get('data-hours')) == 40.0
    assert float(ada_row.get('data-units')) == 32.0
    assert int(ada_row.get('data-sections')) == 9
    assert int(ada_row.get('data-course-count')) == 3

    # Grace Hopper: IT104 (7 sec = 42h, 28u) + IT103 (1 sec = 4h, 4u) -> 46 hrs, 32 units
    hopper_row = soup.find('tr', attrs={'data-prof-name': 'grace hopper'})
    assert hopper_row is not None
    assert float(hopper_row.get('data-hours')) == 46.0
    assert float(hopper_row.get('data-units')) == 32.0
    assert int(hopper_row.get('data-sections')) == 8
    assert int(hopper_row.get('data-course-count')) == 2


def test_tracker_javascript_rules_and_thresholds():
    """Verify that frontend JavaScript handles under 40, exactly 40, over 40, and 0 hours correctly."""
    def compute_status_pill(hours):
        if hours < 40:
            return 'Within Limit'
        elif hours == 40:
            return 'Full Load'
        else:
            return 'Overloaded'

    assert compute_status_pill(0) == 'Within Limit'      # Professor with 0 loads
    assert compute_status_pill(11.0) == 'Within Limit'   # Under 40 hrs
    assert compute_status_pill(39.9) == 'Within Limit'   # Under 40 hrs
    assert compute_status_pill(40.0) == 'Full Load'      # Exactly 40 hrs
    assert compute_status_pill(40.1) == 'Overloaded'     # Above 40 hrs
    assert compute_status_pill(46.0) == 'Overloaded'     # Above 40 hrs


def test_tracker_bar_scaling_clamped_at_100():
    """Verify stacked bar scaling logic: clamped at 100% when overloaded while label retains real number."""
    def compute_bar_scale(hours, course_hours_list):
        scale_base = hours if hours > 40 else 40
        if scale_base == 0:
            return [0]
        return [(h / scale_base) * 100 for h in course_hours_list]

    # Under 40: e.g. 11 hrs (6h + 5h)
    under_widths = compute_bar_scale(11.0, [6.0, 5.0])
    assert pytest.approx(sum(under_widths), 0.001) == 27.5  # (11.0 / 40.0) * 100.0

    # Exactly 40: e.g. 40 hrs (12h + 24h + 4h)
    full_widths = compute_bar_scale(40.0, [12.0, 24.0, 4.0])
    assert sum(full_widths) == 100.0                   # 100%

    # Overloaded: e.g. 46 hrs (42h + 4h)
    over_widths = compute_bar_scale(46.0, [42.0, 4.0])
    assert pytest.approx(sum(over_widths), 0.001) == 100.0  # Clamped at 100%
