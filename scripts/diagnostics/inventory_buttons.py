import os
import glob
import sys
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')
templates = sorted(glob.glob('templates/*.html'))

all_elements = []

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
        if 'btn' in cls or 'button' in cls:
            elements.append(a)
            
    print(f"\n=================== {tname} ({len(elements)} elements) ===================")
    for idx, el in enumerate(elements, 1):
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
        form = el.find_parent('form')
        form_action = form.get('action', '') if form else ''
        
        # Classification
        # Class B: purely client-side
        # Modal open/close, tab pills, collapse, password toggle, cancel, remove selected file
        if (bs_dismiss or 
            bs_toggle in ['collapse', 'pill', 'tab', 'dropdown', 'offcanvas'] or 
            (bs_toggle == 'modal' and not href and not form) or
            el_id in ['toggle-password', 'toggle-delete-password', 'btn-browse-file', 'btn-remove-selected-file', 'btn-import-reupload'] or
            text.strip().lower() in ['cancel', 'close', '×'] or
            cls in ['btn-close', 'btn-close btn-close-white']):
            category = 'B (Client-side / Press Feedback)'
            how = 'Client-side UI action (modal/tab/collapse/dismiss)'
        # Class C: live/silent controls
        elif 'btn-step' in cls or 'step-btn' in cls or 'stat-pill' in cls:
            category = 'C (Live/Silent Control)'
            how = 'Live stepper / counter control'
        # Class A: Waits for something (server request / submit / navigation / download / delete)
        else:
            category = 'A (Loading Animation Required)'
            if action == 'delete':
                how = 'Shared Delete System (AJAX / modal confirm)'
            elif form:
                how = f"Form Submit ({form.get('method', 'GET').upper()} to {form_action or 'current URL'})"
            elif href and not href.startswith('#'):
                if 'export' in href or 'download' in href or 'template' in href or 'backup' in href:
                    how = f'File Download Link ({href})'
                else:
                    how = f'Page Navigation Link ({href})'
            elif onclick or el_id:
                how = f'JS Fetch / Async Action ({onclick or el_id})'
            else:
                how = 'Server / Action Button'
                
        all_elements.append({
            'template': tname,
            'index': idx,
            'tag': tag,
            'category': category,
            'id': el_id,
            'class': cls,
            'text': text,
            'href': href,
            'onclick': onclick,
            'how': how
        })
        print(f"[{category[0]}] #{idx:02d} <{tag}> id='{el_id}' text='{text}' -> {how}")

print(f"\nTotal elements scanned: {len(all_elements)}")
class_a = [e for e in all_elements if e['category'].startswith('A')]
class_b = [e for e in all_elements if e['category'].startswith('B')]
class_c = [e for e in all_elements if e['category'].startswith('C')]
print(f"Summary: Class A = {len(class_a)}, Class B = {len(class_b)}, Class C = {len(class_c)}")
