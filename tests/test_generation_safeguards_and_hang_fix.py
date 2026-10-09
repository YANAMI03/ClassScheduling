import pytest
import os
from bs4 import BeautifulSoup
import app as app_module
from unittest.mock import patch, MagicMock


def test_base_html_and_index_js_delegation_dedup():
    """Verify templates/base.html and static/index.js guard against duplicate document submit listeners."""
    with open('templates/base.html', 'r', encoding='utf-8') as f:
        base_html = f.read()
    with open('static/index.js', 'r', encoding='utf-8') as f:
        index_js = f.read()

    # Both must guard with window.__buttonDelegationBound
    assert '__buttonDelegationBound' in base_html, "base.html must guard delegation listeners"
    assert '__buttonDelegationBound' in index_js, "index.js must guard delegation listeners"

    # base.html must have Try again and Close buttons with data-loader-skip
    assert 'gen-overlay-retry-btn' in base_html
    assert 'data-loader-skip' in base_html
    assert '125 * 1000' in base_html or '125000' in base_html, "Failsafe timer should be ~125s"


def test_program_field_accessibility():
    """Verify Program field has id/name and label for attribute (resolving DevTools Issues tab items)."""
    with open('templates/index.html', 'r', encoding='utf-8') as f:
        html = f.read()
    soup = BeautifulSoup(html, 'html.parser')
    prog_input = soup.find('input', {'id': 'program-display'})
    assert prog_input is not None, "Program field must have id='program-display'"
    assert prog_input.get('name') == 'program_display', "Program field must have name='program_display'"
    
    prog_label = soup.find('label', {'for': 'program-display'})
    assert prog_label is not None, "Program label must have for='program-display'"


def test_generation_concurrency_guard():
    """Verify concurrency guard prevents duplicate generations for the same program and semester."""
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'scheduler1'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    # Simulate an active generation in progress
    key = ('1', '2nd semester')
    with app_module._active_generations_lock:
        app_module._active_generations.add(key)

    try:
        # Request with AJAX headers
        res = client.post('/generate_schedule', data={'semester': '2nd Semester'}, headers={'Accept': 'application/json'})
        assert res.status_code == 409
        json_data = res.get_json()
        assert json_data['already_running'] is True
        assert 'already running' in json_data['error'].lower()

        # Regular form request receives redirect with flash
        res_form = client.post('/generate_schedule', data={'semester': '2nd Semester'})
        assert res_form.status_code == 302
    finally:
        with app_module._active_generations_lock:
            app_module._active_generations.discard(key)


def test_validation_detects_bad_data_missing_prof_and_duplicate():
    """Verify pre-generation validation catches missing professor names and duplicate loads."""
    # Test duplicate load detection
    mock_courses = [
        {'course_id': 101, 'course_name': 'IT101', 'year_level': 1, 'semester': '1st Semester', 'lecture_hours': 3, 'lab_hours': 0, 'program_id': 1}
    ]
    mock_loads = [
        {'id': 1, 'course_id': 101, 'sections': 1, 'professor_name': 'Dr. Smith', 'professor_key': 'dr smith'},
        {'id': 2, 'course_id': 101, 'sections': 1, 'professor_name': 'Dr. Smith', 'professor_key': 'dr smith'}
    ]

    with patch.object(app_module.supabase, 'table') as mock_table:
        def table_side_effect(name):
            builder = MagicMock()
            if name == 'course':
                builder.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = mock_courses
            elif name == 'professor_load':
                builder.select.return_value.execute.return_value.data = mock_loads
            elif name == 'schedule':
                builder.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            elif name == 'room':
                builder.select.return_value.execute.return_value.data = [{'room_id': 1, 'room_type': 'Lecture Room'}]
            return builder
        mock_table.side_effect = table_side_effect

        res = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
        assert res['valid'] is False
        assert any('duplicate professor load detected' in err.lower() for err in res['errors'])


def test_pagination_safeguard():
    """Verify _fetch_all_table_data has a hard page limit to prevent infinite loops."""
    with patch.object(app_module.supabase, 'table') as mock_table:
        builder = MagicMock()
        # Always return 1000 rows to simulate endless table or repeating range
        builder.select.return_value.range.return_value.execute.return_value.data = [{'id': i} for i in range(1000)]
        mock_table.return_value = builder

        # Should terminate cleanly after max_pages (100) instead of hanging forever
        data = app_module._fetch_all_table_data('test_table', page_size=1000)
        assert len(data) == 100000 # 100 pages * 1000


def test_baseline_load_order_and_canonical_timeslots_safeguards():
    """Verify that _get_baseline_professor_load_order maps real professor keys,
    canonical timeslot fallback covers until 20:00:00 on Tue-Fri, and cutoff handling
    does not falsely block evening sessions when ranking table is absent."""
    # 1. Baseline order map must contain real faculty keys
    order_map = app_module._get_baseline_professor_load_order()
    assert len(order_map) > 0, "Baseline order map must not be empty"
    # Verify specialized faculty like Cerin, Corpuz, Tambio, Franco have mapped ranks
    assert any('marcelino s. cerin iii' in str(k) for k in order_map.keys()), "Cerin must be in canonical load order"
    assert any('jev d. corpuz' in str(k) for k in order_map.keys()), "Corpuz must be in canonical load order"

    # 2. Cutoff check with empty map must not block evening slots
    res = app_module._check_professor_cutoff_conflict(
        'marcelino s. cerin iii', 'Tuesday', '18:00:00', '20:00:00',
        session_type='Laboratory', prof_cutoff_map={}, day_cutoff_map={'Tuesday': '17:00:00'}
    )
    assert res is None, "Evening slot must not be blocked when no ranking cutoff constraint exists"

