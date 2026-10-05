"""
Backup and Clear Professor Load Data
Allows user to test the import feature from a clean slate.
Safely decouples schedule foreign keys to prevent CASCADE DELETE of schedules.
"""
import os
import json
import time
from datetime import datetime
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

print("[1/5] Fetching current professor_load rows...")
load_res = client.table('professor_load').select('*').execute()
load_rows = load_res.data or []
print(f"  -> Found {len(load_rows)} professor_load rows.")

if not load_rows:
    print("No professor_load rows to delete. Table is already empty.")
    exit(0)

load_ids = [r['id'] for r in load_rows]

print("[2/5] Fetching schedule records linked to professor_load...")
all_scheds = []
start = 0
page_size = 1000
while True:
    res = client.table('schedule').select('*').range(start, start + page_size - 1).execute()
    data = res.data or []
    all_scheds.extend(data)
    if len(data) < page_size:
        break
    start += page_size

linked_scheds = [s for s in all_scheds if s.get('professor_load_id') in load_ids]
print(f"  -> Found {len(linked_scheds)} schedule rows referencing these professor_load IDs.")

# 3. Create backup file
os.makedirs('backups', exist_ok=True)
timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
backup_filename = f"backups/professor_load_backup_{timestamp_str}.json"
latest_filename = "backups/professor_load_backup_latest.json"

backup_payload = {
    'created_at': datetime.now().isoformat(),
    'professor_load_count': len(load_rows),
    'professor_load_rows': load_rows,
    'schedule_links_count': len(linked_scheds),
    'schedule_links': [{'schedule_id': s['schedule_id'], 'professor_load_id': s['professor_load_id']} for s in linked_scheds],
    'full_schedule_rows': linked_scheds
}

print(f"[3/5] Saving backup to {backup_filename} and {latest_filename}...")
with open(backup_filename, 'w', encoding='utf-8') as f:
    json.dump(backup_payload, f, indent=2)

with open(latest_filename, 'w', encoding='utf-8') as f:
    json.dump(backup_payload, f, indent=2)

print(f"  -> Successfully saved backup ({len(load_rows)} professor_load rows, {len(linked_scheds)} schedule links).")

# 4. Decouple schedule.professor_load_id -> NULL to avoid CASCADE DELETE
print("[4/5] Temporarily setting schedule.professor_load_id = NULL to prevent CASCADE DELETE...")
batch_size = 100
for i in range(0, len(linked_scheds), batch_size):
    batch = linked_scheds[i:i + batch_size]
    batch_ids = [s['schedule_id'] for s in batch]
    client.table('schedule').update({'professor_load_id': None}).in_('schedule_id', batch_ids).execute()
    print(f"  -> Decoupled {min(i + batch_size, len(linked_scheds))}/{len(linked_scheds)} schedules...")

# 5. Delete professor_load rows
print("[5/5] Deleting professor_load rows...")
client.table('professor_load').delete().in_('id', load_ids).execute()

# Verify
verify_load = client.table('professor_load').select('id', count='exact').execute()
remaining_count = verify_load.count or len(verify_load.data or [])
print(f"\n==========================================")
print(f"SUCCESS: professor_load table cleared.")
print(f"Remaining professor_load rows: {remaining_count}")
print(f"Backup saved to: {latest_filename}")
print(f"==========================================")
