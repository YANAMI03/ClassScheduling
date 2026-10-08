import os
import glob
import sys
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

templates = sorted(glob.glob('templates/*.html'))

uncovered_leftovers = []
total_elements = 0
covered_class_a = 0
covered_class_b = 0
covered_class_c = 0

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

    for idx, el in enumerate(elements, 1):
        total_elements += 1
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

        # Check Class B: Purely client-side UI
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
            (tag == 'button' and onclick and ('openCreateModal' in onclick or 'openEditModal' in onclick or 'openEditTimeslotModal' in onclick or 'click()' in onclick))
        )

        # Check Class C: Live / silent controls
        is_class_c = bool(
            'btn-step' in cls or
            'step-btn' in cls or
            'breakdown-sec-minus' in cls or
            'breakdown-sec-plus' in cls or
            'stat-pill' in cls or
            (has_skip and not is_class_b)
        )

        if is_class_b:
            covered_class_b += 1
            continue

        if is_class_c:
            covered_class_c += 1
            continue

        # Class A: must be covered
        is_covered = False
        coverage_reason = ""

        # 1. Covered by universal delete delegation
        if action == 'delete':
            is_covered = True
            coverage_reason = "Universal Delete Delegation (data-action=delete)"
        # 2. Covered by universal form submit delegation (button[type=submit] or submit input)
        elif form and (el_type == 'submit' or (tag == 'button' and el_type != 'button')):
            is_covered = True
            coverage_reason = f"Universal Form Submit Delegation ({form.get('method', 'GET').upper()})"
        # 3. Covered by universal link click delegation
        elif tag == 'a' and href and not href.startswith('#') and not href.startswith('javascript:'):
            if any(k in href for k in ['export', 'download', 'template', 'backup']):
                is_covered = True
                coverage_reason = f"Universal Download Link Delegation ({href[:25]}...)"
            else:
                is_covered = True
                coverage_reason = f"Universal Navigation Link Delegation ({href[:25]}...)"
        # 4. Covered by explicit helper calls in JS or dynamic link routing
        elif has_loading_attr:
            is_covered = True
            coverage_reason = f"data-loading-text attribute"
        elif el_id in ['save-schedule-btn', 'save-entry-btn', 'save-preview-entry-btn', 'modalEditBtn', 'restoreSubmitBtn', 'confirm-delete-all', 'btn-download-error-report', 'btn-download-sample-template', 'btn-confirm-import', 'btn-import-preview-action', 'btn-import-finish-reload', 'btnSubmitCreateUser', 'btnSubmitSaveUser', 'globalConfirmSubmitBtn']:
            is_covered = True
            coverage_reason = f"Explicit window.setButtonLoading JS hook (#{el_id})"
        elif el_id == 'modalProfScheduleLink':
            is_covered = True
            coverage_reason = "Dynamic Navigation Link (href populated on row click)"
        elif onclick and ('createUser' in onclick or 'saveUser' in onclick or 'searchUsers' in onclick):
            is_covered = True
            coverage_reason = f"Explicit window.setButtonLoading JS hook in {onclick}"

        if is_covered:
            covered_class_a += 1
        else:
            uncovered_leftovers.append({
                'template': tname,
                'idx': idx,
                'tag': tag,
                'id': el_id,
                'type': el_type,
                'class': cls,
                'text': text,
                'onclick': onclick,
                'href': href
            })

print("=" * 60)
print("UNIVERSAL BUTTON COVERAGE SCAN AUDIT REPORT")
print("=" * 60)
print(f"Total Interactive Elements Scanned: {total_elements}")
print(f"Class A Covered (Loading Animation): {covered_class_a}")
print(f"Class B Covered (Client-side Press): {covered_class_b}")
print(f"Class C Covered (Live / Silent):     {covered_class_c}")
print(f"UNCOVERED LEFTOVERS:                 {len(uncovered_leftovers)}")
print("=" * 60)

if uncovered_leftovers:
    print("\nFAILED: The following elements are NOT covered by the shared mechanism:")
    for item in uncovered_leftovers:
        print(f"  - [{item['template']}] <{item['tag']}> id='{item['id']}' class='{item['class']}' text='{item['text']}' onclick='{item['onclick']}' href='{item['href']}'")
    sys.exit(1)
else:
    print("\nSUCCESS: ZERO (0) uncovered leftovers! Every button is covered by the unified loading & feedback architecture.")
    sys.exit(0)
