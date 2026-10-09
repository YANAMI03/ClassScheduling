import re

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

hits = []
for i, line in enumerate(lines, 1):
    if ".table('schedule')" in line or '.table("schedule")' in line:
        block = ''.join(lines[i-1:min(len(lines), i+8)])
        # Check if selecting 'semester' directly (not as course(semester)) or filtering .eq('semester'
        if re.search(r"\.eq\(\s*['\"]semester['\"]", block):
            hits.append((i, 'FILTER', block.strip()))
        elif re.search(r"select\([^)]*\bsemester\b[^)]*\)", block) and "course(semester" not in block and "course(" not in block:
            hits.append((i, 'SELECT', block.strip()))

print(f"Total potential schedule.semester query hits in app.py: {len(hits)}")
for ln, htype, b in hits:
    print(f"--- Line {ln} [{htype}] ---")
    print(b)
