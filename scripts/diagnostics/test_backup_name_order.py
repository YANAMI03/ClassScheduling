import os, sys
sys.path.insert(0, os.path.abspath('.'))
import json
import professor_load_importer

with open('scripts/professors_backup.json', 'r', encoding='utf-8') as f:
    pb = json.load(f)

prof_name_map = {}
for p in pb['professors']:
    fn = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    prof_name_map[p['prof_id']] = professor_load_importer.normalize_professor_key(fn)

with open('backups/professor_load_backup_latest.json', 'r', encoding='utf-8') as f:
    bdata = json.load(f)

order_map = {}
for idx, r in enumerate(bdata.get('professor_load_rows', [])):
    pid = r.get('prof_id')
    cid = r.get('course_id')
    pkey = prof_name_map.get(pid)
    if cid:
        cid_int = int(cid)
        if pid:
            order_map[(int(pid), cid_int)] = idx
        if pkey:
            order_map[(pkey, cid_int)] = idx

str_keys = [k for k in order_map if isinstance(k[0], str)]
print(f"Total order_map keys: {len(order_map)}, str keys: {len(str_keys)}")
print("Sample str keys:", str_keys[:5])
