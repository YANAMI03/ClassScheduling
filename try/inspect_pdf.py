import zlib, re

with open(r'try/HEADER_FOOTER.pdf', 'rb') as f:
    data = f.read()

idx = data.find(b'4 0 obj')
s_idx = data.find(b'stream', idx) + 6
if data[s_idx:s_idx+2] == b'\r\n': s_idx += 2
elif data[s_idx:s_idx+1] == b'\n': s_idx += 1
e_idx = data.find(b'endstream', s_idx)
stream = zlib.decompress(data[s_idx:e_idx]).decode('latin1')

print('=== STREAM PARSED ELEMENTS ===')

# Images
for m in re.finditer(r'q\s*(.*?)/(\w+)\s+Do\s*Q', stream, re.DOTALL):
    block = m.group(1)
    img_name = m.group(2)
    cm_m = re.search(r'([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+cm', block)
    re_m = re.search(r'([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+re', block)
    clip_re = re_m.groups() if re_m else None
    cm = cm_m.groups() if cm_m else None
    print(f'IMAGE /{img_name}: pos=({cm[4] if cm else "?"}, {cm[5] if cm else "?"}) size=({cm[0] if cm else "?"} x {cm[3] if cm else "?"}) clip={clip_re}')

# Rectangles & Lines
for m in re.finditer(r'([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+re\s*([fFsSW\*]+)', stream):
    print(f'RECT: x={m.group(1)} y={m.group(2)} w={m.group(3)} h={m.group(4)} op={m.group(5)}')

for m in re.finditer(r'([\d\.\-]+)\s+([\d\.\-]+)\s+m\s+([\d\.\-]+)\s+([\d\.\-]+)\s+l\s*([sS])', stream):
    print(f'LINE: ({m.group(1)},{m.group(2)}) -> ({m.group(3)},{m.group(4)}) op={m.group(5)}')

# Text blocks
for m in re.finditer(r'BT\s*(.*?)\s*ET', stream, re.DOTALL):
    block = m.group(1)
    font_m = re.search(r'/(\w+)\s+([\d\.]+)\s+Tf', block)
    tm_m = re.search(r'([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+Tm', block)
    color_rgb = re.search(r'([\d\.\-]+)\s+([\d\.\-]+)\s+([\d\.\-]+)\s+rg', block)
    color_g = re.search(r'([\d\.\-]+)\s+g', block)
    text_pieces = re.findall(r'\((.*?)\)', block)
    text = ''.join(text_pieces)
    if not text.strip():
        continue
    font = font_m.group(0) if font_m else 'No font'
    tm = tm_m.groups() if tm_m else ()
    x = tm[4] if len(tm) > 4 else '?'
    y = tm[5] if len(tm) > 5 else '?'
    scale_x = tm[0] if len(tm) > 0 else '?'
    col = f'rgb({color_rgb.group(1)},{color_rgb.group(2)},{color_rgb.group(3)})' if color_rgb else (f'gray({color_g.group(1)})' if color_g else 'default')
    print(f'TEXT: "{text}" | Font: {font} | pos: ({x}, {y}) scale_x={scale_x} | col: {col}')
