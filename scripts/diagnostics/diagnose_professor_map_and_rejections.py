import os
import sys
import json
from collections import Counter
from flask import session
import dotenv

sys.path.insert(0, r"c:\Users\Administrator\Oct9capstone\ClassScheduling")
dotenv.load_dotenv(r"c:\Users\Administrator\Oct9capstone\ClassScheduling\.env")
import app

# Authenticate supabase client
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

with app.app.test_request_context('/generate_schedule', method='POST', data={'semester': '2nd Semester'}):
    session['program'] = 'BSIT'
    session['user_id'] = 1
    session['role'] = 'Scheduler'
    session['jwt_token'] = auth_res.session.access_token
    session['refresh_token'] = auth_res.session.refresh_token
    
    # Fetch professor_load directly as app.py does
    pc_res = app.supabase.table('professor_load').select('*').execute()
    loads = pc_res.data or []
    
    print(f"Total professor_load rows fetched: {len(loads)}")
    
    # Check what columns exist on loads
    if loads:
        print(f"Sample load row keys: {list(loads[0].keys())}")
        
    pkeys = set()
    pnames = set()
    pids = set()
    for l in loads:
        pkeys.add(l.get('professor_key'))
        pnames.add(l.get('professor_name'))
        pids.add(l.get('prof_id'))
        
    print(f"Distinct non-null professor_key in DB: {len([k for k in pkeys if k])}")
    print(f"Distinct non-null professor_name in DB: {len([n for n in pnames if n])}")
    print(f"Distinct prof_id values in DB: {pids}")

    # Inspect distinct professor_keys
    print("Distinct professor_keys:", sorted([k for k in pkeys if k]))
