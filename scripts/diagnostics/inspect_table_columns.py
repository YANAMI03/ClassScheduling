import sys, os
sys.path.insert(0, os.path.abspath('.'))
import dotenv
import app

dotenv.load_dotenv('.env')
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Let's inspect a single row of professor_load in detail
row = app.supabase.table('professor_load').select('*').limit(1).execute().data[0]
print("Row columns in professor_load:", list(row.keys()))
print("Sample row:", row)

# Let's check schedule table columns as well!
sched_res = app.supabase.table('schedule').select('*').limit(1).execute().data
if sched_res:
    print("Row columns in schedule:", list(sched_res[0].keys()))
else:
    print("Schedule table currently has 0 rows (as expected for dry-run).")
