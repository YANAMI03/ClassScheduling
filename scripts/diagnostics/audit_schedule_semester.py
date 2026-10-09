import os
import re

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("=== 1. QUERIES TO table('schedule') ===")
sched_hits = []
for i, line in enumerate(lines, 1):
    if "table('schedule')" in line or 'table("schedule")' in line:
        sched_hits.append((i, line.strip()))

for ln, text in sched_hits:
    # Print the line and the next 4 lines to see select / update / insert / filter
    context = [lines[j].strip() for j in range(ln - 1, min(len(lines), ln + 4))]
    print(f"Line {ln}:")
    for cl in context:
        print(f"    {cl}")

print("\n=== 2. ALL REFERENCES TO 'semester' IN A SCHEDULE CONTEXT ===")
sem_hits = []
for i, line in enumerate(lines, 1):
    if 'semester' in line:
        sem_hits.append((i, line.strip()))

print(f"Total lines mentioning 'semester' in app.py: {len(sem_hits)}")
