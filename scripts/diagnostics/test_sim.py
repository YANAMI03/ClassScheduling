import os
import sys
import copy
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app

# Authenticate supabase
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Let's inspect what happens if we simulate generation with the backup's section_course_assignment
# or with the backup's order of loads
from scripts.diagnostics import reproduce_generation

# Let's check what reproduce_generation does
print("Running simulation with current DB...")
res = reproduce_generation.simulate_generation()
print(f"Current DB unscheduled: {len(res['unscheduled_loads'])}")
for u in res['unscheduled_loads']:
    print(f"  | {u['professor']} | {u['course']} | {u['section']} | {u['placed']} / {u['required']} | {u['reason']} |")
