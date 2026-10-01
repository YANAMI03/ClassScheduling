import os, subprocess, re, shutil

def main():
    subprocess.run(['git', 'checkout', 'app.py'], check=True)

    with open('scripts/rewrite_fallback.py', 'r', encoding='utf-8') as f:
        rf = f.read()
    
    # We remove the regex from rewrite_fallback.py that deletes primary_prof_ids and cand_rooms!
    rf = re.sub(
        r"content = re\.sub\(\s*r'            primary_prof_ids = \{p\['prof_id'\] for p in primary_profs\}.*?\(cand_rooms = .*?\).*?passes = \[',\s*'            \\1\\n            passes = \[',\s*content,\s*flags=re\.DOTALL\s*\)",
        "",
        rf,
        flags=re.DOTALL
    )

    # Let's also do the same for the paired block which might be deleted!
    rf = re.sub(
        r"content = re\.sub\(\s*r'            primary_prof_ids = \{p\['prof_id'\] for p in primary_profs\}.*?passes = \[',\s*'            passes = \[',\s*content,\s*flags=re\.DOTALL\s*\)",
        "",
        rf,
        flags=re.DOTALL
    )

    with open('scripts/rewrite_fallback.py', 'w', encoding='utf-8') as f:
        f.write(rf)

    subprocess.run(['python', 'scripts/rewrite_fallback.py'], check=True)
    os.replace('app_new.py', 'app.py')

    with open('scripts/rewrite_fallback_2.py', 'r', encoding='utf-8') as f:
        rf2 = f.read()
    rf2 = rf2.replace("'pass'", "''")
    with open('scripts/rewrite_fallback_2.py', 'w', encoding='utf-8') as f:
        f.write(rf2)

    subprocess.run(['python', 'scripts/rewrite_fallback_2.py'], check=True)
    os.replace('app_new_2.py', 'app.py')

    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Remove unindented pass
    content = content.replace('\npass\n', '\n\n')

    # Remove fallback helpers block
    content = re.sub(
        r'#-------------------------------------------------------FALLBACK_PROFESSOR_HELPERS----------------------------------------------------------------------------------------------\n.*?#-------------------------------------------------------SCHEDULING LOGIC----------------------------------------------------------------------------------------------',
        '#-------------------------------------------------------SCHEDULING LOGIC----------------------------------------------------------------------------------------------',
        content,
        flags=re.DOTALL
    )

    # Prepend ILP
    content = re.sub(
        r'    if ilp_hours == 1:\n        queue\.append\(\{\'paired\': False, \'session_type\': \'ILP\', \'duration\': 1, \'is_ilp\': True\}\)',
        '    if ilp_hours == 1:\n        queue.insert(0, {\'paired\': False, \'session_type\': \'ILP\', \'duration\': 1, \'is_ilp\': True})',
        content
    )

    # Skip first hour for non-ILP
    content = re.sub(
        r'                        if _has_conflict\(day, block_start, block_end, section_bookings\[sec_key\]\):\n                            continue',
        '                        if _has_conflict(day, block_start, block_end, section_bookings[sec_key]):\n                            continue\n                        if block_start < timedelta(hours=8) and session_type != \'ILP\':\n                            continue',
        content
    )
    content = re.sub(
        r'                        if _has_conflict\(day, lec_start, lab_end, section_bookings\[sec_key\]\):\n                            continue',
        '                        if _has_conflict(day, lec_start, lab_end, section_bookings[sec_key]):\n                            continue\n                        if lec_start < timedelta(hours=8):\n                            continue',
        content
    )

    # Remove lingering p_config['prof_pool'] == 'all'
    content = re.sub(
        r'                        elif p_config\[\'prof_pool\'\] == \'all\'.*?assigned_prof = _find_or_create_fallback_professor\(day, block_start, block_end, duration, course_id\)',
        '',
        content,
        flags=re.DOTALL
    )
    content = re.sub(
        r'                        elif p_config\[\'prof_pool\'\] == \'all\'.*?assigned_prof = _find_or_create_fallback_professor\(day, lec_start, lab_end, total_dur, course_id\)',
        '',
        content,
        flags=re.DOTALL
    )

    with open('app.py', 'w', encoding='utf-8') as f:
        f.write(content)

    subprocess.run(['python', 'scripts/rework_ilp.py'], check=True)
    subprocess.run(['python', 'scripts/rework_lecture_fallback.py'], check=True)

    subprocess.run(['python', '-m', 'py_compile', 'app.py'], check=True)

if __name__ == '__main__':
    main()
