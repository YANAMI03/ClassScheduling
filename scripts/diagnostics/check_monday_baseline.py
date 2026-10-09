import json

baseline = json.load(open(r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'))['preview_summary']

mon_6pm_base = [p for p in baseline if p['day'] == 'Monday' and p['start'] in ('05:00 PM', '06:00 PM')]
print("CLASSES AT 5PM/6PM ON MONDAY IN BASELINE:")
for p in sorted(mon_6pm_base, key=lambda x: (x['start'], x['section'])):
    print(f"  {p['start']} - {p['end']} | Sec: {p['section']:<15} | Course: {p['course']:<15} | Prof: {p['prof']:<25} | Room: {p['room']}")
