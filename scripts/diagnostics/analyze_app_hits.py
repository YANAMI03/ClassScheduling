import json
import ast
import re

with open('scripts/diagnostics/legacy_prof_scan.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

app_hits = [d for d in data if d['file'] == 'app.py']

# Let's read app.py to find surrounding function for each line
with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

def get_surrounding_func(line_num):
    # Search backwards for def or @app.route
    for i in range(line_num - 1, -1, -1):
        l = lines[i]
        if l.startswith('def ') or l.startswith('@app.route'):
            return l.strip()
    return "module"

by_func = {}
for h in app_hits:
    func = get_surrounding_func(h['line'])
    by_func.setdefault(func, []).append(h)

print(f"Total hits in app.py: {len(app_hits)}")
print(f"Functions/Routes with hits: {len(by_func)}")
for func, hits in sorted(by_func.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"\n=== {func} ({len(hits)} hits) ===")
    for h in hits[:10]:
        print(f"  Line {h['line']}: {h['content']}")
    if len(hits) > 10:
        print(f"  ... and {len(hits) - 10} more")
