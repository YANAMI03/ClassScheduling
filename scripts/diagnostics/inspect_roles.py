import re

print("--- Checking links to /courses, /rooms, /working_hours in templates ---")
for fpath in ['templates/professor_load.html', 'templates/index.html', 'templates/generated_professor_schedule.html']:
    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    matches = re.findall(r'.{0,50}(?:/courses|/rooms|/working_hours|add courses|add rooms).{0,50}', content, re.I)
    print(f"\n{fpath}:")
    for m in matches:
        print("  ...", m.strip(), "...")
