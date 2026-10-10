import re
import os

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix edit_schedule_entry fallback professor creation
content = re.sub(
    r'elif professor_name\.upper\(\) in \(\'TBA\', \'PROFESSOR A\'\):.*?target_prof_id = fallback_p\.get\(\'prof_id\'\)',
    'elif professor_name.upper() in (\'TBA\', \'PROFESSOR A\'):\n                    target_prof_id = None',
    content,
    flags=re.DOTALL
)
content = re.sub(
    r'if not target_prof_id and professor_name\.lower\(\) in \(\'professor a\', \'tba\'\):.*?target_prof_id = fallback_p\.get\(\'prof_id\'\)',
    'if not target_prof_id and professor_name.lower() in (\'professor a\', \'tba\'):\n                        target_prof_id = None',
    content,
    flags=re.DOTALL
)

# Replace all calls to _is_fallback_prof to return False or remove logic
content = re.sub(r'def _is_fallback_prof\(prof\):.*?return False', 'def _is_fallback_prof(prof):\n            return False', content, flags=re.DOTALL)

# In _commit_ilp_entry, remove fallback logging
content = re.sub(
    r'if is_fallback or \(prof and _is_fallback_prof\(prof\)\):.*?block_start\}-\{block_end\}\"\)',
    '',
    content,
    flags=re.DOTALL
)

# Remove the fallback professor from ILP attempt 3
content = re.sub(
    r'# 3\. Third attempt: Fallback professor pool.*?return _commit_ilp_entry\(day, slot, fb_prof, is_fallback=True\)',
    '',
    content,
    flags=re.DOTALL
)

# ILP final safety net:
content = re.sub(
    r'# 4\. Final safety net:.*?fallback_prof = _ensure_fallback_professor_by_index\(0, department\).*?return _commit_ilp_entry\(default_day, default_slot, fallback_prof, is_fallback=True\)',
    '# 3. Final safety net: Section was booked at all available slots, assign to earliest priority slot with TBA\n            default_day, default_slot = first_hour_early_slots[0] if first_hour_early_slots else (last_hour_slots_raw[0] if last_hour_slots_raw else (\'Monday\', {\'start_time\': timedelta(hours=7), \'end_time\': timedelta(hours=8)}))\n            generation_warnings.append(\n                f"ILP for {course.get(\'course_code\')} could not be placed for Professor {desig_p.get(\'last_name\', \'\') if desig_p else \'X\'} and was set to TBA"\n            )\n            return _commit_ilp_entry(default_day, default_slot, None, is_fallback=True)',
    content,
    flags=re.DOTALL
)

# Remove fallback logging in single/paired block:
content = re.sub(
    r'                    if _is_fallback_prof\(assigned_prof\):.*?block_start\}-\{block_end\}\"\)',
    '',
    content,
    flags=re.DOTALL
)

with open('app_new_2.py', 'w', encoding='utf-8') as f:
    f.write(content)
