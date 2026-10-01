import re
import os

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove active_fallback_profs and its initialization
content = re.sub(
    r'# Ensure dedicated fallback professor pool starting with "Professor A".*?active_fallback_profs = \[fallback_prof_obj\]',
    '',
    content,
    flags=re.DOTALL
)

# 2. Remove all_professors_pool fallback additions
content = re.sub(
    r'# Ensure initial fallback professor is in all_professors_pool if not already present.*?# Subject-Oriented Fallback Professor Assignment:',
    '# Subject-Oriented Fallback Professor Assignment:',
    content,
    flags=re.DOTALL
)

# 3. Remove Subject-Oriented Fallback Assignment logic
content = re.sub(
    r'# Subject-Oriented Fallback Professor Assignment:.*?# Fetch rooms',
    '# Fetch rooms',
    content,
    flags=re.DOTALL
)

# 4. Remove _is_fallback_prof definition
content = re.sub(
    r'def _is_fallback_prof\(prof\):.*?return any\(p\.get\(\'prof_id\'\) == prof\.get\(\'prof_id\'\) for p in active_fallback_profs\)',
    'def _is_fallback_prof(prof):\n            return False',
    content,
    flags=re.DOTALL
)

# 5. Remove _find_or_create_fallback_professor
content = re.sub(
    r'def _find_or_create_fallback_professor\(day, block_start, block_end, duration, course_id=None\):.*?return new_prof\n',
    '',
    content,
    flags=re.DOTALL
)

# 6. Simplify _schedule_single_session prof_pool setup
# content = re.sub(
#     r'            primary_prof_ids = {p\[\'prof_id\'\] for p in primary_profs}.*?(cand_rooms = .*?)\n.*?passes = \[',
#     '            \\1\n            passes = [',
#     content,
#     flags=re.DOTALL
# )
content = re.sub(
    r'                \{\'strict_rules\': True,  \'prof_pool\': \'primary\'\},\n                \{\'strict_rules\': True,  \'prof_pool\': \'all\'\},\n                \{\'strict_rules\': False, \'prof_pool\': \'primary\'\},\n                \{\'strict_rules\': False, \'prof_pool\': \'all\'\},\n            \]\n\n            for p_config in passes:\n                prof_pool = primary_profs if p_config\[\'prof_pool\'\] == \'primary\' else \(primary_profs \+ other_profs\)',
    '                {\'strict_rules\': True},\n                {\'strict_rules\': False},\n            ]\n\n            for p_config in passes:\n                prof_pool = primary_profs',
    content,
    flags=re.DOTALL
)

# 7. Remove all occurrences of _find_or_create_fallback_professor
content = re.sub(
    r'elif not is_real_faculty_mode and p_config\.get\(\'prof_pool\'\) == \'all\'.*?assigned_prof = _find_or_create_fallback_professor\(.*?course_id\)',
    '',
    content,
    flags=re.DOTALL
)

content = re.sub(
    r'elif not is_real_faculty_mode and p_config\[\'prof_pool\'\] == \'all\' and \(not primary_profs or not p_config\[\'strict_rules\'\]\):.*?assigned_prof = _find_or_create_fallback_professor\(day, block_start, block_end, duration, course_id\)',
    '',
    content,
    flags=re.DOTALL
)

content = re.sub(
    r'elif not is_real_faculty_mode and p_config\[\'prof_pool\'\] == \'all\' and \(not primary_profs or not p_config\[\'strict_rules\'\]\):.*?assigned_prof = _find_or_create_fallback_professor\(day, lec_start, lab_end, total_dur, course_id\)',
    '',
    content,
    flags=re.DOTALL
)


content = re.sub(
    r'                        # Fallback to sequential conflict-free professor.*?assigned_prof = _find_or_create_fallback_professor\(day, block_start, block_end, duration, course_id\)',
    '                        assigned_prof = None',
    content,
    flags=re.DOTALL
)

content = re.sub(
    r'                        # Fallback to sequential conflict-free professor.*?assigned_prof = _find_or_create_fallback_professor\(day, lec_start, lab_end, total_dur, course_id\)',
    '                        assigned_prof = None',
    content,
    flags=re.DOTALL
)

# Remove full grid relaxation fallback profs logic
content = re.sub(
    r'                    regular_profs = \[p for p in \(primary_profs \+ other_profs\) if not _is_fallback_prof\(p\)\]',
    '                    regular_profs = primary_profs',
    content,
    flags=re.DOTALL
)

content = re.sub(
    r'                    regular_profs = \[p for p in \(primary_profs \+ other_profs\) if not _is_fallback_prof\(p\)\]',
    '                    regular_profs = primary_profs',
    content,
    flags=re.DOTALL
)

# Same for paired block
# content = re.sub(
#     r'            primary_prof_ids = {p\[\'prof_id\'\] for p in primary_profs}.*?(cand_rooms = .*?)\n.*?passes = \[',
#     '            \\1\n            passes = [',
#     content,
#     flags=re.DOTALL
# )

# Write to file
with open('app_new.py', 'w', encoding='utf-8') as f:
    f.write(content)
