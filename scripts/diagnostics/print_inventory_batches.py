import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

with open('scripts/diagnostics/inventory_data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

templates = sorted(list(set(d['template'] for d in data)))

start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
end = int(sys.argv[2]) if len(sys.argv) > 2 else len(templates)

for t in templates[start:end]:
    items = [d for d in data if d['template'] == t]
    ca = len([e for e in items if e['category'].startswith('Class A')])
    cb = len([e for e in items if e['category'].startswith('Class B')])
    cc = len([e for e in items if e['category'].startswith('Class C')])
    print(f"\n--- {t} (Total: {len(items)}, A: {ca}, B: {cb}, C: {cc}) ---")
    for e in items:
        code = 'A' if e['category'].startswith('Class A') else ('B' if e['category'].startswith('Class B') else 'C')
        id_str = f" id='{e['id']}'" if e['id'] else ""
        print(f"  [{code}] <{e['tag']}{id_str}> \"{e['text']}\" -> {e['mechanism']}")
