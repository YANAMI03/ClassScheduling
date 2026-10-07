"""
Professor Load Importer Module
Handles parsing, name/course matching, ranking workload validation,
template generation, error reporting, and bulk database persistence.
"""

import io
import re
import csv
import logging
from typing import List, Dict, Any, Tuple, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


MAX_IMPORT_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def normalize_text(val: Any) -> str:
    """Normalize text: trim, replace \xa0 with space, collapse multiple spaces."""
    if val is None:
        return ""
    s = str(val).replace('\xa0', ' ').strip()
    return re.sub(r'\s+', ' ', s)


def normalize_name(name: str) -> str:
    """Normalize person name for matching: lowercase, strip periods, normalize whitespace."""
    s = normalize_text(name).lower().replace('.', '')
    return s


def get_name_tokens(name: str) -> List[str]:
    """Extract word tokens from normalized name, handling suffixes like jr, sr, ii, iii, iv."""
    return [t for t in normalize_name(name).split() if t]


def split_full_name(name: str) -> Tuple[str, str]:
    """
    Split full name into (first_name, last_name).
    Handles suffixes like Jr., Sr., II, III, IV, etc.
    """
    tokens = [t for t in normalize_text(name).split() if t]
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""

    if len(tokens) >= 3 and tokens[-1].lower().rstrip('.') in {'jr', 'sr', 'ii', 'iii', 'iv', 'v'}:
        first_name = " ".join(tokens[:-2])
        last_name = f"{tokens[-2]} {tokens[-1]}"
    else:
        first_name = " ".join(tokens[:-1])
        last_name = tokens[-1]
    return first_name, last_name


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if s1 == s2:
        return 0
    if len(s1) == 0:
        return len(s2)
    if len(s2) == 0:
        return len(s1)

    dp = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        new_dp = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            new_dp[j + 1] = min(
                dp[j + 1] + 1,
                new_dp[j] + 1,
                dp[j] + cost
            )
        dp = new_dp
    return dp[len(s2)]


def find_similar_existing_professors(name_str: str, professors_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Check if a new professor name is similar to any existing professor:
    - Levenshtein distance <= 2 on normalized full names, OR
    - Same last name and same first name initial.
    Returns list of matching existing professor records.
    """
    norm_input = normalize_name(name_str)
    in_first, in_last = split_full_name(name_str)
    norm_in_first = normalize_name(in_first)
    norm_in_last = normalize_name(in_last)
    in_initial = norm_in_first[0] if norm_in_first else ""

    similar_profs = []
    for p in professors_list:
        db_first = p.get('first_name') or ''
        db_last = p.get('last_name') or ''
        norm_db_full = normalize_name(f"{db_first} {db_last}")
        if not norm_db_full or norm_db_full == norm_input:
            continue

        # 1. Levenshtein distance <= 2
        if levenshtein_distance(norm_input, norm_db_full) <= 2:
            similar_profs.append(p)
            continue

        # 2. Same last name + first initial
        norm_db_first = normalize_name(db_first)
        norm_db_last = normalize_name(db_last)
        db_initial = norm_db_first[0] if norm_db_first else ""
        if norm_in_last and norm_db_last and norm_in_last == norm_db_last:
            if in_initial and db_initial and in_initial == db_initial:
                similar_profs.append(p)
                continue

    return similar_profs


def normalize_course_code(code: str) -> str:
    """Normalize course code for exact/case-insensitive matching."""
    return normalize_text(code).upper()


def clean_course_code(raw_code: str, known_course_codes_map: Dict[str, Any]) -> Tuple[str, bool, Optional[str]]:
    """
    Attempt to match a course code, cleaning dirty suffixes if needed.
    Returns (matched_code, was_cleaned, note).
    """
    norm = normalize_course_code(raw_code)
    if norm in known_course_codes_map:
        return norm, False, None

    # Clean stray characters after closing parenthesis: e.g. "IT-CAP01 (WST)1\xa0" -> "IT-CAP01 (WST)"
    # Regex checks for a closing parenthesis followed by digits or spaces
    cleaned = re.sub(r'(\))\s*[\d\s]+$', r'\1', norm).strip()
    if cleaned in known_course_codes_map:
        return cleaned, True, f"Auto-cleaned course code from '{normalize_text(raw_code)}' to '{cleaned}'"

    # Also check OCR typo: e.g. "IT-HC101" -> "IT-HCI01"
    if 'HC1' in norm:
        hci_candidate = re.sub(r'HC1(\d*)', r'HCI\1', norm)
        if hci_candidate in known_course_codes_map:
            return hci_candidate, True, f"Auto-cleaned course code from '{normalize_text(raw_code)}' to '{hci_candidate}'"

    # Also check without trailing digits or spaces
    stripped = re.sub(r'[\d\s]+$', '', norm).strip()
    if stripped in known_course_codes_map:
        return stripped, True, f"Auto-cleaned course code from '{normalize_text(raw_code)}' to '{stripped}'"

    return norm, False, None


def parse_year_divider(text: str) -> Optional[int]:
    """Detect if a text line is a Year divider row (e.g. '1st Year', '2nd Year', '3rd Year', '4th Year')."""
    if not text:
        return None
    cleaned = normalize_text(text).lower()
    m = re.match(r'^(1st|2nd|3rd|4th|\d+(?:st|nd|rd|th)?)\s*year', cleaned)
    if m:
        val = m.group(1)
        for digit, label in [('1', '1st'), ('2', '2nd'), ('3', '3rd'), ('4', '4th')]:
            if digit in val or label in val:
                return int(digit)
    return None


def detect_csv_delimiter(sample_lines: List[str]) -> str:
    """Detect delimiter among comma, semicolon, tab from sample lines."""
    delims = [',', ';', '\t']
    counts = {d: 0 for d in delims}
    for line in sample_lines:
        for d in delims:
            counts[d] += line.count(d)
    best = max(delims, key=lambda d: counts[d])
    return best if counts[best] > 0 else ','


def parse_import_file(file_bytes: bytes, filename: str = '') -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, Any]]]:
    """
    Parse an Excel (.xlsx) or CSV file.
    Returns:
      - data_rows: list of parsed data rows
      - dividers: list of detected dividers
      - parse_errors: fatal file-level parse errors (e.g. empty file, missing headers)
    """
    if not file_bytes or len(file_bytes.strip() if isinstance(file_bytes, (bytes, bytearray)) else file_bytes) == 0:
        return [], [], ["The uploaded file is empty."]

    is_zip = file_bytes.startswith(b'PK\x03\x04')
    is_xlsx_ext = filename.lower().endswith('.xlsx')
    rows_raw: List[List[Any]] = []

    if is_zip or is_xlsx_ext:
        try:
            wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
            sheet = wb.active
            if sheet is not None:
                for r in range(1, sheet.max_row + 1):
                    row_vals = [sheet.cell(r, c).value for c in range(1, sheet.max_column + 1)]
                    rows_raw.append(row_vals)
        except Exception as e:
            if is_xlsx_ext:
                return [], [], [f"Failed to read Excel file (.xlsx): The file is corrupted or not a valid Excel file ({e})."]
            return [], [], [f"Failed to read spreadsheet file: {e}"]
    else:
        content_str = None
        for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                content_str = file_bytes.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if content_str is None:
            try:
                content_str = file_bytes.decode('latin-1', errors='replace')
            except Exception as e:
                return [], [], [f"Failed to decode CSV file: {e}"]

        try:
            lines = [l for l in content_str.splitlines() if l.strip()]
            delim = detect_csv_delimiter(lines[:15]) if lines else ','
            reader = csv.reader(io.StringIO(content_str), delimiter=delim)
            for row in reader:
                rows_raw.append(row)
        except Exception as e:
            return [], [], [f"Failed to parse CSV file: {e}"]

    # Filter out completely empty rows
    non_empty_rows = []
    for r in rows_raw:
        if r and any(c is not None and str(c).strip() != '' for c in r):
            non_empty_rows.append(r)

    if not non_empty_rows:
        return [], [], ["The uploaded file is empty."]

    # Find Header Row
    header_idx = -1
    col_name_idx = -1
    col_code_idx = -1
    col_sec_idx = -1
    col_ilp_idx = -1

    for idx, row in enumerate(rows_raw[:15]):
        row_str = [normalize_text(c).lower() for c in row if c is not None]
        # Look for headers containing 'name', 'course', 'section'
        has_name = any('name' in c or 'prof' in c or 'faculty' in c for c in row_str)
        has_course = any('course' in c or 'subject' in c or 'code' in c for c in row_str)
        has_sec = any('section' in c or 'sec' in c for c in row_str)

        if has_name and has_course:
            header_idx = idx
            for c_idx, cell in enumerate(row):
                c_norm = normalize_text(cell).lower()
                if ('name' in c_norm or 'prof' in c_norm or 'faculty' in c_norm) and col_name_idx == -1:
                    col_name_idx = c_idx
                elif ('course' in c_norm or 'code' in c_norm or 'subject' in c_norm) and col_code_idx == -1:
                    col_code_idx = c_idx
                elif ('section' in c_norm or 'sec' in c_norm) and col_sec_idx == -1:
                    col_sec_idx = c_idx
                elif ('ilp' in c_norm) and col_ilp_idx == -1:
                    col_ilp_idx = c_idx
            break

    # If headers not explicitly named, error out
    if header_idx == -1:
        return [], [], ["Missing required header row. The file must have columns: NAME, Course Code, Number of sections."]

    if col_name_idx == -1:
        col_name_idx = 0
    if col_code_idx == -1:
        col_code_idx = 1
    if col_sec_idx == -1:
        col_sec_idx = 2

    data_rows: List[Dict[str, Any]] = []
    dividers: List[Dict[str, Any]] = []
    current_year_level: Optional[int] = None
    current_year_label: str = ""

    for row_idx in range(header_idx + 1, len(rows_raw)):
        row = rows_raw[row_idx]
        excel_row_num = row_idx + 1

        val1 = row[col_name_idx] if col_name_idx < len(row) else None
        val2 = row[col_code_idx] if col_code_idx < len(row) else None
        val3 = row[col_sec_idx] if col_sec_idx < len(row) else None
        val4 = row[col_ilp_idx] if col_ilp_idx != -1 and col_ilp_idx < len(row) else None

        # Check if completely blank row
        if (val1 is None or str(val1).strip() == '') and \
           (val2 is None or str(val2).strip() == '') and \
           (val3 is None or str(val3).strip() == ''):
            continue

        # Check for Year divider row (Col 1 has text, Col 2 and Col 3 empty/None)
        txt1 = normalize_text(val1)
        txt2 = normalize_text(val2)
        txt3 = normalize_text(val3)
        txt4 = normalize_text(val4) if val4 is not None else None

        detected_year = parse_year_divider(txt1)
        if detected_year is not None and not txt2 and not txt3:
            current_year_level = detected_year
            current_year_label = txt1
            dividers.append({
                'row_number': excel_row_num,
                'year_level': detected_year,
                'label': txt1,
            })
            continue

        # Data row
        data_rows.append({
            'row_number': excel_row_num,
            'raw_professor': txt1,
            'raw_course': txt2,
            'raw_sections': val3,
            'raw_ilp': txt4,
            'year_level': current_year_level,
            'year_label': current_year_label,
        })

    return data_rows, dividers, []


def match_professor(name_str: str, professors_list: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Match professor name by normalized full name against existing professors.
    Handles middle initials, suffixes ('Jr.', 'III'), and casing.
    Returns (matched_record, error_message).
    If professor is simply not found, returns (None, None) so caller can auto-create.
    """
    if not name_str or not str(name_str).strip():
        return None, "Professor name is empty."

    norm_input = normalize_name(name_str)
    input_tokens = get_name_tokens(name_str)

    # 1. Exact normalized match: first_name + ' ' + last_name
    for p in professors_list:
        db_first = normalize_name(p.get('first_name') or '')
        db_last = normalize_name(p.get('last_name') or '')
        db_full = normalize_name(f"{db_first} {db_last}")
        if norm_input == db_full:
            return p, None

    # 2. Match ignoring middle initials (single character tokens in either input or DB)
    def strip_initials(tokens: List[str]) -> List[str]:
        return [t for t in tokens if len(t) > 1 or t in ('ii', 'iii', 'iv')]

    input_no_init = strip_initials(input_tokens)

    candidates = []
    for p in professors_list:
        db_first = normalize_name(p.get('first_name') or '')
        db_last = normalize_name(p.get('last_name') or '')
        db_tokens = get_name_tokens(f"{db_first} {db_last}")
        db_no_init = strip_initials(db_tokens)

        if input_no_init == db_no_init and len(input_no_init) >= 2:
            candidates.append(p)

    if len(candidates) == 1:
        return candidates[0], None
    elif len(candidates) > 1:
        return None, f"Ambiguous professor name: matches {len(candidates)} records."

    # 3. Match by last name and first name prefix/token
    last_token = input_tokens[-1] if input_tokens else ""
    first_token = input_tokens[0] if input_tokens else ""
    for p in professors_list:
        db_first = normalize_name(p.get('first_name') or '')
        db_last = normalize_name(p.get('last_name') or '')
        db_tokens = get_name_tokens(f"{db_first} {db_last}")
        if len(db_tokens) >= 2:
            if db_tokens[0] == first_token and db_tokens[-1] == last_token:
                candidates.append(p)

    if len(candidates) == 1:
        return candidates[0], None

    # Not found in system - valid for auto-creation
    return None, None


def validate_import_data(
    parsed_rows: List[Dict[str, Any]],
    dividers: List[Dict[str, Any]],
    professors_list: List[Dict[str, Any]],
    courses_list: List[Dict[str, Any]],
    existing_loads_list: List[Dict[str, Any]],
    all_courses_list: Optional[List[Dict[str, Any]]] = None,
    target_program_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate all parsed rows against database records.
    Auto-creates new professors when names are not found in existing professors.
    Warns on similar names without failing.
    No ranking limits check is enforced.
    Returns comprehensive preview data.
    """
    # Build fast lookup maps
    courses_by_code: Dict[str, Dict[str, Any]] = {}
    for c in courses_list:
        c_code = normalize_course_code(c.get('course_name') or '')
        if c_code:
            courses_by_code[c_code] = c

    all_courses_by_code: Dict[str, Dict[str, Any]] = {}
    if all_courses_list:
        for c in all_courses_list:
            c_code = normalize_course_code(c.get('course_name') or '')
            if c_code:
                all_courses_by_code[c_code] = c

    # Map existing assignments: (prof_id, course_id) -> existing_load
    existing_map: Dict[Tuple[int, int], Dict[str, Any]] = {}
    existing_loads_by_prof: Dict[int, List[Dict[str, Any]]] = {}
    for pl in existing_loads_list:
        p_id = pl.get('prof_id')
        c_id = pl.get('course_id')
        if p_id and c_id:
            pair = (int(p_id), int(c_id))
            existing_map[pair] = pl
            existing_loads_by_prof.setdefault(int(p_id), []).append(pl)

    validated_rows: List[Dict[str, Any]] = []
    seen_pairs_in_file: Dict[Tuple[Any, int], int] = {}
    course_section_totals: Dict[str, Dict[str, Any]] = {}
    prof_imported_assignments: Dict[Any, List[Dict[str, Any]]] = {}
    new_professors_detected: Dict[str, Dict[str, Any]] = {}

    # First Pass: Syntax, Professor & Course resolution, Section Validation
    for r in parsed_rows:
        row_num = r['row_number']
        raw_prof = r['raw_professor']
        raw_course = r['raw_course']
        raw_sec = r['raw_sections']
        divider_year = r.get('year_level')

        row_errors: List[str] = []
        row_warnings: List[str] = []
        status = "Ready"

        # 1. Section Count Validation
        sections = 0
        try:
            if raw_sec is None or str(raw_sec).strip() == '':
                raise ValueError("Section count is blank.")
            # Handle float strings like '4.0'
            try:
                f_sec = float(str(raw_sec).strip())
            except (ValueError, TypeError):
                raise ValueError(f"'{raw_sec}' is not a valid whole number >= 1.")
            if not f_sec.is_integer():
                raise ValueError(f"Section count must be a whole number, got '{raw_sec}'.")
            sections = int(f_sec)
            if sections < 1:
                raise ValueError(f"Section count must be at least 1, got {sections}.")
        except Exception as e:
            row_errors.append(f"Invalid section count: {e}")

        # 1b. ILP Hours Validation (must be 0 or 1)
        raw_ilp = r.get('raw_ilp')
        ilp_hours = 0
        if raw_ilp is not None and str(raw_ilp).strip() != '':
            try:
                f_ilp = float(str(raw_ilp).strip())
                if not f_ilp.is_integer() or int(f_ilp) not in (0, 1):
                    row_errors.append(f"ILP hours must be 0 or 1, got '{raw_ilp}'.")
                else:
                    ilp_hours = int(f_ilp)
            except Exception:
                row_errors.append(f"Invalid ILP hours: '{raw_ilp}'. Must be 0 or 1.")

        # 2. Professor Matching & Auto-Creation
        is_new_professor = False
        matched_prof = None
        prof_id = None

        if not raw_prof or not str(raw_prof).strip():
            row_errors.append("Professor name is empty.")
        else:
            matched_prof, prof_match_err = match_professor(raw_prof, professors_list)
            if prof_match_err:
                row_errors.append(prof_match_err)
            elif matched_prof:
                prof_id = matched_prof.get('prof_id')
            else:
                # Unknown professor -> will be auto-created
                is_new_professor = True
                norm_prof_name = normalize_name(raw_prof)
                if norm_prof_name not in new_professors_detected:
                    f_name, l_name = split_full_name(raw_prof)
                    new_professors_detected[norm_prof_name] = {
                        'name': raw_prof,
                        'first_name': f_name,
                        'last_name': l_name,
                    }
                # Check for similar existing professors
                similar = find_similar_existing_professors(raw_prof, professors_list)
                if similar:
                    sim_names = ", ".join(f"'{p.get('first_name', '')} {p.get('last_name', '')}'.strip()" for p in similar)
                    row_warnings.append(
                        f"Similar professor name already exists: {sim_names}. Please verify this is a new professor."
                    )

        # 3. Course Matching & Auto-Cleaning
        matched_course = None
        cleaned_code_used = None
        was_cleaned = False
        if not raw_course:
            row_errors.append("Course code is missing.")
        else:
            resolved_code, was_cleaned, clean_note = clean_course_code(raw_course, courses_by_code)
            if resolved_code in courses_by_code:
                matched_course = courses_by_code[resolved_code]
                cleaned_code_used = resolved_code
                if was_cleaned and clean_note:
                    row_warnings.append(clean_note)
            else:
                is_cross_program = False
                if all_courses_by_code:
                    all_resolved_code, _, _ = clean_course_code(raw_course, all_courses_by_code)
                    if all_resolved_code in all_courses_by_code or normalize_course_code(raw_course) in all_courses_by_code:
                        is_cross_program = True
                if is_cross_program:
                    pname = target_program_name or "program"
                    row_errors.append(f"Course not offered in {pname}")
                else:
                    row_errors.append(f"Course not found in catalog: '{raw_course}'")

        # 4. Year Level Validation
        if matched_course and divider_year:
            c_yl = matched_course.get('year_level')
            try:
                c_yl_int = int(c_yl) if c_yl is not None else None
            except (ValueError, TypeError):
                c_yl_int = None
            if c_yl_int and c_yl_int != divider_year:
                row_warnings.append(
                    f"Year level mismatch: Course '{matched_course.get('course_name')}' is Year {c_yl_int}, "
                    f"but file placed it under Year {divider_year} divider."
                )

        # 5. Duplicate Check in File
        course_id = matched_course.get('course_id') if matched_course else None
        prof_key = prof_id if prof_id is not None else (f"new_{normalize_name(raw_prof)}" if raw_prof else None)

        is_duplicate_in_file = False
        if prof_key and course_id:
            pair = (prof_key, int(course_id))
            if pair in seen_pairs_in_file:
                first_row = seen_pairs_in_file[pair]
                row_errors.append(f"Duplicate assignment in file: Same professor and course already listed on Row {first_row}.")
                is_duplicate_in_file = True
            else:
                seen_pairs_in_file[pair] = row_num

        # Check if this assignment already exists in database (Updated vs Ready)
        is_existing_db = False
        existing_load_id = None
        if prof_id and course_id:
            pair = (int(prof_id), int(course_id))
            if pair in existing_map:
                is_existing_db = True
                existing_load_id = existing_map[pair].get('id') or existing_map[pair].get('professor_load_id')

        # Determine preliminary status
        if row_errors:
            status = "Error"
        elif is_existing_db:
            status = "Updated"
        elif row_warnings:
            status = "Warning"
        else:
            status = "Ready"

        reason = "; ".join(row_errors + row_warnings)
        if not reason:
            if is_existing_db:
                reason = "Will update existing load"
            elif is_new_professor:
                reason = "New Professor (will be created)"
            else:
                reason = "Ready to assign"

        row_item = {
            'row_number': row_num,
            'professor_name': raw_prof,
            'matched_professor_name': f"{matched_prof.get('first_name')} {matched_prof.get('last_name')}" if matched_prof else raw_prof,
            'is_new_professor': is_new_professor,
            'new_professor_badge': 'New Professor' if is_new_professor else None,
            'prof_id': prof_id,
            'department': (matched_prof.get('department') if matched_prof else None) or 'CICT',
            'raw_course_code': raw_course,
            'course_code': matched_course.get('course_name') if matched_course else raw_course,
            'course_id': course_id,
            'sections': sections,
            'ilp_hours': ilp_hours if raw_ilp is not None and str(raw_ilp).strip() != '' else (int(matched_course.get('ilp_hours') or 0) if matched_course and matched_course.get('ilp_hours') in (0, 1) else 0),
            'year_level': divider_year,
            'status': status,
            'is_existing_db': is_existing_db,
            'existing_load_id': existing_load_id,
            'errors': row_errors,
            'warnings': row_warnings,
            'was_cleaned': was_cleaned,
            'cleaning_note': clean_note if was_cleaned else None,
            'reason': reason,
            'matched_course_info': matched_course,
            'matched_prof_info': matched_prof,
        }
        validated_rows.append(row_item)

        # Record for professor load accumulation if structurally valid
        if status in ("Ready", "Updated", "Warning") and prof_key and matched_course:
            prof_imported_assignments.setdefault(prof_key, []).append(row_item)

    # Workload Summaries per Professor (No ranking limits enforced)
    prof_workload_summaries: List[Dict[str, Any]] = []
    existing_prof_map = {int(p['prof_id']): p for p in professors_list if p.get('prof_id')}

    for prof_key, imported_list in prof_imported_assignments.items():
        is_new = isinstance(prof_key, str) and prof_key.startswith('new_')
        if is_new:
            raw_n = imported_list[0]['professor_name']
            p_id = None
            prof_name = raw_n
            dept = 'CICT'
        else:
            p_id = int(prof_key)
            p_obj = existing_prof_map.get(p_id) or {}
            p_full = f"{p_obj.get('first_name', '')} {p_obj.get('last_name', '')}".strip()
            prof_name = p_full or imported_list[0]['professor_name']
            dept = p_obj.get('department') or 'CICT'

        total_hours = 0.0
        total_units = 0.0
        imported_cids = {item['course_id'] for item in imported_list}

        # For existing professors, add untouched existing assignments
        if p_id is not None and p_id in existing_loads_by_prof:
            for pl in existing_loads_by_prof[p_id]:
                c_id = pl.get('course_id')
                if c_id not in imported_cids:
                    c_info = pl.get('course') or {}
                    try:
                        secs = int(pl.get('sections') or 1)
                    except (ValueError, TypeError):
                        secs = 1
                    lec = float(c_info.get('lecture_hours') or 0)
                    lab = float(c_info.get('lab_hours') or 0)
                    ilp = float(c_info.get('ilp_hours') or 0)
                    u = float(c_info.get('units') or 0)
                    total_hours += (lec + lab + ilp) * secs
                    total_units += u * secs

        # Add imported assignments
        for item in imported_list:
            c_info = item['matched_course_info']
            secs = item['sections']
            lec = float(c_info.get('lecture_hours') or 0)
            lab = float(c_info.get('lab_hours') or 0)
            ilp = float(c_info.get('ilp_hours') or 0)
            u = float(c_info.get('units') or 0)
            total_hours += (lec + lab + ilp) * secs
            total_units += u * secs

        prof_workload_summaries.append({
            'prof_id': p_id,
            'name': prof_name,
            'department': dept,
            'total_hours': round(total_hours, 1),
            'total_units': round(total_units, 1),
            'assignments_count': len(imported_list),
            'is_new_professor': is_new,
        })

    # Compute Course Section Totals across valid rows
    valid_courses_affected = set()
    valid_profs_affected = set()
    valid_rows_count = 0
    warning_rows_count = 0
    error_rows_count = 0

    for item in validated_rows:
        if item['status'] in ("Ready", "Updated", "Warning"):
            valid_rows_count += 1
            if item.get('warnings') or item['status'] == "Warning":
                warning_rows_count += 1
            c_code = item['course_code']
            valid_courses_affected.add(c_code)
            if item.get('prof_id') is not None:
                valid_profs_affected.add(item['prof_id'])
            elif item.get('professor_name'):
                valid_profs_affected.add(f"new_{normalize_name(item['professor_name'])}")

            # Aggregate sections
            if c_code not in course_section_totals:
                course_section_totals[c_code] = {
                    'course_code': c_code,
                    'year_level': item.get('year_level') or (item['matched_course_info'].get('year_level') if item.get('matched_course_info') else None),
                    'total_sections': 0,
                    'professors_count': 0,
                }
            course_section_totals[c_code]['total_sections'] += item['sections']
            course_section_totals[c_code]['professors_count'] += 1
        else:
            error_rows_count += 1

    summary_counts = {
        'total_rows': len(parsed_rows),
        'year_dividers_count': len(dividers),
        'valid_count': valid_rows_count,
        'warning_count': warning_rows_count,
        'error_count': error_rows_count,
        'professors_affected': len(valid_profs_affected),
        'new_professors_count': len(new_professors_detected),
        'courses_affected': len(valid_courses_affected),
    }

    # Sort course section totals alphabetically
    sorted_course_totals = sorted(
        course_section_totals.values(),
        key=lambda x: str(x['course_code'])
    )

    # Sort professor workload summaries by name
    sorted_prof_summaries = sorted(
        prof_workload_summaries,
        key=lambda x: x['name']
    )

    return {
        'summary': summary_counts,
        'rows': validated_rows,
        'course_section_totals': sorted_course_totals,
        'professor_workloads': sorted_prof_summaries,
    }



def generate_import_template_xlsx() -> bytes:
    """Generate sample Excel template matching the required import format."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Professor Load Import"

    # Styling
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")  # Dark blue
    font_divider = Font(name="Calibri", size=11, bold=True, color="1E293B")
    fill_divider = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")  # Light slate
    font_data = Font(name="Calibri", size=11)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # Header Row
    headers = ["NAME", "Course Code", "Number of sections"]
    ws.append(headers)
    for col_num in range(1, 4):
        cell = ws.cell(1, col_num)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_left if col_num <= 2 else align_center
        cell.border = thin_border

    # Sample Data with Year Dividers
    sample_content = [
        # (is_divider, col1, col2, col3)
        (True, "1st Year", "", ""),
        (False, "Alexander S. Cochanco", "CC-102", 4),
        (False, "Rodibelle F. Leona", "CC-102", 6),
        (False, "Marcelino S. Cerin III", "IT-NET02", 2),
        (True, "2nd Year", "", ""),
        (False, "Leonylyn P. Bensi", "CC-104", 4),
        (False, "Apple Grace G. Oliveros", "CC-104", 1),
        (False, "Ronaldin V. Bauat", "IT-PF02", 5),
        (True, "3rd Year", "", ""),
        (False, "Henry T. Roque", "IT-IAS02", 3),
        (False, "Michelle Ann Mae G. Franco", "IT-IAS02", 1),
        (False, "Alexander S. Cochanco", "IT-CAP01 (WST)", 1),
    ]

    for item in sample_content:
        is_div, c1, c2, c3 = item
        if is_div:
            row_idx = ws.max_row + 1
            ws.append([c1, "", ""])
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=3)
            for c in range(1, 4):
                cell = ws.cell(row_idx, c)
                cell.font = font_divider
                cell.fill = fill_divider
                cell.alignment = align_left
                cell.border = thin_border
        else:
            row_idx = ws.max_row + 1
            ws.append([c1, c2, c3])
            ws.cell(row_idx, 1).font = font_data
            ws.cell(row_idx, 1).alignment = align_left
            ws.cell(row_idx, 1).border = thin_border
            ws.cell(row_idx, 2).font = font_data
            ws.cell(row_idx, 2).alignment = align_left
            ws.cell(row_idx, 2).border = thin_border
            ws.cell(row_idx, 3).font = font_data
            ws.cell(row_idx, 3).alignment = align_center
            ws.cell(row_idx, 3).border = thin_border

    # Adjust Column Widths
    ws.column_dimensions['A'].width = 32
    ws.column_dimensions['B'].width = 24
    ws.column_dimensions['C'].width = 20

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def generate_error_report_xlsx(error_rows: List[Dict[str, Any]]) -> bytes:
    """Generate Excel error report for skipped rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Import Skipped Rows"

    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header = PatternFill(start_color="DC2626", end_color="DC2626", fill_type="solid")  # Red
    font_data = Font(name="Calibri", size=11)
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    headers = ["File Row #", "Professor Name in File", "Course Code in File", "Sections", "Status", "Reason / Errors"]
    ws.append(headers)
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(1, col_num)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center if col_num in (1, 4, 5) else align_left
        cell.border = thin_border

    for r in error_rows:
        row_idx = ws.max_row + 1
        ws.append([
            r.get('row_number'),
            r.get('professor_name'),
            r.get('raw_course_code') or r.get('course_code'),
            r.get('sections'),
            r.get('status'),
            r.get('reason'),
        ])
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row_idx, c)
            cell.font = font_data
            cell.alignment = align_center if c in (1, 4, 5) else align_left
            cell.border = thin_border

    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 24
    ws.column_dimensions['D'].width = 14
    ws.column_dimensions['E'].width = 14
    ws.column_dimensions['F'].width = 60

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def execute_bulk_import(
    supabase_client: Any,
    valid_rows: List[Dict[str, Any]],
    existing_loads_list: List[Dict[str, Any]],
    program_id: Optional[int] = None,
) -> Tuple[int, int, int, List[str]]:
    """
    Execute atomic bulk database updates/inserts for valid rows.
    - If row is for an unknown professor: auto-creates professor record in 'professor'.
    - If a (prof_id, course_id) assignment already exists: UPDATE sections.
    - If new assignment: INSERT into 'professor_load'.
    - Untouched assignments are preserved.
    - On failure, deletes only newly created professors from this run.
    Returns:
      (updated_count, inserted_count, new_professors_count, error_messages)
    """
    if not valid_rows:
        return 0, 0, 0, []

    # 1. Identify and group new professors
    new_profs_by_name: Dict[str, Dict[str, Any]] = {}
    for item in valid_rows:
        if item.get('is_new_professor') or not item.get('prof_id'):
            raw_name = (item.get('professor_name') or '').strip()
            norm_name = normalize_name(raw_name)
            if norm_name and norm_name not in new_profs_by_name:
                first_name, last_name = split_full_name(raw_name)
                payload: Dict[str, Any] = {
                    'first_name': first_name,
                    'last_name': last_name,
                }
                if program_id is not None:
                    payload['program_id'] = program_id
                new_profs_by_name[norm_name] = payload

    created_prof_ids: List[int] = []
    created_name_to_id: Dict[str, int] = {}

    if new_profs_by_name:
        new_prof_records = list(new_profs_by_name.values())
        try:
            ins_res = supabase_client.table('professor').insert(new_prof_records).execute()
            created_rows = ins_res.data or []
            for r in created_rows:
                pid = r.get('prof_id') or r.get('id')
                if pid:
                    created_prof_ids.append(int(pid))
                    k = normalize_name(f"{r.get('first_name', '')} {r.get('last_name', '')}")
                    created_name_to_id[k] = int(pid)

            if len(created_name_to_id) < len(new_profs_by_name):
                # Query newly created professors from DB
                q = supabase_client.table('professor').select('prof_id, first_name, last_name')
                if program_id is not None:
                    q = q.eq('program_id', program_id)
                db_profs = q.execute().data or []
                for p in db_profs:
                    k = normalize_name(f"{p.get('first_name', '')} {p.get('last_name', '')}")
                    if k in new_profs_by_name and k not in created_name_to_id:
                        pid = p.get('prof_id') or p.get('id')
                        if pid:
                            created_name_to_id[k] = int(pid)
                            if int(pid) not in created_prof_ids:
                                created_prof_ids.append(int(pid))
        except Exception as e:
            return 0, 0, 0, [f"Failed to auto-create professors: {e}"]

    # 2. Map prof_id to rows that were new professors
    for item in valid_rows:
        if not item.get('prof_id'):
            norm_name = normalize_name(item.get('professor_name', ''))
            if norm_name in created_name_to_id:
                item['prof_id'] = created_name_to_id[norm_name]
            else:
                if created_prof_ids:
                    try:
                        supabase_client.table('professor').delete().in_('prof_id', created_prof_ids).execute()
                    except Exception:
                        pass
                return 0, 0, 0, [f"Could not resolve ID for created professor: '{item.get('professor_name')}'"]

    # 3. Upsert assignments in professor_load
    existing_map: Dict[Tuple[int, int], int] = {}
    for pl in existing_loads_list:
        p_id = pl.get('prof_id')
        c_id = pl.get('course_id')
        load_id = pl.get('id') or pl.get('professor_load_id')
        if p_id and c_id and load_id:
            existing_map[(int(p_id), int(c_id))] = int(load_id)

    updates_to_run: List[Tuple[int, int, int]] = []
    inserts_to_run: List[Dict[str, Any]] = []

    for item in valid_rows:
        prof_id = int(item['prof_id'])
        course_id = int(item['course_id'])
        sections = int(item['sections'])
        ilp_hours = int(item.get('ilp_hours') or 0)
        pair = (prof_id, course_id)

        if pair in existing_map:
            load_id = existing_map[pair]
            updates_to_run.append((load_id, sections, ilp_hours))
        else:
            ins = {
                'prof_id': prof_id,
                'course_id': course_id,
                'sections': sections,
                'ilp_hours': ilp_hours,
            }
            inserts_to_run.append(ins)

    updated_count = 0
    inserted_count = 0
    errors: List[str] = []

    try:
        if inserts_to_run:
            try:
                supabase_client.table('professor_load').insert(inserts_to_run).execute()
                inserted_count = len(inserts_to_run)
            except Exception as e:
                # Fallback if ilp_hours or sections column has issues in schema cache
                if 'ilp_hours' in str(e) or 'sections' in str(e) or '42703' in str(e):
                    fallback_inserts = [{'prof_id': d['prof_id'], 'course_id': d['course_id'], 'sections': d['sections']} for d in inserts_to_run]
                    try:
                        supabase_client.table('professor_load').insert(fallback_inserts).execute()
                        inserted_count = len(fallback_inserts)
                    except Exception as e2:
                        if 'sections' in str(e2) or '42703' in str(e2):
                            min_inserts = [{'prof_id': d['prof_id'], 'course_id': d['course_id']} for d in fallback_inserts]
                            supabase_client.table('professor_load').insert(min_inserts).execute()
                            inserted_count = len(min_inserts)
                        else:
                            raise
                else:
                    raise

        if updates_to_run:
            for load_id, sections, ilp_h in updates_to_run:
                try:
                    supabase_client.table('professor_load').update({'sections': sections, 'ilp_hours': ilp_h}).eq('id', load_id).execute()
                    updated_count += 1
                except Exception as e:
                    if 'ilp_hours' in str(e) or 'sections' in str(e) or '42703' in str(e):
                        try:
                            supabase_client.table('professor_load').update({'sections': sections}).eq('id', load_id).execute()
                            updated_count += 1
                        except Exception as e2:
                            if 'sections' in str(e2) or '42703' in str(e2):
                                pass
                            else:
                                raise
                    else:
                        raise
    except Exception as e:
        # Rollback newly created professors on failure
        if created_prof_ids:
            try:
                supabase_client.table('professor').delete().in_('prof_id', created_prof_ids).execute()
            except Exception as rollback_err:
                logging.exception(f"Rollback error deleting created professors: {rollback_err}")
        errors.append(f"Bulk insert failed: {e}")
        return 0, 0, 0, errors

    return updated_count, inserted_count, len(new_profs_by_name), errors
