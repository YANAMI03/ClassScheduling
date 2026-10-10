import os
import glob
import re
from bs4 import BeautifulSoup


def test_button_coverage_zero_leftovers():
    templates = sorted(glob.glob(os.path.join(os.path.dirname(__file__), '..', 'templates', '*.html')))
    assert len(templates) >= 20, "Expected at least 20 templates"

    uncovered = []
    total = 0

    for tpath in templates:
        tname = os.path.basename(tpath)
        with open(tpath, 'r', encoding='utf-8') as f:
            html = f.read()
        soup = BeautifulSoup(html, 'html.parser')

        elements = []
        for btn in soup.find_all(['button', 'input']):
            if btn.name == 'input' and btn.get('type') not in ['submit', 'button']:
                continue
            elements.append(btn)
        for a in soup.find_all('a'):
            cls = ' '.join(a.get('class', []))
            data_action = a.get('data-action', '')
            if 'btn' in cls or 'button' in cls or data_action:
                elements.append(a)

        for el in elements:
            total += 1
            tag = el.name
            el_type = el.get('type', '')
            el_id = el.get('id', '')
            cls = ' '.join(el.get('class', []))
            text = ' '.join(el.get_text().split())[:35]
            action = el.get('data-action', '')
            bs_toggle = el.get('data-bs-toggle', '')
            bs_dismiss = el.get('data-bs-dismiss', '')
            href = el.get('href', '')
            onclick = el.get('onclick', '')
            has_skip = el.has_attr('data-loader-skip')
            form = el.find_parent('form')
            has_loading_attr = el.has_attr('data-loading-text')

            # Class B: Purely client-side UI
            is_class_b = bool(
                bs_dismiss or
                bs_toggle in ['collapse', 'pill', 'tab', 'dropdown', 'offcanvas', 'modal'] or
                el_id in ['toggle-password', 'toggle-delete-password', 'btn-browse-file', 'btn-remove-selected-file', 'btn-import-reupload', 'sidebar-toggle', 'delete-all-btn', 'btnSortAsc', 'btnSortDesc', 'btn-clear-selection', 'btnClearFilters', 'btn-clear-search'] or
                text.strip().lower() in ['cancel', 'close', '×'] or
                'btn-close' in cls or
                'btn-press' in cls or
                'btn-clear-filters' in cls or
                'sem-filter-btn' in cls or
                'year-filter-btn' in cls or
                'professor-option' in cls or
                'btn-remove-breakdown' in cls or
                'edit-preview-entry-btn' in cls or
                'edit-schedule-btn' in cls or
                'restore-batch-btn' in cls or
                'btn-notif-bell' in cls or
                (tag == 'button' and onclick and ('openCreateModal' in onclick or 'openEditModal' in onclick or 'openEditWorkingHoursModal' in onclick or 'click()' in onclick))
            )

            # Class C: Live / silent controls
            is_class_c = bool(
                'btn-step' in cls or
                'step-btn' in cls or
                'breakdown-sec-minus' in cls or
                'breakdown-sec-plus' in cls or
                'stat-pill' in cls or
                (has_skip and not is_class_b)
            )

            if is_class_b or is_class_c:
                continue

            # Class A
            is_covered = (
                action == 'delete' or
                (form and (el_type == 'submit' or (tag == 'button' and el_type != 'button'))) or
                (tag == 'a' and href and not href.startswith('#') and not href.startswith('javascript:')) or
                has_loading_attr or
                el_id in ['save-schedule-btn', 'save-entry-btn', 'save-preview-entry-btn', 'modalEditBtn', 'restoreSubmitBtn', 'confirm-delete-all', 'btn-download-error-report', 'btn-download-sample-template', 'btn-confirm-import', 'btn-import-preview-action', 'btn-import-finish-reload', 'btnSubmitCreateUser', 'btnSubmitSaveUser', 'globalConfirmSubmitBtn'] or
                el_id == 'modalProfScheduleLink' or
                (onclick and any(k in onclick for k in ['createUser', 'saveUser', 'searchUsers']))
            )

            if not is_covered:
                uncovered.append(f"[{tname}] <{tag}> id='{el_id}' text='{text}'")

    assert len(uncovered) == 0, f"Found {len(uncovered)} uncovered buttons: {uncovered}"


def test_shared_infrastructure_integrity():
    base_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'base.html')
    index_js_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'index.js')
    style_css_path = os.path.join(os.path.dirname(__file__), '..', 'static', 'style.css')
    login_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'login.html')

    with open(base_path, 'r', encoding='utf-8') as f:
        base_html = f.read()
    with open(index_js_path, 'r', encoding='utf-8') as f:
        index_js = f.read()
    with open(style_css_path, 'r', encoding='utf-8') as f:
        style_css = f.read()
    with open(login_path, 'r', encoding='utf-8') as f:
        login_html = f.read()

    # 1. Base HTML has helper and gerund derive
    assert 'window.setButtonLoading' in base_html
    assert 'window.deriveLoadingText' in base_html
    assert 'minWidth' in base_html and 'minHeight' in base_html
    assert 'sibling-locked-mid-load' in base_html

    # 2. Index JS has helper and universal delegation
    assert 'window.setButtonLoading' in index_js
    assert 'window.deriveLoadingText' in index_js
    assert '__buttonDelegationBound' in index_js
    assert 'sibling-locked-mid-load' in index_js

    # 3. Style CSS has spinner, icon-only modifier, and Class B active press feedback
    assert '.btn-loading-spinner' in style_css
    assert '.btn-loading-spinner--icon-only' in style_css
    assert '.btn:active:not(:disabled):not(.btn-loading)' in style_css
    assert 'transform: scale(0.97)' in style_css
    assert 'sibling-locked-mid-load' in style_css
    assert 'prefers-reduced-motion: reduce' in style_css

    # 4. Login includes index.js
    assert 'static/index.js' in login_html or 'filename=\'index.js\'' in login_html or 'filename="index.js"' in login_html


def test_legacy_inline_spinners_removed():
    sched_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'schedules.html')
    gen_sched_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'generated_schedule.html')

    with open(sched_path, 'r', encoding='utf-8') as f:
        sched_html = f.read()
    with open(gen_sched_path, 'r', encoding='utf-8') as f:
        gen_sched_html = f.read()

    # saveScheduleBtn and saveEntryBtn must NOT use spinner-border hack
    assert 'saveScheduleBtn.innerHTML = \'<span class="spinner-border' not in sched_html
    assert 'saveEntryBtn.innerHTML = \'<span class="spinner-border' not in gen_sched_html
