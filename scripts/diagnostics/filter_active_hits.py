import json
import re

with open('scripts/diagnostics/legacy_prof_scan.json', 'r', encoding='utf-8') as f:
    hits = json.load(f)

# Filter out diagnostics, backups, migrations, and mock data files if needed,
# or classify all active application files (app.py, templates, static, tests).

active_hits = [h for h in hits if not h['file'].startswith('backups') and not h['file'].startswith('scripts\\diagnostics')]

print(f"Total active hits: {len(active_hits)}")

# Let's inspect active files
files = set(h['file'] for h in active_hits)
print("Files involved:", sorted(files))
