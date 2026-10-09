import os, sys
sys.path.insert(0, os.path.abspath('.'))
import dotenv
from collections import Counter
import json

dotenv.load_dotenv('.env')
import app

auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

def get_professor_key(load):
    if not load:
        return ""
    pkey = load.get('professor_key')
    if pkey:
        return pkey.strip().lower()
    pname = load.get('professor_name') or load.get('name') or ""
    return app.professor_load_importer.normalize_professor_key(pname)

def get_professor_name(load):
    if not load:
        return ""
    pname = load.get('professor_name') or load.get('name')
    if pname:
        return pname.strip()
    return ""

print("Test get_professor_key and get_professor_name helpers defined.")
