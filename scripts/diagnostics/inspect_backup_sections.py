import json

with open('backups/professor_load_backup_latest.json') as f:
    d = json.load(f)

pls = {p['id']: p for p in d.get('professor_load_rows', [])}
active_rows = [r for r in d.get('full_schedule_rows', []) if not r.get('archive')]

print(f"Total active rows in backup: {len(active_rows)}")

for sec in ['3C-Networking', '3D-Networking', '3E-Networking', '3F-Networking']:
    print(f"\n==================== {sec} ====================")
    sec_entries = [r for r in active_rows if r.get('section') == sec]
    sec_entries.sort(key=lambda x: (x['day'], x['class_start']))
    for e in sec_entries:
        pl = pls.get(e['professor_load_id'], {})
        print(f"  {e['day']:<10} {e['class_start']}-{e['class_end']} | {e['session_type']:<10} | Course {pl.get('course_id')} | Prof {pl.get('prof_id')} | Room {e['room_id']}")
