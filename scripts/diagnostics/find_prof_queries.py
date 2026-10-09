import re

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Queries referencing 'professor' table or embedding in app.py:")
for idx, line in enumerate(lines, 1):
    if any(q in line for q in [".table('professor')", '.table("professor")', 'professor(', 'professor:', 'prof_id']):
        if not line.strip().startswith('#'):
            print(f"Line {idx:5d}: {line.strip()[:110]}")
