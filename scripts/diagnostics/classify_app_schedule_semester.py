import re

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

schedule_semester_hits = []

for idx, line in enumerate(lines, 1):
    l_str = line.strip()
    # Check for direct database query / update / insert / filter on schedule table referencing semester
    # e.g. .eq('semester', ...), .select(...semester...), 'semester': ..., row.get('semester')
    # Or in routes / functions related to schedule
    # Let's inspect
    if 'semester' in line.lower():
        # Let's check if line or nearby lines touch schedule
        # Look at window of +- 5 lines
        window = "".join(lines[max(0, idx - 6):min(len(lines), idx + 5)])
        if "table('schedule')" in window or 'table("schedule")' in window or 'schedule_row' in window or 'preview_entries' in window or 'preview' in window:
            schedule_semester_hits.append((idx, l_str, window))

print(f"Total potential schedule-related semester lines in app.py: {len(schedule_semester_hits)}")

with open('scripts/diagnostics/app_schedule_semester_detailed.txt', 'w', encoding='utf-8') as out:
    for ln, l_str, win in schedule_semester_hits:
        out.write(f"Line {ln}: {l_str}\n")

print("Saved to scripts/diagnostics/app_schedule_semester_detailed.txt")
