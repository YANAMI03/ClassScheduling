import json

baseline = json.load(open(r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'))['preview_summary']

by_day = {}
for p in baseline:
    by_day.setdefault(p['day'], []).append((p['start'], p['end']))

for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']:
    slots = by_day.get(day, [])
    starts = sorted(set(s[0] for s in slots))
    ends = sorted(set(s[1] for s in slots))
    print(f"{day}:")
    print(f"  Starts: {starts}")
    print(f"  Ends:   {ends}")
