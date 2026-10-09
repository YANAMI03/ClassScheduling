import os
import re

categorized_hits = []

# Regex patterns
p_eq_sem = re.compile(r"\.eq\(\s*['\"]semester['\"]\s*,", re.IGNORECASE)
p_sel_sem = re.compile(r"select\(.*?\bsemester\b", re.IGNORECASE)
p_insert_sem = re.compile(r"['\"]semester['\"]\s*:", re.IGNORECASE)
p_update_sem = re.compile(r"\.update\(\{.*?['\"]semester['\"]", re.IGNORECASE)

# Target files to analyze
files = [
    'app.py',
    'pdf_export.py',
    'static/index.js',
    'templates/schedules.html',
    'templates/schedule_archive.html',
    'templates/preview_schedule.html',
    'templates/generated_schedule.html',
    'templates/professor_schedule.html',
    'templates/generated_professor_schedule.html',
]

for fpath in files:
    if not os.path.exists(fpath):
        continue
    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
        for idx, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str or line_str.startswith('#') or line_str.startswith('//'):
                continue
            
            # Check for operation type on schedule
            op = None
            if p_eq_sem.search(line):
                op = 'FILTER (.eq)'
            elif p_sel_sem.search(line) and ('schedule' in line or idx in range(2490, 2520) or idx in range(7200, 7220) or idx in range(11650, 11675) or idx in range(12030, 12050)):
                op = 'READ (.select)'
            elif ("'semester':" in line or '"semester":' in line) and ('rows.append' in line or 'row =' in line or 'chunk' in line or 'insert' in line or 'payload' in line):
                op = 'WRITE (insert payload)'
            elif ('archive' in line or 'schedule' in line) and '.update(' in line and 'semester' in line:
                op = 'WRITE (.update)'
            elif 'semester' in line.lower() and any(k in line.lower() for k in ['schedule', 'archive_batch', 'active_sem', 'target_sem', 'batch_id']):
                op = 'READ/LOGIC'
            
            if op:
                categorized_hits.append((fpath, idx, op, line_str))

print(f"Total categorized hits: {len(categorized_hits)}")
with open('scripts/diagnostics/categorized_semester_hits.txt', 'w', encoding='utf-8') as out:
    for f, ln, op, text in categorized_hits:
        out.write(f"[{op}] {f}:{ln} -> {text}\n")
print("Saved to scripts/diagnostics/categorized_semester_hits.txt")
