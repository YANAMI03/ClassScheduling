import os
import sys
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# We want to see: during the real generation, when a session cannot be placed in _schedule_single_session,
# what were all the reasons candidate blocks were rejected?
# Let's inspect the 7 unscheduled sessions.
with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['user_id'] = 1
    app.session['role'] = 'scheduler'
    app.session['program'] = 'BSIT'
    app.session['program_id'] = 1
    app.session['access_token'] = auth_res.session.access_token

    # Let's run generation and capture warnings
    resp = app.generate_schedule()
    unsched = app.session.get('unscheduled_loads', [])
    print(f"Total unscheduled: {len(unsched)}")
    for u in unsched:
        print(f"  {u['professor']} | {u['course']} | {u['section']} | {u['placed']}/{u['required']} | {u['reason']}")
