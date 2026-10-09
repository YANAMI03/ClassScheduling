import sys, os
sys.path.insert(0, os.path.abspath('.'))
import dotenv
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['program'] = 'BSIT'
    app.session['user_id'] = 1
    app.session['role'] = 'Scheduler'
    app.session['jwt_token'] = auth_res.session.access_token
    app.session['refresh_token'] = auth_res.session.refresh_token

    # Fetch data as in generate_schedule
    pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, sections, professor_name').execute()
    pc_data = pc_res.data or []
    for r in pc_data:
        r['professor_key'] = app.professor_load_importer.normalize_professor_key(r.get('professor_name') or '')

    # Distinct professors
    pkeys = set(r['professor_key'] for r in pc_data)
    print(f"Distinct professor_key count: {len(pkeys)}")
    
    # Check baseline order keys
    base_order = app._get_baseline_professor_load_order()
    print("Baseline order sample keys:", list(base_order.keys())[:10])
    
    # Let's inspect prof_track_affinity with (l['prof_id'], c_yl) vs (l['professor_key'], c_yl)
    all_courses = app.supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', '2nd Semester').eq('program_id', 1).execute().data or []
    
    prof_track_affinity_old = {}
    prof_track_affinity_new = {}
    
    for c in all_courses:
        c_yl = int(c.get('year_level') or 1)
        c_spec = c.get('specialization') or c.get('major')
        if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
            for r in pc_data:
                if r.get('course_id') == c.get('course_id'):
                    prof_track_affinity_old[(r.get('prof_id'), c_yl)] = str(c_spec).strip()
                    prof_track_affinity_new[(r.get('professor_key'), c_yl)] = str(c_spec).strip()

    print("Old prof_track_affinity (with prof_id):", prof_track_affinity_old)
    print("New prof_track_affinity (with professor_key):", prof_track_affinity_new)
