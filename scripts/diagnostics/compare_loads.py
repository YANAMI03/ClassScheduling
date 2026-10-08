import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Current DB
pc_res = app.supabase.table('professor_load').select('id, prof_id, course_id, sections, professor(first_name, last_name)').eq('course_id', 51).execute()
print('=== CURRENT DB PROFESSOR_LOAD FOR COURSE 51 ===')
for r in pc_res.data:
    p = r.get('professor') or {}
    print("ID:", r['id'], "Prof:", p.get('first_name'), p.get('last_name'), "prof_id:", r['prof_id'], "sections:", r['sections'])

with open('backups/professor_load_backup_20261003_191218.json') as f:
    data = json.load(f)
profs = app.supabase.table('professor').select('prof_id, first_name, last_name').execute().data or []
p_map = {p['prof_id']: p.get('first_name', '') + ' ' + p.get('last_name', '') for p in profs}
print('\n=== BACKUP PROFESSOR_LOAD FOR COURSE 51 ===')
for r in data.get('professor_load_rows', []):
    if r.get('course_id') == 51:
        print("ID:", r['id'], "Prof:", p_map.get(r['prof_id']), "prof_id:", r['prof_id'], "sections:", r['sections'])
