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

# We want to trace candidate evaluations for the sessions that fail
# Let's inspect which sections/courses failed:
# IT-NET05 (course_id 60), IT-IAS02 (course_id 50), IT-NET04 (course_id 59)
# Let's run generation and hook into _schedule_single_session / _schedule_paired_block

print("Starting trace...")
# We will inspect app.py's internal state during generation by subclassing or logging
