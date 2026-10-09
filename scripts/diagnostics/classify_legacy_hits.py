import json
import os

with open('scripts/diagnostics/legacy_prof_scan.json', 'r', encoding='utf-8') as f:
    hits = json.load(f)

with open('app.py', 'r', encoding='utf-8') as f:
    app_lines = f.readlines()

def get_app_func(line_no):
    for i in range(line_no - 1, -1, -1):
        l = app_lines[i]
        if l.startswith('def ') or l.startswith('@app.route'):
            return l.strip()
    return "module"

categories = {
    'a_generator_data_loading': [],
    'b_conflict_occupancy': [],
    'c_per_prof_hours_40h': [],
    'd_load_ordering_tiebreaks': [],
    'e_unscheduled_toasts': [],
    'f_confirm_insert_payload': [],
    'g_revalidation_restore_manual_conflicts': [],
    'h_schedule_pages_room_utilization': [],
    'i_pdf_exports_urls': [],
    'j_archive_list_view': [],
    'k_backup_restore': [],
    'l_activity_log': [],
    'm_frontend_templates_js': [],
    'other': []
}

for h in hits:
    f = h['file']
    line = h['line']
    content = h['content']
    
    # Ignore diagnostics, backups, migrations, scratch scripts, tests for core app categorization
    if f.startswith('backups') or f.startswith('scripts') or f.startswith('migrations') or f.startswith('tests'):
        continue
        
    if f.startswith('templates') or f.endswith('.js'):
        categories['m_frontend_templates_js'].append(h)
    elif f == 'app.py':
        func = get_app_func(line)
        h['func'] = func
        
        # Generator lines (approx 9600 to 10650)
        if 'generate_schedule' in func or (line >= 9608 and line <= 10650):
            if any(k in content for k in ['pc_res', 'all_profs_res', '_all_profs_by_id', 'all_professors_pool', 'professors_by_course']):
                categories['a_generator_data_loading'].append(h)
            elif any(k in content for k in ['_get_pc_sort_key', 'baseline_order', 'prof_track_affinity', 'order_rank']):
                categories['d_load_ordering_tiebreaks'].append(h)
            elif any(k in content for k in ['unscheduled_loads', 'Prof #', 'assigned_secs', 'reason_desc', 'sched_by_lid']):
                categories['e_unscheduled_toasts'].append(h)
            elif any(k in content for k in ['professor_hours', 'total_faculty_capacity', 'active_prof_ids', 'prof_util_pct', 'max_hours']):
                categories['c_per_prof_hours_40h'].append(h)
            elif any(k in content for k in ['professor_bookings', 'prof_scheduled_days', 'prof_day_hours', '_check_professor_cutoff', '_can_prof_teach_on_day', 'existing_rows']):
                categories['b_conflict_occupancy'].append(h)
            else:
                categories['a_generator_data_loading'].append(h)
        elif 'confirm_schedule' in func or line in range(10650, 11000):
            categories['f_confirm_insert_payload'].append(h)
        elif any(k in func for k in ['edit_schedule', 'restore_schedule', '_check_professor_cutoff_conflict', 'check_conflict']):
            categories['g_revalidation_restore_manual_conflicts'].append(h)
        elif any(k in func for k in ['view_professor_schedule', 'view_room_schedule', 'schedules', 'export_section', 'calculate_semester_section_counts', '_build_preview_context']):
            categories['h_schedule_pages_room_utilization'].append(h)
        elif any(k in func for k in ['export_professor_schedule_pdf', 'export_professor_schedule', 'export_room_schedule_pdf', 'export_section_schedule_pdf', 'pdf']):
            categories['i_pdf_exports_urls'].append(h)
        elif 'archive' in func:
            categories['j_archive_list_view'].append(h)
        elif 'backup' in func or 'restore' in func:
            categories['k_backup_restore'].append(h)
        elif 'activity' in func or 'log_activity' in content:
            categories['l_activity_log'].append(h)
        else:
            categories['other'].append(h)
    else:
        categories['other'].append(h)

for cat, items in categories.items():
    print(f"[{cat}]: {len(items)} hits")
    for it in items[:5]:
        print(f"   {it['file']}:{it['line']} ({it.get('func', '')}) -> {it['content'][:80]}")
    if len(items) > 5:
        print(f"   ... and {len(items)-5} more")
    print()

with open('scripts/diagnostics/classified_hits.json', 'w', encoding='utf-8') as f:
    json.dump(categories, f, indent=2)
