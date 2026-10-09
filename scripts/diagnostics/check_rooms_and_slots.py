import os
import sys
import dotenv

sys.path.insert(0, '.')
dotenv.load_dotenv('.env')

import app
auth_res = app.supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
app.supabase.postgrest.auth(auth_res.session.access_token)

rooms = app.supabase.table('room').select('*').execute().data or []
print(f"Total rooms: {len(rooms)}")
for r in rooms:
    print(f"  id={r.get('room_id')}, name={r.get('room_name')}, type={r.get('room_type')}")
