"""Shared per-course color assignment for exported schedules (PDF/room util).

Rule: the SAME course always gets the SAME base color (deterministic hash of the
course code, independent of row order), and each session type uses a different
SHADE of that color — e.g. Lecture = base, Laboratory = a lighter tint.
No grays.
"""
import hashlib

# Vivid, print-friendly hues (no grays).
_PALETTE = [
    '#1D4ED8',  # blue
    '#0E7490',  # dark cyan
    '#4338CA',  # indigo
    '#15803D',  # green
    '#7C3AED',  # violet
    '#B45309',  # dark amber
    '#C026D3',  # fuchsia
    '#DC2626',  # red
    '#DB2777',  # pink
    '#4D7C0F',  # olive
    '#0D9488',  # teal
    '#EA580C',  # orange
    '#6D28D9',  # deep purple
    '#0891B2',  # cyan
    '#BE123C',  # rose
    '#9333EA',  # purple
    '#A16207',  # dark amber 2
    '#047857',  # emerald
    '#7E22CE',  # grape
    '#B91C1C',  # dark red
]

_WHITE = '#FFFFFF'


def _normalize_code(course_code):
    return str(course_code or 'TBA').strip().upper() or 'TBA'


def course_index(course_code):
    """Stable palette index for a course code (same course -> same index)."""
    digest = hashlib.md5(_normalize_code(course_code).encode('utf-8')).hexdigest()
    return int(digest, 16) % len(_PALETTE)


def course_base(course_code):
    """The base hex color for a course."""
    return _PALETTE[course_index(course_code)]


def _rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _blend(hex_color, other_hex, factor):
    """Blend hex_color toward other_hex by factor (0=hex_color, 1=other_hex)."""
    r1, g1, b1 = _rgb(hex_color)
    r2, g2, b2 = _rgb(other_hex)
    r = round(r1 + (r2 - r1) * factor)
    g = round(g1 + (g2 - g1) * factor)
    b = round(b1 + (b2 - b1) * factor)
    return '#%02X%02X%02X' % (r, g, b)


def build_course_color_map(course_codes):
    """Assign a DISTINCT base color to each distinct course in a document.

    Each course first tries its deterministic hash color; on collision it takes
    the next free palette slot. This keeps colors stable across exports for
    non-colliding courses while guaranteeing different courses differ (until the
    palette is exhausted).
    """
    codes = []
    seen = set()
    for c in (course_codes or []):
        n = _normalize_code(c)
        if n not in seen:
            seen.add(n)
            codes.append(n)
    codes.sort()

    used_slots = set()
    mapping = {}
    for c in codes:
        slot = course_index(c)
        tries = 0
        while slot in used_slots and tries < len(_PALETTE):
            slot = (slot + 1) % len(_PALETTE)
            tries += 1
        used_slots.add(slot)
        mapping[c] = _PALETTE[slot]
    return mapping


def fill_from_base(base_hex, session_type=None):
    """Shade a base color by session type (Lecture base, Lab lighter, Home deeper)."""
    st = (session_type or '').strip().lower()
    if 'lab' in st:
        return _blend(base_hex, _WHITE, 0.76)   # lighter tint (e.g. light blue)
    if 'home' in st or 'advis' in st or 'consult' in st:
        return _blend(base_hex, _WHITE, 0.36)   # deeper tint
    return _blend(base_hex, _WHITE, 0.50)       # lecture / default


def course_fill_hex(course_code, session_type=None, color_map=None):
    """Hex fill for a course + session type.

    If `color_map` (from build_course_color_map) is supplied, the course's
    assigned base color is used; otherwise a deterministic hash color is used.
    """
    base = None
    if color_map:
        base = color_map.get(_normalize_code(course_code))
    if not base:
        base = course_base(course_code)
    return fill_from_base(base, session_type)


def course_text_hex(course_code, session_type=None, color_map=None):
    """Readable text color (black or white) for the given fill luminance."""
    hex_fill = course_fill_hex(course_code, session_type, color_map)
    r, g, b = _rgb(hex_fill)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return '#FFFFFF' if luminance < 0.55 else '#1F2937'


def course_text_hex(course_code, session_type=None):
    """Readable text color (black or white) for the given fill luminance."""
    hex_fill = course_fill_hex(course_code, session_type)
    r, g, b = _rgb(hex_fill)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return '#FFFFFF' if luminance < 0.55 else '#1F2937'
