import os
import glob
import re

patterns = [
    (r'<button\b[^>]*>', 'HTML <button> tag'),
    (r'<a\b[^>]*class=["\'][^"\']*btn[^"\']*["\']', 'HTML <a class="btn"> tag'),
    (r'createElement\(["\']button["\']\)', 'createElement("button")')
]

print("=== Scanning static/*.js ===")
for js in sorted(glob.glob('static/*.js')):
    with open(js, 'r', encoding='utf-8') as f:
        content = f.read()
    for pattern, name in patterns:
        matches = list(re.finditer(pattern, content, re.IGNORECASE))
        if matches:
            print(f"[{js}] {len(matches)} occurrences of {name}")
            for m in matches[:5]:
                print(f"   Line: {content[:m.start()].count(chr(10))+1}: {m.group(0)[:80]}")

print("\n=== Scanning inline <script> in templates/*.html ===")
for t in sorted(glob.glob('templates/*.html')):
    with open(t, 'r', encoding='utf-8') as f:
        content = f.read()
    scripts = list(re.finditer(r'<script\b[^>]*>(.*?)</script>', content, re.DOTALL | re.IGNORECASE))
    for s_idx, s in enumerate(scripts):
        s_text = s.group(1)
        offset = s.start(1)
        for pattern, name in patterns:
            matches = list(re.finditer(pattern, s_text, re.IGNORECASE))
            if matches:
                print(f"[{os.path.basename(t)} script #{s_idx+1}] {len(matches)} occurrences of {name}")
                for m in matches[:5]:
                    line_num = content[:offset + m.start()].count('\n') + 1
                    snippet = m.group(0).replace('\n', ' ')[:80]
                    print(f"   Line {line_num}: {snippet}")
