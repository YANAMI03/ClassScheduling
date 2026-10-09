import json

path = r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print(f"Total placed: {data['total_placed']}")
entries = data['preview_summary']
late_entries = [e for e in entries if e['end'] in ('06:00 PM', '07:00 PM', '08:00 PM')]
print(f"Entries ending at or after 6:00 PM: {len(late_entries)}")
professors_late = set(e['prof'] for e in late_entries)
print(f"Professors with classes ending at/after 6:00 PM: {len(professors_late)}")
for p in sorted(professors_late):
    print(f"  {p}")
