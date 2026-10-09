import sys, os
sys.path.insert(0, os.path.abspath('.'))
import dotenv
from collections import Counter
import json

dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Run reproduction script or simulate generation to trace rejections
with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['program'] = 'BSIT'
    app.session['user_id'] = 1
    app.session['role'] = 'Scheduler'
    app.session['jwt_token'] = auth_res.session.access_token
    app.session['refresh_token'] = auth_res.session.refresh_token

    # Let's run generate_schedule() directly!
    resp = app.generate_schedule()
    
    # Check session['unscheduled_loads']
    unsched = app.session.get('unscheduled_loads', [])
    print(f"Total unscheduled loads: {len(unsched)}")
    for u in unsched:
        print(f"  Load ID: {u.get('professor_load_id')}, Prof: {u.get('professor')}, Course: {u.get('course')}, Section: {u.get('section')}, Placed: {u.get('placed')}/{u.get('required')}")
        print(f"    Reason: {u.get('reason')}")
