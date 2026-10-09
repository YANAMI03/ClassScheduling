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

    pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, sections, professor_name').execute()
    pc_data = pc_res.data or []
    for r in pc_data:
        r['professor_key'] = app.professor_load_importer.normalize_professor_key(r.get('professor_name') or '')

    base_order = app._get_baseline_professor_load_order()
    
    missing_baseline = []
    for r in pc_data:
        cid = int(r.get('course_id') or 0)
        pkey = r.get('professor_key')
        rank = base_order.get((pkey, cid))
        if rank is None:
            missing_baseline.append((pkey, cid, r.get('sections'), r.get('professor_name')))
            
    print(f"Total loads: {len(pc_data)}, missing baseline order: {len(missing_baseline)}")
    for m in missing_baseline:
        print("  Missing:", m)
