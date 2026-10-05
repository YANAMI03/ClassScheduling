import os
import sys

sys.path.insert(0, os.path.abspath('.'))
import scripts.diagnostics.reproduce_generation as rg

res = rg.simulate_generation()
sched = res['preview_entries']

for prof_id, name in [(19, 'Tambio'), (34, 'Santos'), (35, 'Corpuz')]:
    entries = [e for e in sched if e.get('prof_id') == prof_id]
    print(f"\n=== {name} (prof_id {prof_id}) ===")
    print(f"Total sessions placed: {len(entries)}")
    for e in entries:
        print(f"  {e['course_name']} | Sec: {e['section']} | {e['session_type']} | {e['day']} {e['start']} - {e['end']} | Room: {e['room_name']}")
