import os
import re
import pytest
from bs4 import BeautifulSoup

def test_part1_section_schedule_layout_and_major_filter():
    """Verify Section Schedule template layout matching Professor Schedule."""
    path = os.path.join('templates', 'schedules.html')
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Filter form must be inside card-custom with row g-2 align-items-end
    form = soup.find('form', id='sectionScheduleFilterForm')
    assert form is not None, "Missing #sectionScheduleFilterForm"
    assert 'row' in form.get('class', [])
    assert 'g-2' in form.get('class', [])
    assert 'align-items-end' in form.get('class', [])

    # 2. Card wrapper must have card-custom and p-3 bg-light-subtle
    card = form.parent
    assert 'card-custom' in card.get('class', [])

    # 3. Label must be 'Major', never 'Specialization'
    labels = [l.get_text(strip=True) for l in form.find_all('label')]
    assert 'Major' in labels, "Expected 'Major' label in filter form"
    assert not any('Specialization' in l for l in labels), "Found obsolete 'Specialization' label"

    # 4. Check for 'All Majors' option
    major_select = form.find('select', {'name': 'major'})
    assert major_select is not None, "Missing major select dropdown"
    options = [o.get_text(strip=True) for o in major_select.find_all('option')]
    assert 'All Majors' in options

    # 5. Page header must have flex layout with align-items-center
    page_header = soup.find('div', class_='page-header')
    assert page_header is not None
    assert 'justify-content-between' in page_header.get('class', [])

    # 6. Section cards should have aligned header
    section_headers = soup.find_all('div', class_='section-card__header')
    for sh in section_headers:
        assert 'justify-content-between' in sh.get('class', [])

    # 7. Modal must have Major instead of Specialization
    modal = soup.find('div', id='editScheduleModal')
    if modal:
        modal_labels = [l.get_text(strip=True) for l in modal.find_all('label')]
        assert not any('Specialization' in l for l in modal_labels), "Found 'Specialization' in edit modal"


def test_part2_professor_load_import_modal_elements():
    """Verify Import Modal overlay, timer, buttons, and loading helpers."""
    path = os.path.join('templates', 'professor_load.html')
    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')

    # 1. Modal overlay exists inside modal-content
    overlay = soup.find('div', id='import-modal-overlay')
    assert overlay is not None, "Missing #import-modal-overlay"
    assert 'import-modal-overlay' in overlay.get('class', [])
    assert 'd-none' in overlay.get('class', [])

    # 2. Timer elements exist inside overlay
    timer = soup.find('div', id='import-overlay-timer')
    assert timer is not None, "Missing #import-overlay-timer"
    seconds = soup.find('span', id='import-overlay-seconds')
    assert seconds is not None, "Missing #import-overlay-seconds"

    # 3. Sample template download has button id
    template_btn = soup.find('a', id='btn-download-sample-template')
    assert template_btn is not None, "Missing #btn-download-sample-template"

    # 4. Success counts container exists
    success_counts = soup.find('div', id='import-success-counts')
    assert success_counts is not None, "Missing #import-success-counts"

    # 5. JS contains overlay and button loading wiring
    assert 'showImportOverlay' in html
    assert 'hideImportOverlay' in html
    assert 'isImportPreviewing' in html
    assert 'isImportConfirming' in html
    assert 'pageshow' in html


def test_part3_delete_inventory_and_shared_mechanism():
    """Verify all delete buttons are marked with data-action='delete' and index.js handles them."""
    # Check static/index.js
    with open('static/index.js', 'r', encoding='utf-8') as f:
        js = f.read()

    assert 'executeUniversalDelete' in js
    assert 'updateCountBadges' in js
    assert 'row-deleting-fade' in js
    assert 'row-delete-shake' in js
    assert 'row-disabled-mid-delete' in js
    assert 'pageshow' in js
    assert 'data-action=\'delete\'' in js or 'data-action="delete"' in js

    # Check static/style.css
    with open('static/style.css', 'r', encoding='utf-8') as f:
        css = f.read()

    assert '.row-deleting-fade' in css
    assert '.row-delete-shake' in css
    assert '.row-disabled-mid-delete' in css
    assert 'prefers-reduced-motion' in css
    assert '.import-modal-overlay' in css

    # Verify inventory templates have data-action="delete"
    inventory = {
        'templates/courses.html': '/delete_course/',
        'templates/room.html': '/delete_room/',
        'templates/timeslot.html': '/delete_timeslot/',
        'templates/programs.html': '/delete_program/',
        'templates/users.html': '/delete_user/',
        'templates/schedule_archive.html': '/delete_schedule_archive/',
        'templates/schedules.html': 'delete_section_schedule',
        'templates/generated_schedule.html': 'delete_section_schedule',
        'templates/base.html': '/admin/delete_requests/',
    }

    for file_path, endpoint_marker in inventory.items():
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert endpoint_marker in content, f"Marker {endpoint_marker} not found in {file_path}"
        assert 'data-action="delete"' in content, f"Missing data-action='delete' in {file_path}"
