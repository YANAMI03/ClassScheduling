import os
import sys
import json
import dotenv

sys.path.insert(0, os.path.abspath('.'))
dotenv.load_dotenv('.env')

import app

# Sign in as admin and set auth on app.supabase
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

res = app.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
print("Valid:", res.get('valid'))
print("Errors:", res.get('errors'))
print("Warnings:", res.get('warnings'))

for b in res.get('breakdown', []):
    print(f"\n--- Year {b['year_level']} ({b['year_label']}) ---")
    print(f"Is specialized: {b.get('is_specialized')}")
    if not b.get('is_specialized'):
        print(f"Section count: {b.get('section_count')}")
        print(f"Section names: {b.get('section_names')}")
        for c in b.get('courses', []):
            print(f"    Course: {c['course_code']} -> sections: {c['sections']}")
    else:
        print(f"General required: {b.get('general_total_required')}")
        for grp in b.get('specialization_groups', []):
            print(f"  Spec: {grp['specialization']}, Count: {grp['section_count']}, Sections: {grp['section_names']}")
            for c in grp['courses']:
                print(f"    Course: {c['course_code']} -> sections: {c['sections']}")
        print("  General Courses:")
        for gc in b.get('general_courses', []):
            print(f"    Course: {gc['course_code']} -> sections: {gc['sections']}")
