import os
import sys
import copy
from datetime import timedelta
from collections import Counter
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Let's test the generator logic directly
with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    app.session['user_id'] = 1
    app.session['role'] = 'scheduler'
    app.session['program'] = 'BSIT'
    app.session['program_id'] = 1
    app.session['access_token'] = auth_res.session.access_token

    # Let's test with monkey-patching app.py in memory
    orig_table = app.supabase.table
    
    # We want to see: if we fix prof_track_affinity and unscheduled_loads in app.py, what happens?
    # Let's inspect app.py around lines 10385-10425 and lines 10555-10605
    print("Ready to test.")
