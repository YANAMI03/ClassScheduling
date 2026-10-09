import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
import app
import dotenv
dotenv.load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env')))

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

client = app.app.test_client()

runs = []
for i in range(1, 4):
    with client.session_transaction() as sess:
        sess['user_id'] = 'scheduler-user-id'
        sess['username'] = 'scheduler_tester'
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1

    resp = client.post('/generate_schedule', data={'semester': '2nd Semester', 'number_of_sections': '1'}, follow_redirects=True)
    with client.session_transaction() as sess:
        unsched = sess.get('unscheduled_loads', [])
        prev = app._get_preview_for_user(sess.get('user_id'), sess.get('preview_id'))
    
    html = resp.data.decode('utf-8')
    prof_none_in_html = 'Prof #None' in html or 'Prof #' in html
    prof_none_in_unsched = any('Prof #' in str(u.get('professor')) or u.get('professor') is None for u in unsched)
    prof_none_in_prev = any('Prof #' in str(e.get('professor_name')) for e in prev)
    
    unsched_profs = [u.get('professor') for u in unsched]
    unsched_courses = [u.get('course') for u in unsched]
    
    runs.append({
        'run': i,
        'preview_count': len(prev),
        'unscheduled_count': len(unsched),
        'prof_none_in_html': prof_none_in_html,
        'prof_none_in_unsched': prof_none_in_unsched,
        'prof_none_in_prev': prof_none_in_prev,
        'unscheduled_profs': unsched_profs,
        'unscheduled_courses': unsched_courses,
    })

print('=== 3 DRY RUN GENERATION RESULTS ===')
for r in runs:
    print(f"Run {r['run']}: Preview={r['preview_count']}, Unscheduled={r['unscheduled_count']}, Prof# in HTML={r['prof_none_in_html']}, Prof# in Unscheduled={r['prof_none_in_unsched']}")
    print(f"  Unscheduled Faculty: {r['unscheduled_profs']}")

det_preview = (runs[0]['preview_count'] == runs[1]['preview_count'] == runs[2]['preview_count'])
det_unsched = (runs[0]['unscheduled_profs'] == runs[1]['unscheduled_profs'] == runs[2]['unscheduled_profs'])
print(f"Deterministic Preview Count: {det_preview} ({runs[0]['preview_count']})")
print(f"Deterministic Unscheduled: {det_unsched}")

active_sched_count = len(app.supabase.table('schedule').select('schedule_id').eq('archive', False).execute().data or [])
print(f"Active Schedule Rows in DB: {active_sched_count} (preview preserved, no DB writes)")
