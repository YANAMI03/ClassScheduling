import json

path = r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("BASELINE SESSIONS FOR 3A-3F NETWORKING:")
for sec in ['3A-Networking', '3B-Networking', '3C-Networking', '3D-Networking', '3E-Networking', '3F-Networking']:
    print(f"\n=== {sec} ===")
    sec_entries = [p for p in data['preview_summary'] if p['section'] == sec]
    print(f"Total entries: {len(sec_entries)}")
    for p in sorted(sec_entries, key=lambda x: (x['day'], x['start'])):
        print(f"  {p['day']:<10} {p['start']:<8} - {p['end']:<8} | {p['course']:<12} | {p['room']:<8} | {p['prof']}")
