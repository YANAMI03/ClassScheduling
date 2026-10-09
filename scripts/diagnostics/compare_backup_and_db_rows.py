import os, sys
sys.path.insert(0, os.path.abspath('.'))
import json
import dotenv
dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

# Load backup
with open('backups/professor_load_backup_latest.json', 'r', encoding='utf-8') as f:
    bdata = json.load(f)

print("Backup created at:", bdata.get('created_at'))
b_rows = bdata.get('professor_load_rows', [])
print("Backup rows count:", len(b_rows))
print("Sample backup rows:")
for r in b_rows[:5]:
    print(" ", r)

# Fetch current professor_load
db_rows = app.supabase.table('professor_load').select('*').order('id').execute().data or []
print("Current DB rows count:", len(db_rows))
print("Sample DB rows:")
for r in db_rows[:5]:
    print(" ", r)
