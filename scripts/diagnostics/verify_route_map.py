import sys, os
sys.path.insert(0, os.path.abspath('.'))
import app as app_module

# New Permission Matrix:
# Admin:
#   - Courses: YES, full (add/edit/delete)
#   - Rooms: YES, full (add/edit/delete)
#   - Timeslots: YES, full (add/edit/delete)
#   - Schedules: READ-ONLY (schedules, professor_schedule, room_schedule, schedule_archive views, export pdf)
#   - Users: YES, full
#   - Activity Log: YES
#   - Backup & Restore: YES
#   - Generate Schedule: NO (403)
#   - Professor Load: NO (403)
# Scheduler:
#   - Generate Schedule: YES (generate + confirm)
#   - Professor Load: YES, full (import, manual add, edit, delete)
#   - Schedules: YES, full (all views + write: edit, delete, archive, restore)
#   - Courses: NO (403) [except GET /api/courses for dropdowns]
#   - Rooms: NO (403)
#   - Timeslots: NO (403)
#   - Users: NO (403)
#   - Activity Log: NO (403)
#   - Backup & Restore: NO (403)
# Viewer:
#   - Schedules: READ-ONLY (schedules, professor_schedule, room_schedule + export pdf)
#   - Schedule Archive: NO (403)
#   - Everything else: NO (403)

ROUTE_MAP = {
    # Generate Schedule
    'home': {'scheduler'},
    'legacy_index_html': {'scheduler'},
    'generate_schedule': {'scheduler'},
    'confirm_preview': {'scheduler'},
    'discard_preview': {'scheduler'},
    'edit_preview_entry': {'scheduler'},
    'preview_schedule': {'scheduler'},
    'legacy_generated_schedule_html': {'scheduler'},
    'api_preview_entries': {'scheduler'},
    'api_semester_section_counts': {'scheduler'},
    'registrar_counts': {'scheduler'},
    'check_schedule_exists': {'scheduler'},

    # Professor Load
    'professor_load': {'scheduler'},
    'add_professor_load': {'scheduler'},
    'edit_professor_load': {'scheduler'},
    'delete_professor_load': {'scheduler'},
    'delete_professor_load_all': {'scheduler'},
    'update_prof_with_courses': {'scheduler'},
    'api_professor_load': {'scheduler'},
    'professor_load_import_template': {'scheduler'},
    'professor_load_import_preview': {'scheduler'},
    'professor_load_import_confirm': {'scheduler'},
    'professor_load_import_error_report': {'scheduler'},

    # Courses (Admin full, Scheduler 403)
    'show_courses': {'admin'},
    'legacy_courses_html': {'admin'},
    'add_course': {'admin'},
    'edit_course': {'admin'},
    'delete_course': {'admin'},
    'search_courses': {'admin'},
    # Helper GET endpoint for Scheduler dropdowns
    'api_courses': {'admin', 'scheduler'},

    # Rooms (Admin full, Scheduler 403)
    'rooms': {'admin'},
    'legacy_room_html': {'admin'},
    'add_room': {'admin'},
    'edit_room': {'admin'},
    'delete_room': {'admin'},
    'search_rooms': {'admin'},

    # Working hours (Admin full, Scheduler 403)
    'working_hours': {'admin'},
    'legacy_working_hours_html': {'admin'},
    'add_working_hours': {'admin'},
    'edit_working_hours': {'admin'},
    'delete_working_hours': {'admin'},
    'initialize_working_hours': {'admin'},

    # Schedules: Section Schedule (Admin READ-ONLY, Scheduler full, Viewer READ-ONLY)
    'schedules': {'admin', 'scheduler', 'viewer'},
    'legacy_schedules_html': {'admin', 'scheduler', 'viewer'},
    'view_schedule': {'admin', 'scheduler', 'viewer'},
    'legacy_schedule_html': {'admin', 'scheduler', 'viewer'},
    'legacy_section_html': {'admin', 'scheduler', 'viewer'},
    'export_section_schedule': {'admin', 'scheduler', 'viewer'},
    'export_section_schedule_pdf': {'admin', 'scheduler', 'viewer'},
    'api_section_availability': {'admin', 'scheduler', 'viewer'},
    'api_section_theme': {'admin', 'scheduler', 'viewer'},

    # Schedules: Professor Schedule (Admin READ-ONLY, Scheduler full, Viewer READ-ONLY)
    'professor_schedule': {'admin', 'scheduler', 'viewer'},
    'view_professor_schedule': {'admin', 'scheduler', 'viewer'},
    'export_professor_schedule': {'admin', 'scheduler', 'viewer'},
    'export_professor_schedule_pdf': {'admin', 'scheduler', 'viewer'},
    'api_professor_availability': {'admin', 'scheduler', 'viewer'},
    'api_professor_workload': {'admin', 'scheduler', 'viewer'},

    # Schedules: Room Schedule (Admin READ-ONLY, Scheduler full, Viewer READ-ONLY)
    'room_schedule': {'admin', 'scheduler', 'viewer'},
    'view_room_schedule': {'admin', 'scheduler', 'viewer'},
    'export_room_schedule': {'admin', 'scheduler', 'viewer'},
    'api_room_availability': {'admin', 'scheduler', 'viewer'},

    # Schedules: Schedule Archive VIEW (Admin READ-ONLY view, Scheduler full, Viewer 403)
    'schedule_archive': {'admin', 'scheduler'},
    'view_schedule_archive_batch': {'admin', 'scheduler'},

    # Schedules WRITE endpoints (Scheduler ONLY; Admin 403, Viewer 403)
    'archive_schedule': {'scheduler'},
    'edit_schedule': {'scheduler'},
    'edit_schedule_entry': {'scheduler'},
    'delete_schedule': {'scheduler'},
    'delete_section_schedule': {'scheduler'},
    'delete_all_schedules': {'scheduler'},
    'restore_schedule_archive': {'scheduler'},
    'delete_schedule_archive': {'scheduler'},

    # Users (Admin only)
    'users': {'admin'},
    'search_users': {'admin'},
    'create_user': {'admin'},
    'edit_user': {'admin'},
    'delete_user': {'admin'},
    'set_admin_role': {'admin'},
    'add_user_columns': {'admin'},
    'create_test_accounts': {'admin'},
    'approve_delete_request': {'admin'},
    'reject_delete_request': {'admin'},

    # Activity Log (Admin only)
    'activity_log': {'admin'},

    # Backup & Restore (Admin only)
    'backup_database': {'admin'},
    'restore_database': {'admin'},

    # Common Authenticated (All logged-in roles)
    'profile': {'admin', 'scheduler', 'viewer'},
    'upload_profile_picture': {'admin', 'scheduler', 'viewer'},
    'mark_notifications_read': {'admin', 'scheduler', 'viewer'},

    # Public / Auth
    'login': None,
    'signup': None,
    'logout': None,
    'static': None,

    # Irregular students (disabled / 404)
    'irregular_students': None,
    'assign_irregular_section': None,
    'manage_irregular_student_schedule': None,
    'unassign_irregular_section': None,
    'view_irregular_student_schedule': None,
    'delete_irregular_student': None,
}

all_eps = set(app_module.app.view_functions.keys())
mapped_eps = set(ROUTE_MAP.keys())

unmapped = all_eps - mapped_eps
print("Unmapped endpoints:", unmapped)
print(f"Total mapped: {len(mapped_eps)}, Total app endpoints: {len(all_eps)}")
