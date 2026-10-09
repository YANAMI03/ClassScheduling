import app
auth=app.supabase.auth.sign_in_with_password({'email':'admin@example.com','password':'password123'})
app.supabase.postgrest.auth(auth.session.access_token)
with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['user_id'] = 1
    app.session['program'] = 'BSIT'
    app.session['role'] = 'scheduler'
    app.session['program_id'] = 1
    pc_res = app.supabase.table('professor_load').select('professor_load_id:id, course_id, sections, professor_name').eq('program_id', 1).execute()
    pc_data = pc_res.data or []
    for r in pc_data:
        r['professor_key'] = app.get_professor_key(r)
    baseline_order = app._get_baseline_professor_load_order()
    def _get_pc_sort_key(row):
        cid = int(row.get('course_id') or 0)
        pkey = row.get('professor_key') or app.get_professor_key(row)
        order_rank = baseline_order.get((pkey, cid))
        if order_rank is None and row.get('prof_id'):
            order_rank = baseline_order.get((int(row['prof_id']), cid))
        if order_rank is None:
            order_rank = 9999
        return (order_rank, -int(row.get('sections') or 1), pkey or '', int(row.get('professor_load_id') or row.get('id') or 0))
    pc_data.sort(key=_get_pc_sort_key)
    for r in pc_data:
        if r['course_id'] in [50, 51, 58, 59, 60]:
            lid = r.get('professor_load_id') or r.get('id')
            print(f"Course {r['course_id']}: {r['professor_name']:<30} (ID {lid}, sec {r['sections']})")
