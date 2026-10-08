import subprocess
import difflib

def get_file_content(commit, path):
    res = subprocess.run(['git', 'show', f'{commit}:{path}'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
    return res.stdout

content_old = get_file_content('bf13370', 'app.py')
content_new = get_file_content('ad3c678', 'app.py')

def extract_func(text, func_name):
    lines = text.splitlines(keepends=True)
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {func_name}(") or line.startswith(f"def {func_name}:"):
            start = i
            break
    if start is None:
        return []
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("def ") or lines[i].startswith("@app.route"):
            if not lines[i].startswith("    "):
                end = i
                break
    if end is None:
        end = len(lines)
    return lines[start:end]

lines_old = extract_func(content_old, 'generate_schedule')
lines_new = extract_func(content_new, 'generate_schedule')

print(f"Old lines count: {len(lines_old)}, New lines count: {len(lines_new)}")

diff = difflib.unified_diff(lines_old, lines_new, fromfile='bf13370:generate_schedule', tofile='ad3c678:generate_schedule', n=3)
diff_text = "".join(diff)

# Print all diff lines
for line in diff_text.splitlines():
    if line.startswith('+') or line.startswith('-'):
        if not line.startswith('+++') and not line.startswith('---'):
            print(line)
