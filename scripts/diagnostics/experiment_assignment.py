import json
import dotenv
import os
import sys

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Load backup
with open('backups/professor_load_backup_20261003_191218.json') as f:
    backup_data = json.load(f)

backup_sched = [s for s in backup_data.get('full_schedule_rows', []) if not s.get('archive')]
backup_pls = {p['id']: p for p in backup_data.get('professor_load_rows', [])}

backup_section_prof = {}
for s in backup_sched:
    sec = s.get('section')
    pl_id = s.get('professor_load_id')
    pl = backup_pls.get(pl_id)
    if pl:
        cid = pl.get('course_id')
        pid = pl.get('prof_id')
        backup_section_prof[(sec, cid)] = pid

# Let's test with Flask test client by temporarily intercepting section_course_assignment in generate_schedule!
# Or let's see: in app.py, where section_course_assignment is built:
# What if we hook into app.py to see if using the backup assignments yields 0 unscheduled loads?

from unittest.mock import patch

orig_generate = app.generate_schedule

# Let's inspect if we can reorder loads or assign matching the backup
print("Testing with current DB and Flask test_client...")
test_client = app.app.test_client()
with test_client.session_transaction() as sess:
    sess['user_id'] = 1
    sess['role'] = 'scheduler'
    sess['program'] = 'BSIT'
    sess['program_id'] = 1
    sess['access_token'] = auth_res.session.access_token

# Let's patch the section_course_assignment logic inside generate_schedule or see what happens:
# Let's write an experiment that tests different assignments:
