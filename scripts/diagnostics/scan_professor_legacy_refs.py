import os
import re
import json

ROOT = r"c:\Users\Administrator\Oct9capstone\ClassScheduling"

PATTERNS = [
    r"\bprofessors\b",
    r"\bprofessor_id\b",
    r"\bprof_id\b",
    r"\binstructor_id\b",
    r"Prof\s*#",
    r"\.table\(['\"]professors['\"]\)",
    r"\bprofessor_program\b",
    r"users\.professor_id",
    r"\.get\(['\"]prof_id['\"]\)",
    r"\.get\(['\"]professor_id['\"]\)",
    r"\[['\"]prof_id['\"]\]",
    r"\[['\"]professor_id['\"]\]",
]

compiled = [(p, re.compile(p, re.IGNORECASE)) for p in PATTERNS]

results = []

EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', '.idea', '.vscode'}
EXTS = {'.py', '.html', '.js', '.sql', '.json'}

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
    for fn in filenames:
        ext = os.path.splitext(fn)[1].lower()
        if ext not in EXTS:
            continue
        rel_path = os.path.relpath(os.path.join(dirpath, fn), ROOT)
        # Skip if in scripts/diagnostics or migrations or tests unless relevant
        file_path = os.path.join(dirpath, fn)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_no, line in enumerate(f, 1):
                    for pat_str, regex in compiled:
                        if regex.search(line):
                            results.append({
                                'file': rel_path,
                                'line': line_no,
                                'pattern': pat_str,
                                'content': line.strip()
                            })
        except Exception as e:
            pass

print(f"Total occurrences found: {len(results)}")
with open(os.path.join(ROOT, "scripts", "diagnostics", "legacy_prof_scan.json"), "w", encoding="utf-8") as out:
    json.dump(results, out, indent=2)

print("Saved to scripts/diagnostics/legacy_prof_scan.json")
