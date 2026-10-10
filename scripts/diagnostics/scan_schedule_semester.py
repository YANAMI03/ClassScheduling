import os
import re

files_to_check = []
for root, dirs, files in os.walk('.'):
    # skip .git, venv, __pycache__, .system_generated
    if any(p in root for p in ['.git', '__pycache__', '.system_generated', 'node_modules', '.gemini']):
        continue
    for f in files:
        if f.endswith(('.py', '.html', '.js', '.sql')):
            files_to_check.append(os.path.join(root, f))

print(f"Scanning {len(files_to_check)} files for schedule.semester references...")

results = []
pattern = re.compile(r'semester', re.IGNORECASE)

for fpath in files_to_check:
    try:
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        for idx, line in enumerate(lines, 1):
            # check if line relates to schedule table or schedule query or schedule object
            if pattern.search(line):
                # Filter for schedule context
                lower_line = line.lower()
                rel_path = os.path.relpath(fpath, '.')
                # Look for indicators of schedule context
                if any(k in lower_line for k in [
                    'schedule', 'preview', 'archive', 'confirm', 'working_hours', 'generate',
                    'entry', 'entries', 'batch', 'active_tag', 'restore', 'slot'
                ]) or 'schedule' in fpath.lower():
                    results.append((rel_path, idx, line.strip()))
    except Exception as e:
        pass

print(f"Found {len(results)} occurrences in schedule context.")
with open('scripts/diagnostics/schedule_semester_hits.txt', 'w', encoding='utf-8') as out:
    for rpath, ln, text in results:
        out.write(f"{rpath}:{ln}: {text}\n")
print("Saved to scripts/diagnostics/schedule_semester_hits.txt")
