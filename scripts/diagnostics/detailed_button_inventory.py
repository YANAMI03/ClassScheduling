import os
import glob
import json
import sys
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

templates = sorted(glob.glob('templates/*.html'))

all_elements = []
per_page_summary = {}

for tpath in templates:
    tname = os.path.basename(tpath)
    with open(tpath, 'r', encoding='utf-8') as f:
        html = f.read()
    soup = BeautifulSoup(html, 'html.parser')
    
    elements = []
    # Buttons and submit/button inputs
    for btn in soup.find_all(['button', 'input']):
        if btn.name == 'input' and btn.get('type') not in ['submit', 'button']:
            continue
        elements.append(btn)
    # <a> styled as button or having action
    for a in soup.find_all('a'):
        cls = ' '.join(a.get('class', []))
        data_action = a.get('data-action', '')
        if 'btn' in cls or 'button' in cls or data_action:
            elements.append(a)
            
    page_items = []
    for idx, el in enumerate(elements, 1):
        tag = el.name
        el_type = el.get('type', '')
        el_id = el.get('id', '')
        cls = ' '.join(el.get('class', []))
        raw_text = ' '.join(el.get_text().split())
        text = raw_text[:40] if raw_text else ('<icon-only>' if not el_id else f'<{el_id}>')
        action = el.get('data-action', '')
        bs_toggle = el.get('data-bs-toggle', '')
        bs_dismiss = el.get('data-bs-dismiss', '')
        href = el.get('href', '')
        onclick = el.get('onclick', '')
        data_loading_text = el.get('data-loading-text', '')
        data_loader_skip = el.has_attr('data-loader-skip')
        form = el.find_parent('form')
        form_action = form.get('action', '') if form else ''
        form_method = form.get('method', 'GET').upper() if form else ''
        
        # Classification:
        # Class B: Purely client-side UI
        if (bs_dismiss or 
            bs_toggle in ['collapse', 'pill', 'tab', 'dropdown', 'offcanvas'] or 
            (bs_toggle == 'modal' and not href and not form and not action) or
            el_id in ['toggle-password', 'toggle-delete-password', 'btn-browse-file', 'btn-remove-selected-file', 'btn-import-reupload', 'sidebar-toggle'] or
            raw_text.strip().lower() in ['cancel', 'close', '×'] or
            'btn-close' in cls or
            'btn-press' in cls):
            category = 'Class B (Client-side Press)'
            mechanism = 'Modal close/cancel/toggle, tab, pill, collapse, password reveal'
        elif 'btn-step' in cls or 'step-btn' in cls or 'stat-pill' in cls:
            category = 'Class C (Live / Silent)'
            mechanism = 'Live numeric stepper or filter pill'
        else:
            category = 'Class A (Waits / Loading Animation)'
            if action == 'delete':
                mechanism = f'Shared Delete System (AJAX {href or form_action})'
            elif form and el_type == 'submit':
                mechanism = f'Form Submit ({form_method} to {form_action or "current URL"})'
            elif href and not href.startswith('#') and not href.startswith('javascript:'):
                if any(k in href for k in ['export', 'download', 'template', 'backup']):
                    mechanism = f'Download Link ({href})'
                else:
                    mechanism = f'Navigation Link ({href})'
            elif onclick or el_id:
                mechanism = f'JS Fetch / Action ({onclick or el_id})'
            else:
                mechanism = f'Server / Form Action'
                
        item = {
            'template': tname,
            'idx': idx,
            'tag': tag,
            'id': el_id,
            'type': el_type,
            'class': cls,
            'text': text,
            'category': category,
            'mechanism': mechanism,
            'href': href,
            'form_action': form_action,
            'form_method': form_method,
            'loading_text': data_loading_text,
            'has_skip': data_loader_skip
        }
        page_items.append(item)
        all_elements.append(item)
        
    per_page_summary[tname] = page_items

with open('scripts/diagnostics/inventory_data.json', 'w', encoding='utf-8') as f:
    json.dump(all_elements, f, indent=2, ensure_ascii=False)

# Print clean report
total_a = len([e for e in all_elements if e['category'].startswith('Class A')])
total_b = len([e for e in all_elements if e['category'].startswith('Class B')])
total_c = len([e for e in all_elements if e['category'].startswith('Class C')])

print(f"Total Scanned: {len(all_elements)}")
print(f"Class A (Loading Animation): {total_a}")
print(f"Class B (Client-side Press): {total_b}")
print(f"Class C (Live / Silent): {total_c}")

for tname, items in per_page_summary.items():
    ca = len([e for e in items if e['category'].startswith('Class A')])
    cb = len([e for e in items if e['category'].startswith('Class B')])
    cc = len([e for e in items if e['category'].startswith('Class C')])
    print(f"\n--- {tname} (Total: {len(items)}, Class A: {ca}, Class B: {cb}, Class C: {cc}) ---")
    for e in items:
        code = 'A' if e['category'].startswith('Class A') else ('B' if e['category'].startswith('Class B') else 'C')
        id_str = f" id='{e['id']}'" if e['id'] else ""
        print(f"  [{code}] <{e['tag']}{id_str}> \"{e['text']}\" -> {e['mechanism']}")
