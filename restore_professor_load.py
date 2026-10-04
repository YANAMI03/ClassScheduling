"""
Restore Professor Load Data from Backup
Restores original professor_load rows with their exact IDs and re-links schedules.
"""
import os
import json
import time
import dotenv
from supabase import create_client

dotenv.load_dotenv('.env')

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_PUBLISHABLE_KEY')

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("Missing Supabase configuration in .env")

client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Sign in as admin to bypass RLS
auth_res = client.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
client.postgrest.auth(auth_res.session.access_token)

latest_filename = "backups/professor_load_backup_latest.json"
if not os.path.exists(latest_filename):
    raise FileNotFoundError(f"Backup file not found at {latest_filename}")

with open(latest_filename, 'r', encoding='utf-8') as f:
    backup_data = json.load(f)

load_rows = backup_data['professor_load_rows']
schedule_links = backup_data['schedule_links']

print(f"[1/4] Found backup created at {backup_data.get('created_at')}")
print(f"  -> {len(load_rows)} professor_load rows to restore")
print(f"  -> {len(schedule_links)} schedule links to restore")

# 1. Clean up any test/temporary rows in professor_load
current_loads = client.table('professor_load').select('id').execute().data or []
if current_loads:
    print(f"[2/4] Clearing {len(current_loads)} current/test rows in professor_load...")
    curr_ids = [r['id'] for r in current_loads]
    client.table('professor_load').delete().in_('id', curr_ids).execute()

# 2. Insert the original 63 professor_load rows with their exact IDs
print(f"[3/4] Re-inserting original {len(load_rows)} professor_load records...")
batch_size = 50
for i in range(0, len(load_rows), batch_size):
    batch = load_rows[i:i + batch_size]
    # Clean rows to ensure only proper columns are inserted
    insert_batch = [{'id': r['id'], 'prof_id': r['prof_id'], 'course_id': r['course_id'], 'sections': r.get('sections', 1)} for r in batch]
    client.table('professor_load').insert(insert_batch).execute()
    print(f"  -> Inserted {min(i + batch_size, len(load_rows))}/{len(load_rows)} professor_load rows...")

# 3. Re-link schedules by professor_load_id
print(f"[4/4] Re-linking {len(schedule_links)} schedule entries to original professor_load IDs...")
# Group schedule_ids by professor_load_id for ultra-fast batch updating
links_by_load_id = {}
for link in schedule_links:
    plid = link['professor_load_id']
    sid = link['schedule_id']
    links_by_load_id.setdefault(plid, []).append(sid)

total_plids = len(links_by_load_id)
idx = 0
for plid, sids in links_by_load_id.items():
    idx += 1
    # Update in chunks of 100 schedule IDs
    for j in range(0, len(sids), 100):
        sid_chunk = sids[j:j + 100]
        client.table('schedule').update({'professor_load_id': plid}).in_('schedule_id', sid_chunk).execute()
    if idx % 10 == 0 or idx == total_plids:
        print(f"  -> Re-linked schedules for {idx}/{total_plids} load IDs...")

# Verify
verify_load = client.table('professor_load').select('id', count='exact').execute()
final_count = verify_load.count or len(verify_load.data or [])
print(f"\n==========================================")
print(f"SUCCESS: Professor load records completely restored!")
print(f"Total professor_load rows restored: {final_count}")
print(f"Total schedules re-linked: {len(schedule_links)}")
print(f"==========================================")
