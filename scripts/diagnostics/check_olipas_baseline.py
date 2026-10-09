import json

baseline = json.load(open(r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'))['preview_summary']

print("BASELINE SCHEDULE FOR CRIS NORMAN P. OLIPAS:")
for p in sorted(baseline, key=lambda x: (x['day'], x['start'])):
    if 'Olipas' in p['prof']:
        print(f"  {p['day']:<10} {p['start']:<8} - {p['end']:<8} | {p['course']:<15} | {p['section']:<8} | {p['room']}")
