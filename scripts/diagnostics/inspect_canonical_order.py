import json
import os
import sys

with open('backups/professor_load_backup_latest.json', 'r', encoding='utf-8') as f:
    latest = json.load(f)

with open('backups/professor_load_repair_backup_20261004_023143.json', 'r', encoding='utf-8') as f:
    repair = json.load(f)

sys.path.insert(0, '.')
import dotenv
dotenv.load_dotenv('.env')
import app
auth = app.supabase.auth.sign_in_with_password({'email':'admin@example.com','password':'password123'})
app.supabase.postgrest.auth(auth.session.access_token)
db_loads = app.supabase.table('professor_load').select('*').execute().data or []
db_by_id = {r['id']: r for r in db_loads}

prof_id_to_name = {1: 'Emilsa T. Bantug', 2: 'Ronaldin V. Bauat'}
for r in repair['rows']:
    lid = r['id']
    pid = r['prof_id']
    if lid in db_by_id:
        prof_id_to_name[pid] = db_by_id[lid]['professor_name']

print(f"Total backup load rows: {len(latest['professor_load_rows'])}")
canonical_order = []
for idx, r in enumerate(latest['professor_load_rows']):
    pid = r.get('prof_id')
    cid = r.get('course_id')
    pname = prof_id_to_name.get(pid, f'Unknown_{pid}')
    pkey = app.professor_load_importer.normalize_professor_key(pname)
    canonical_order.append({
        'index': idx,
        'prof_id': pid,
        'course_id': cid,
        'professor_name': pname,
        'professor_key': pkey,
        'sections': r.get('sections')
    })
    print(f"{idx:2d}: (cid={cid:2d}, sec={r.get('sections'):2d}) -> {pname} (key='{pkey}')")

with open('scripts/diagnostics/canonical_load_order.json', 'w', encoding='utf-8') as out_f:
    json.dump(canonical_order, out_f, indent=2)

print("\nSaved to scripts/diagnostics/canonical_load_order.json")
