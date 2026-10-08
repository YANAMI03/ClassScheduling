import os
import sys
import subprocess
import tempfile
import dotenv

dotenv.load_dotenv('.env')

commits_to_test = [
    'ad3c678',
    'bf13370',
    '7bbda3a',
    'c97d72b',
    '34f810a',
    '29b09b5',
    'd71dd41',
    'aba2e75'
]

repo_root = os.path.abspath('.')

for commit in commits_to_test:
    print(f"\n================ Testing commit {commit} ================")
    cmd = ["git", "show", f"{commit}:app.py"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    if proc.returncode != 0:
        print(f"Error getting app.py at {commit}: {proc.stderr}")
        continue
    
    app_code = proc.stdout
    temp_dir = tempfile.mkdtemp()
    temp_app_path = os.path.join(temp_dir, "app.py")
    with open(temp_app_path, "w", encoding='utf-8') as f:
        f.write(app_code)
        
    runner_code = f"""
import os
import sys
import dotenv

sys.path.insert(0, {repr(temp_dir)})
sys.path.insert(1, {repr(repo_root)})
dotenv.load_dotenv({repr(os.path.join(repo_root, '.env'))})

try:
    import app
    # Set templates folder to repo root templates
    app.app.template_folder = {repr(os.path.join(repo_root, 'templates'))}
    test_client = app.app.test_client()
    auth_res = app.supabase.auth.sign_in_with_password({{'email': 'admin@example.com', 'password': 'password123'}})
    app.supabase.postgrest.auth(auth_res.session.access_token)

    with test_client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'scheduler'
        sess['program'] = 'BSIT'
        sess['program_id'] = 1
        sess['access_token'] = auth_res.session.access_token

    response = test_client.post('/generate_schedule', data={{'semester': '2nd Semester'}}, follow_redirects=False)
    with test_client.session_transaction() as sess:
        unscheduled = sess.get('unscheduled_loads', [])
        preview_id = sess.get('preview_id')
        if hasattr(app, '_get_preview_for_user'):
            preview = app._get_preview_for_user(user_id=1, preview_id=preview_id)
        else:
            preview = sess.get('schedule_preview', [])

    print(f"STATUS={{response.status_code}} PREVIEW={{len(preview)}} UNSCHEDULED={{len(unscheduled)}}")
    for u in unscheduled:
        print(f"UNSCHED_ROW: {{u.get('professor')}} | {{u.get('course')}} | {{u.get('section')}} | {{u.get('placed')}}/{{u.get('required')}}")
except Exception as e:
    import traceback
    print(f"EXCEPTION: {{e}}")
    traceback.print_exc()
"""
    runner_path = os.path.join(temp_dir, "runner.py")
    with open(runner_path, "w", encoding='utf-8') as f:
        f.write(runner_code)
        
    run_proc = subprocess.run([sys.executable, runner_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    out = run_proc.stdout
    err = run_proc.stderr
    print(f"Commit {commit} output:")
    for line in out.splitlines():
        if any(tag in line for tag in ['STATUS=', 'PREVIEW=', 'UNSCHEDULED=', 'UNSCHED_ROW:', 'EXCEPTION:']):
            print("  ", line)
    if err and "Traceback" in err:
        print("  Stderr traceback:\n" + "\n".join(err.splitlines()[-5:]))

