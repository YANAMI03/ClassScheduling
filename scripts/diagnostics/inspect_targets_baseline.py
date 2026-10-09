import json

baseline = json.load(open(r'C:\Users\Administrator\.gemini\antigravity-ide\brain\bcf8874e-ad14-48d4-8474-99c839a48f6c\scratch\baseline_generation_run.json'))['preview_summary']

targets = [
    ('Michelle Ann Mae G. Franco', 'IT-IAS02', '3F-Networking'),
    ('Christian Noli C. Tambio', 'IT-IAS02', '3C-Networking'),
    ('Christian Noli C. Tambio', 'IT-IAS02', '3D-Networking'),
    ('Rosalie B. Sison', 'IT-NET05', '3E-Networking'),
    ('Henry T. Roque', 'IT-IAS02', '3E-Networking'),
    ('Jev D. Corpuz', 'IT-NET04', '3E-Networking'),
    ('Jev D. Corpuz', 'IT-NET04', '3F-Networking'),
    ('Jev D. Corpuz', 'IT-NET05', '3F-Networking'),
]

print("EXACT BASELINE PLACEMENT FOR TARGET MISSING SESSIONS:")
for p in baseline:
    for prof, cname, sec in targets:
        if prof in p['prof'] and cname == p['course'] and sec == p['section']:
            print(f"  {sec:<15} | {p['course']:<10} | {p['prof']:<28} | {p['day']:<10} {p['start']} - {p['end']} | Room: {p['room']}")
