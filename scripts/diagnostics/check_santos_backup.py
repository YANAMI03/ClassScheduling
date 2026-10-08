import json

with open('backups/professor_load_backup_20261003_191218.json') as f:
    data = json.load(f)

sched = [s for s in data.get('full_schedule_rows', []) if not s.get('archive')]

# Find prof 34 (Santos) loads: 99 and 121
santos_rows = [s for s in sched if s.get('professor_load_id') in (99, 121)]
santos_rows.sort(key=lambda s: (s['day'], s['class_start']))
print("=== RONALD S. SANTOS IN WORKING BACKUP (441 rows) ===")
for s in santos_rows:
    print(f"{s['day']:<10} | {s['class_start']}-{s['class_end']} | {s['session_type']:<10} | sec={s['section']:<15} | room={s['room_id']} | load={s['professor_load_id']}")
