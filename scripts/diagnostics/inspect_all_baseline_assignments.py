import json

path = r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("ALL PROFESSORS AND SECTIONS IN BASELINE RUN:")
by_course = {}
for p in data['preview_summary']:
    c = p['course']
    prof = p['prof']
    sec = p['section']
    by_course.setdefault(c, {}).setdefault(prof, set()).add(sec)

for c in sorted(by_course.keys()):
    print(f"\nCourse: {c}")
    for prof, secs in sorted(by_course[c].items()):
        print(f"  {prof:<30} ({len(secs)} secs): {sorted(secs)}")
