import os, sys
sys.path.insert(0, os.path.abspath('.'))
import dotenv
from datetime import timedelta
from collections import Counter
import json

dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Run simulation and capture rejected slot evaluations for the 7 loads
with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['user_id'] = 1
    app.session['role'] = 'scheduler'
    app.session['program'] = 'BSIT'
    app.session['program_id'] = 1
    app.session['access_token'] = auth_res.session.access_token
    app.session['jwt_token'] = auth_res.session.access_token

    # Let's see what loads are unscheduled
    app.generate_schedule()
    unsched = app.session.get('unscheduled_loads', [])
    print(f"Total unscheduled in current app: {len(unsched)}")
    for u in unsched:
        print(f"Load ID {u['professor_load_id']}: Prof '{u['professor']}' | Course '{u['course']}' | Sec '{u['section']}' | {u['placed']}/{u['required']}")
