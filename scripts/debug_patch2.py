import sys

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if 'p = _rel(row, \'professor\')' in l:
        lines.insert(i+1, '''
            print(f"DEBUG row={row}, pid={pid}")
            if not p and pid:
                print(f"DEBUG not p and pid. all_profs={all_profs_res.data}")
                for ap in (all_profs_res.data or []):
                    if ap.get('prof_id') == pid:
                        p = ap
                        print(f"DEBUG found ap={ap}")
                        break
''')
        break

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
