from flask import Flask, request, render_template, redirect, url_for, session, jsonify, flash, send_file
from datetime import timedelta, datetime
from werkzeug.utils import secure_filename
from functools import wraps
import os
import json
import tempfile
import logging
import base64
import time
import math
import random
import uuid

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client, Client
from postgrest.exceptions import APIError

SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
# Anon / public key only. `SUPABASE_PUBLISHABLE_KEY` is the newer name for the
# same anon key; both are accepted, the service_role secret key is NOT used.
SUPABASE_ANON_KEY: str = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError(
        "Missing Supabase configuration. Set the SUPABASE_URL and "
        "SUPABASE_ANON_KEY (or SUPABASE_PUBLISHABLE_KEY) environment variables before "
        "starting the app. Do NOT use the service_role secret key — RLS is enforced "
        "via each user's JWT."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Optional service-role client used ONLY for server-side auth admin operations
# (creating users). It is never used for data access — all table reads/writes
# still go through the `supabase` (anon) client above, so RLS is enforced.
admin_supabase = None
if os.environ.get("SUPABASE_SECRET_KEY"):
    admin_supabase = create_client(SUPABASE_URL, os.environ.get("SUPABASE_SECRET_KEY"))


def _admin_auth():
    if admin_supabase is None:
        raise RuntimeError(
            "Admin user management requires the SUPABASE_SECRET_KEY (service_role) "
            "to be set. This key is used only for auth admin operations, never for "
            "data access."
        )
    return admin_supabase.auth.admin

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'scheduler-secret-key')

# Simple scheduler app: manage courses, professors, rooms, and generated schedules

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)

def _first(data):
    """Return the first row of a Supabase response payload, or None."""
    return data[0] if data else None

def _rel(row, key):
    """Return an embedded related row (a dict, or a single-element list) or None."""
    if not isinstance(row, dict):
        return None
    val = row.get(key)
    if isinstance(val, dict):
        return val
    if isinstance(val, list) and val and isinstance(val[0], dict):
        return val[0]
    return None

def _user_to_dict(user):
    """Convert a Supabase Auth user object into the dict shape the templates expect."""
    metadata = getattr(user, 'user_metadata', None) or {}
    email = getattr(user, 'email', None) or ''
    username = metadata.get('username') or (email.split('@')[0] if '@' in email else email)
    return {
        'id': getattr(user, 'id', None),
        'email': email,
        'username': username,
        'first_name': metadata.get('first_name', ''),
        'last_name': metadata.get('last_name', ''),
        'program': metadata.get('program', ''),
        'role': metadata.get('role', 'Viewer'),
        'profile_picture': metadata.get('profile_picture'),
    }


def _list_users():
    """Return all Supabase Auth users as template-ready dicts."""
    try:
        res = _admin_auth().list_users(per_page=1000)
        raw_users = getattr(res, 'users', res) if res is not None else []
        if isinstance(raw_users, list):
            return [_user_to_dict(u) for u in raw_users]
        return []
    except Exception as err:
        logging.error(f"Error listing users: {err}")
        return []


def _find_email_by_username_or_email(identifier):
    """Resolve a username or email input to the registered Supabase Auth email."""
    if not identifier:
        return None
    identifier = identifier.strip()
    if admin_supabase is not None:
        try:
            res = _admin_auth().list_users(per_page=1000)
            raw_users = getattr(res, 'users', res) if res is not None else []
            for u in raw_users:
                u_email = (getattr(u, 'email', None) or '').strip()
                metadata = getattr(u, 'user_metadata', None) or {}
                u_name = (metadata.get('username') or '').strip()
                if identifier.lower() in (u_name.lower(), u_email.lower()):
                    return u_email
                if u_email and u_email.split('@')[0].lower() == identifier.lower():
                    return u_email
        except Exception as err:
            logging.error(f"Error resolving username to email: {err}")
    if '@' in identifier:
        return identifier
    return f"{identifier.lower()}@example.com"

def _to_datetime(value):
    """Coerce an ISO 8601 / date string into a Python datetime object (for .strftime in templates)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
    return value

def _normalize_created_at(rows):
    """Convert 'created_at' strings into datetime objects in-place."""
    for row in (rows or []):
        if isinstance(row, dict) and isinstance(row.get('created_at'), str):
            row['created_at'] = _to_datetime(row['created_at'])
    return rows

# PostgREST error codes that indicate the user's JWT is expired/invalid.
_JWT_ERROR_CODES = ('PGRST301', 'PGRST302')


def _decode_jwt_exp(access_token):
    """Return the JWT `exp` claim (unix seconds) without verifying the signature."""
    try:
        payload_b64 = access_token.split('.')[1]
        payload_b64 += '=' * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get('exp')
    except Exception:
        return None


def _expire_session():
    """Clear the Flask session and redirect to login with a session-expired message."""
    session.clear()
    flash('Session expired. Please log in again.', 'error')
    return redirect(url_for('login'))


@app.before_request
def inject_auth_token():
    """Attach the current user's JWT to the Supabase client before every request.

    This is what makes `auth.uid()` resolve correctly inside RLS policies.
    Expired tokens are proactively refreshed here so protected routes never
    see a stale JWT.
    """
    endpoint = request.endpoint

    # Public endpoints must never carry a stale Authorization header.
    if endpoint in ('login', 'signup', 'static') or endpoint is None:
        if hasattr(supabase, 'postgrest') and hasattr(supabase.postgrest, 'headers'):
            supabase.postgrest.headers.pop('Authorization', None)
        return

    access_token = session.get('jwt_token')
    refresh_token = session.get('refresh_token')

    if not access_token:
        if hasattr(supabase, 'postgrest') and hasattr(supabase.postgrest, 'headers'):
            supabase.postgrest.headers.pop('Authorization', None)
        return

    exp = _decode_jwt_exp(access_token)
    if exp is not None and time.time() >= int(exp):
        try:
            if not refresh_token:
                raise RuntimeError('Missing refresh token')
            refreshed = supabase.auth.refresh_session(refresh_token)
            new_session = refreshed.session
            if not new_session:
                raise RuntimeError('Token refresh failed')
            session['jwt_token'] = new_session.access_token
            session['refresh_token'] = new_session.refresh_token
            access_token = new_session.access_token
        except Exception:
            return _expire_session()

    if hasattr(supabase, 'postgrest') and hasattr(supabase.postgrest, 'auth'):
        supabase.postgrest.auth(access_token)


@app.errorhandler(APIError)
def handle_postgrest_error(err):
    code = getattr(err, 'code', '') or ''
    if code in _JWT_ERROR_CODES:
        return _expire_session()
    return jsonify({'error': str(getattr(err, 'message', None) or err)}), 500

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return redirect(url_for('schedules'))
        return f(*args, **kwargs)
    return decorated_function

def scheduler_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') == 'Viewer':
            return redirect(url_for('schedules'))
        return f(*args, **kwargs)
    return decorated_function

def scheduler_only_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'Scheduler':
            flash('Access denied. Only Schedulers can generate schedules.', 'error')
            return redirect(url_for('schedules'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(allowed_roles):
    """Decorator factory — returns 403 if the current user's role is not in allowed_roles.
    Role comparison is case-insensitive for robustness."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user_role = (session.get('role') or '').lower()
            normalised = [r.lower() for r in allowed_roles]
            if user_role not in normalised:
                return jsonify({'error': 'Access denied. Insufficient permissions.'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def log_activity(action, target_type, target_detail=''):
    """Write one row to activity_log. Never raises — logging must not break the main action."""
    try:
        first_name = session.get('first_name', '')
        last_name = session.get('last_name', '')
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            full_name = session.get('username', '')

        user_id = session.get('user_id')
        payload = {
            'username': full_name,
            'action': action,
            'target_type': target_type,
            'target_detail': str(target_detail)[:255],
        }
        if user_id:
            payload['user_id'] = str(user_id)

        supabase.table('activity_log').insert(payload).execute()
    except Exception as err:
        logging.debug(f"Activity log error: {err}")

def _ensure_delete_requests_table():
    # Schema is managed via Supabase migrations/SQL editor, not at runtime.
    return

_ensure_delete_requests_table()

def _ensure_irregular_student_tables():
    # Schema is managed via Supabase migrations/SQL editor, not at runtime.
    return

_ensure_irregular_student_tables()

def _time_to_minutes(time_val):
    if not time_val:
        return 0
    if isinstance(time_val, timedelta):
        return int(time_val.total_seconds()) // 60
    if hasattr(time_val, 'hour') and hasattr(time_val, 'minute'):
        return time_val.hour * 60 + time_val.minute
    s = str(time_val).strip()
    try:
        dt = datetime.strptime(s, '%I:%M %p')
        return dt.hour * 60 + dt.minute
    except ValueError:
        pass
    try:
        dt = datetime.strptime(s, '%H:%M:%S')
        return dt.hour * 60 + dt.minute
    except ValueError:
        pass
    try:
        dt = datetime.strptime(s, '%H:%M')
        return dt.hour * 60 + dt.minute
    except ValueError:
        pass
    return 0

def _check_schedule_conflict(student_id, new_schedule_entries):
    res = supabase.table('irregular_student_schedule').select(
        'course_id, schedule(day, class_start, class_end, course_id, course(course_name))'
    ).eq('student_id', student_id).execute()

    existing_entries = []
    for row in (res.data or []):
        sch = _rel(row, 'schedule') or {}
        crs = _rel(sch, 'course') or {}
        existing_entries.append({
            'course_id': row.get('course_id'),
            'course_name': crs.get('course_name'),
            'section': sch.get('section'),
            'day': sch.get('day'),
            'class_start': sch.get('class_start'),
            'class_end': sch.get('class_end'),
        })

    for new_e in new_schedule_entries:
        new_day = (new_e.get('day') or '').strip().capitalize()
        new_start = _time_to_minutes(new_e.get('class_start'))
        new_end = _time_to_minutes(new_e.get('class_end'))
        new_course_name = new_e.get('course_name') or 'Selected Subject'

        for ext in existing_entries:
            if str(ext.get('course_id')) == str(new_e.get('course_id')):
                continue

            ext_day = (ext.get('day') or '').strip().capitalize()
            if new_day and ext_day and new_day == ext_day:
                ext_start = _time_to_minutes(ext.get('class_start'))
                ext_end = _time_to_minutes(ext.get('class_end'))

                if new_start < ext_end and new_end > ext_start:
                    ext_course = ext.get('course_name') or 'existing subject'
                    start_str = _format_time(new_e.get('class_start')) or str(new_e.get('class_start'))
                    end_str = _format_time(new_e.get('class_end')) or str(new_e.get('class_end'))
                    conflict_msg = f"Schedule Conflict: {new_course_name} conflicts with {ext_course} on {new_day} from {start_str} - {end_str}."
                    return True, conflict_msg

    return False, None

@app.context_processor
def inject_pending_requests():
    user_id = session.get('user_id')
    user_role = session.get('role', '')

    context = {
        'pending_delete_requests_count': 0,
        'pending_delete_requests': [],
        'unread_notifications_count': 0,
        'scheduler_notifications': []
    }

    if not user_id:
        return context

    try:
        if user_role == 'admin':
            res = supabase.table('delete_requests').select('*', count='exact').eq('status', 'pending').execute()
            context['pending_delete_requests_count'] = res.count if res.count is not None else 0

            pending = supabase.table('delete_requests').select(
                'id, user_id, username, first_name, last_name, item_type, item_id, item_details, status, created_at'
            ).eq('status', 'pending').order('created_at', desc=True).execute()
            context['pending_delete_requests'] = _normalize_created_at(pending.data or [])

        n_res = supabase.table('scheduler_notifications').select('*', count='exact').eq('user_id', user_id).eq('is_read', False).execute()
        context['unread_notifications_count'] = n_res.count if n_res.count is not None else 0

        notifs = supabase.table('scheduler_notifications').select(
            'id, user_id, request_id, message, status, is_read, created_at'
        ).eq('user_id', user_id).order('created_at', desc=True).limit(50).execute()
        context['scheduler_notifications'] = _normalize_created_at(notifs.data or [])
    except Exception:
        pass

    return context

def _request_delete_if_scheduler(item_type, item_id, item_details):
    role = session.get('role', '')
    if role == 'Scheduler':
        user_id = session.get('user_id')
        username = session.get('username', '')
        first_name = session.get('first_name', '')
        last_name = session.get('last_name', '')

        try:
            details_str = str(item_details)
            if item_type == 'course':
                res = supabase.table('course').select('course_name').eq('course_id', item_id).execute()
                c_row = _first(res.data or [])
                if c_row and c_row.get('course_name'):
                    details_str = f"{c_row['course_name']} (ID: {item_id})"
            elif item_type == 'professor':
                res = supabase.table('professor').select('first_name, last_name').eq('prof_id', item_id).execute()
                p_row = _first(res.data or [])
                if p_row:
                    details_str = f"{p_row.get('first_name', '')} {p_row.get('last_name', '')} (ID: {item_id})".strip()
            elif item_type == 'room':
                res = supabase.table('room').select('room_name').eq('room_id', item_id).execute()
                r_row = _first(res.data or [])
                if r_row and r_row.get('room_name'):
                    details_str = f"Room {r_row['room_name']} (ID: {item_id})"
            elif item_type == 'timeslot':
                res = supabase.table('timeslot').select('start_day, start_time, end_time').eq('timeslot_id', item_id).execute()
                t_row = _first(res.data or [])
                if t_row:
                    details_str = f"{t_row.get('start_day', '')} {t_row.get('start_time', '')}-{t_row.get('end_time', '')} (ID: {item_id})"

            existing = supabase.table('delete_requests').select('id').eq('item_type', item_type).eq('item_id', str(item_id)).eq('status', 'pending').execute()
            if existing.data:
                msg = 'Delete request already pending. Waiting for Administrator approval.'
                if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return True, jsonify({'success': False, 'message': msg})
                flash(msg, 'warning')
                return True, None

            supabase.table('delete_requests').insert({
                'user_id': str(user_id) if user_id else None,
                'username': username,
                'first_name': first_name,
                'last_name': last_name,
                'item_type': item_type,
                'item_id': str(item_id),
                'item_details': details_str,
                'status': 'pending',
            }).execute()

            log_activity('request_delete', item_type, f'Requested deletion of {details_str}')
            msg = 'Delete Request Sent. Your request has been sent to an Administrator for approval.'
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return True, jsonify({'success': True, 'message': msg})
            flash(msg, 'info')
            return True, None
        except Exception as err:
            msg = f'Database error: {err}'
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return True, jsonify({'success': False, 'message': msg})
            flash(msg, 'error')
            return True, None
    return False, None

#-------------------------------------------------------PROGRAM_TO_DEPARTMENT----------------------------------------------------------------------------------------------
def _get_department(program=None):
    """Dynamically fetch the department associated with a program from the program_department table in Supabase.

    1. Retrieves the user's program from the argument, session, or the users table.
    2. For Admin role without program specified, returns None without error.
    3. Queries the program_department table using the Supabase Python SDK.
    4. Returns the matching department_name, or None if not found.
    """
    user_role = (session.get('role') or '').lower()
    user_program = program or session.get('program')
    if not user_program and session.get('user_id'):
        try:
            res = supabase.auth.get_user(session.get('jwt_token'))
            if res and res.user:
                metadata = getattr(res.user, 'user_metadata', None) or {}
                user_program = metadata.get('program')
                if user_program:
                    session['program'] = user_program
        except Exception as err:
            logging.error(f"Error retrieving program for user_id {session.get('user_id')}: {err}")

    if not user_program or str(user_program).upper() in ['ALL', 'GLOBAL', 'N/A', 'NONE']:
        if user_role == 'admin':
            return None
        logging.warning("Unable to resolve program: No program found in session or database.")
        return None

    try:
        response = supabase.table('program_department').select('department_name').eq('program_name', user_program).single().execute()
        if response.data and response.data.get('department_name'):
            return response.data['department_name']
        if user_role != 'admin':
            logging.error(f"Program '{user_program}' does not exist in program_department table.")
        return None
    except Exception as err:
        if user_role != 'admin':
            logging.error(f"Failed to query program_department for program '{user_program}': {err}")
        return None


def _ensure_course_semester_column():
    # The 'semester' column is part of the migrated Supabase schema.
    return


_SERVER_PREVIEW_STORE = {}

def _get_preview_for_user(user_id=None, preview_id=None):
    """Retrieve schedule preview from server-side store or temp file to avoid Flask 4KB cookie limits."""
    from flask import has_request_context
    req_preview_id = session.get('preview_id') if has_request_context() else None
    req_user_id = session.get('user_id') if has_request_context() else None
    key = str(preview_id or user_id or req_preview_id or req_user_id or 'anon')
    if key in _SERVER_PREVIEW_STORE:
        return _SERVER_PREVIEW_STORE[key]
    tmp_path = os.path.join(tempfile.gettempdir(), f'sched_preview_{key}.json')
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _SERVER_PREVIEW_STORE[key] = data
                return data
        except Exception:
            pass
    if has_request_context():
        return session.get('schedule_preview', [])
    return []

def _set_preview_for_user(preview_data, user_id=None, preview_id=None):
    """Save schedule preview in server-side storage and prevent cookie size overflow."""
    from flask import has_request_context
    if has_request_context() and not session.get('preview_id'):
        session['preview_id'] = str(uuid.uuid4())
    req_preview_id = session.get('preview_id') if has_request_context() else None
    req_user_id = session.get('user_id') if has_request_context() else None
    key = str(preview_id or user_id or req_preview_id or req_user_id or 'anon')
    _SERVER_PREVIEW_STORE[key] = preview_data
    tmp_path = os.path.join(tempfile.gettempdir(), f'sched_preview_{key}.json')
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(preview_data, f)
    except Exception as e:
        logging.error(f"Error saving preview cache: {e}")
    if has_request_context():
        # Strip from client session cookie to prevent HTTP header truncation / browser dropping cookie
        session.pop('schedule_preview', None)
        session['has_schedule_preview'] = bool(preview_data)
        session.modified = True

def _clear_preview_for_user(user_id=None, preview_id=None):
    """Clear server-side schedule preview and session flags."""
    from flask import has_request_context
    req_preview_id = session.get('preview_id') if has_request_context() else None
    req_user_id = session.get('user_id') if has_request_context() else None
    key = str(preview_id or user_id or req_preview_id or req_user_id or 'anon')
    _SERVER_PREVIEW_STORE.pop(key, None)
    tmp_path = os.path.join(tempfile.gettempdir(), f'sched_preview_{key}.json')
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    if has_request_context():
        session.pop('schedule_preview', None)
        session.pop('has_schedule_preview', None)
        session.pop('preview_id', None)
        session.modified = True

def _clear_preview_generation_state():
    _clear_preview_for_user()
    session['generated_sections'] = []
    session.modified = True


def _major_matches(course_major, selected_major):
    if not selected_major:
        return True
    if not course_major:
        return True

    normalized_course = str(course_major).strip().lower()
    normalized_selected = str(selected_major).strip().lower()

    if normalized_course in ['general', 'none', '']:
        return True

    aliases = {
        'database systems': ['database', 'database systems', 'database system'],
        'database': ['database', 'database systems', 'database system'],
        'web development': ['web', 'web development', 'web dev'],
        'web': ['web', 'web development', 'web dev'],
        'networking': ['networking'],
        'general': ['general', 'none', '']
    }

    if normalized_selected in aliases:
        return normalized_course in aliases[normalized_selected]

    return normalized_course == normalized_selected
def _normalize_major_key(major):
    if not major or str(major).strip().lower() in ('general', 'none', 'null', ''):
        return None
    return str(major).strip()


def _group_preview_sections(sections_with_entries, year_filter=None, major_filter=None):
    """Group sections_with_entries into year level and track accordion blocks for schedule preview & section schedule."""
    groups = [
        {
            'id': '1st-year',
            'title': '1st Year Schedules',
            'year_level': '1',
            'track': None,
            'sections': [],
        },
        {
            'id': '2nd-year',
            'title': '2nd Year Schedules',
            'year_level': '2',
            'track': None,
            'sections': [],
        },
        {
            'id': '3rd-year',
            'title': '3rd Year Schedules',
            'year_level': '3',
            'track': None,
            'sections': [],
        },
        {
            'id': '4th-year-wst',
            'title': '4th Year Schedules — WST',
            'year_level': '4',
            'track': 'WST',
            'sections': [],
        },
        {
            'id': '4th-year-dst',
            'title': '4th Year Schedules — DST',
            'year_level': '4',
            'track': 'DST',
            'sections': [],
        },
        {
            'id': '4th-year-nst',
            'title': '4th Year Schedules — NST',
            'year_level': '4',
            'track': 'NST',
            'sections': [],
        },
    ]

    other_group = {
        'id': 'other-schedules',
        'title': 'Other Schedules',
        'year_level': 'Other',
        'track': None,
        'sections': [],
    }

    group_map = {g['id']: g for g in groups}

    for item in sections_with_entries:
        sec_info = item.get('section') or {}
        sec_name = str(sec_info.get('section_name') or sec_info.get('section') or '').strip()
        sec_major = str(sec_info.get('major') or '').strip()
        entries = item.get('entries') or []

        # Determine year level
        year_level = None
        if sec_name and sec_name[0] in ('1', '2', '3', '4'):
            year_level = sec_name[0]
        else:
            for entry in entries:
                yr = str(entry.get('year_level') or '')
                if yr in ('1', '2', '3', '4'):
                    year_level = yr
                    break

        if year_level == '1':
            group_map['1st-year']['sections'].append(item)
        elif year_level == '2':
            group_map['2nd-year']['sections'].append(item)
        elif year_level == '3':
            group_map['3rd-year']['sections'].append(item)
        elif year_level == '4':
            combined_str = f"{sec_name} {sec_major}".upper()
            for entry in entries:
                combined_str += f" {entry.get('course_name','')} {entry.get('major','')}".upper()

            if any(k in combined_str for k in ['WST', 'WEB', 'WEB DEVELOPMENT']) or sec_name.endswith('WST') or ' WST' in combined_str or ' W ' in f" {combined_str} ":
                group_map['4th-year-wst']['sections'].append(item)
            elif any(k in combined_str for k in ['DST', 'DB', 'DATABASE']) or sec_name.endswith('DST') or ' DST' in combined_str or ' D ' in f" {combined_str} ":
                group_map['4th-year-dst']['sections'].append(item)
            elif any(k in combined_str for k in ['NST', 'NET', 'NETWORKING']) or sec_name.endswith('NST') or ' NST' in combined_str or ' N ' in f" {combined_str} ":
                group_map['4th-year-nst']['sections'].append(item)
            else:
                group_map['4th-year-wst']['sections'].append(item)
        else:
            other_group['sections'].append(item)

    if other_group['sections']:
        groups.append(other_group)

    active_groups = []
    for g in groups:
        # Only display a schedule block if it contains at least one section
        if not g.get('sections'):
            continue

        if year_filter and str(g['year_level']) != str(year_filter):
            continue

        if major_filter and g['year_level'] == '4':
            m_str = str(major_filter).upper()
            g_track = str(g['track'] or '').upper()
            g_title = str(g['title']).upper()
            match = False
            if 'WST' in g_track or 'WST' in g_title:
                if any(k in m_str for k in ['WST', 'WEB']):
                    match = True
            if 'DST' in g_track or 'DST' in g_title:
                if any(k in m_str for k in ['DST', 'DB', 'DATABASE']):
                    match = True
            if 'NST' in g_track or 'NST' in g_title:
                if any(k in m_str for k in ['NST', 'NET', 'NETWORK']):
                    match = True
            if not match and g['sections']:
                for sec_item in g['sections']:
                    s_maj = str((sec_item.get('section') or {}).get('major') or '').upper()
                    if m_str in s_maj or s_maj in m_str:
                        match = True
                        break
            if not match:
                continue

        active_groups.append(g)

    return active_groups


def _build_preview_context(preview_entries=None):
    preview = preview_entries if preview_entries is not None else _get_preview_for_user()
    sections_by_key = {}
    for entry in preview:
        section_name = entry.get('section')
        semester = entry.get('semester', '')
        major_key = _normalize_major_key(entry.get('major'))
        key = (section_name, semester, major_key)
        if key not in sections_by_key:
            sections_by_key[key] = {
                'section': {
                    'section': section_name,
                    'section_name': section_name,
                    'semester': semester,
                    'major': major_key,
                },
                'entries': []
            }
        sections_by_key[key]['entries'].append(entry)

    sections_with_entries = list(sections_by_key.values())
    year_groups = _group_preview_sections(sections_with_entries)

    courses = []
    rooms = []
    prof_course_assignments = {}
    try:
        user_role = session.get('role', 'Viewer')
        department = _get_department()
        program = session.get('program', '')

        # Admin and Scheduler see all courses, Viewer sees only their program's courses
        if user_role == 'Viewer':
            if not program:
                courses = []
            else:
                courses_res = supabase.table('course').select('course_id, course_name').eq('program', program).order('course_name').execute()
                courses = courses_res.data or []
        else:
            courses_res = supabase.table('course').select('course_id, course_name').order('course_name').execute()
            courses = courses_res.data or []

        # Admin and Scheduler see all rooms, Viewer sees only their department's rooms
        if user_role == 'Viewer':
            if not department:
                rooms = []
            else:
                rooms_res = supabase.table('room').select('room_id, room_name').eq('department', department).order('room_name').execute()
                rooms = rooms_res.data or []
        else:
            rooms_res = supabase.table('room').select('room_id, room_name').order('room_name').execute()
            rooms = rooms_res.data or []

        # Admin and Scheduler see all prof_course assignments, Viewer sees only their department's
        if user_role == 'Viewer' and department:
            pc_res = supabase.table('prof_course').select('course_id, professor(prof_id, first_name, last_name)').eq('professor.department', department).execute()
        else:
            pc_res = supabase.table('prof_course').select('course_id, professor(prof_id, first_name, last_name)').execute()
        assignments = pc_res.data or []
        for row in assignments:
            p = _rel(row, 'professor')
            if not p:
                continue
            course_key = str(row['course_id'])
            prof_name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            prof_entry = {'id': p['prof_id'], 'name': prof_name}
            prof_course_assignments.setdefault(course_key, []).append(prof_entry)
    except Exception:
        courses = []
        rooms = []
        prof_course_assignments = {}

    return {
        'sections_with_entries': sections_with_entries,
        'year_groups': year_groups,
        'courses': courses,
        'rooms': rooms,
        'prof_course_assignments': prof_course_assignments,
    }


def _generate_mock_registrar_data(semester=None):
    """Generate mock registrar data with proportional section scaling.
    
    Rule:
      - When semester is 2nd Semester:
          - Only 1st, 2nd, and 3rd year levels are populated. 4th year is 0.
          - 3rd year separates sections per major: Database, Web, Networking.
      - Otherwise (1st Semester / all): 1st, 2nd, 3rd, and 4th year levels are populated.
      - Random number of students between 300 and 400 for active years.
      - Proportional section count formula: math.ceil(students / 30).
    """
    years_data = {}
    total_students = 0
    total_sections = 0

    is_second_sem = str(semester or '').strip().lower() in ('2nd semester', '2nd', '2')
    max_year = 3 if is_second_sem else 4

    for y in range(1, 5):
        y_str = str(y)
        if y > max_year:
            years_data[y_str] = {
                'student_count': 0,
                'section_count': 0,
            }
        else:
            students = random.randint(300, 400)
            sections = math.ceil(students / 30)
            y_info = {
                'student_count': students,
                'section_count': sections,
            }

            if is_second_sem and y == 3:
                # Distribute sections across Database, Web, Networking
                base_sec = sections // 3
                rem_sec = sections % 3
                db_sec = max(1, base_sec + (1 if rem_sec > 0 else 0))
                web_sec = max(1, base_sec + (1 if rem_sec > 1 else 0))
                net_sec = max(1, base_sec)
                
                majors_breakdown = {
                    'database': db_sec,
                    'web': web_sec,
                    'networking': net_sec,
                }
                y_info['majors'] = majors_breakdown
                y_info['sections_by_major'] = majors_breakdown

            years_data[y_str] = y_info
            total_students += students
            total_sections += sections

    return {
        'years': years_data,
        'total_students': total_students,
        'total_sections': total_sections,
    }


def _get_registrar_counts_for_year(year_level=None, semester=None):
    """Return registrar counts for year levels using proportional mock data."""
    data = _generate_mock_registrar_data(semester=semester)
    year_key = str(year_level or '').strip()
    if year_key in data['years']:
        return data['years'][year_key]
    if year_key.lower() in ('all', 'batch', ''):
        return {
            'student_count': data['total_students'],
            'section_count': data['total_sections'],
            'years': data['years'],
        }
    return data['years']['1']


@app.route('/api/registrar-counts')
@login_required
def registrar_counts():
    """Return registrar mock data for year levels as JSON."""
    year_level = request.args.get('year_level', '')
    semester = request.args.get('semester', '')
    if year_level and year_level.lower() not in ('all', 'batch'):
        return jsonify(_get_registrar_counts_for_year(year_level, semester=semester))
    return jsonify(_generate_mock_registrar_data(semester=semester))
#-----------------------------------------------------LOGIN AND LOGOUT----------------------------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        identifier = (request.form.get('username') or request.form.get('email') or '').strip()
        password = request.form.get('password', '')

        if not identifier or not password:
            error = 'Username or email and password are required.'
        else:
            try:
                target_email = _find_email_by_username_or_email(identifier) or identifier
                auth_res = None
                try:
                    auth_res = supabase.auth.sign_in_with_password({'email': target_email, 'password': password})
                except Exception as first_err:
                    if target_email != identifier and '@' in identifier:
                        auth_res = supabase.auth.sign_in_with_password({'email': identifier, 'password': password})
                    elif '@' not in identifier:
                        # Fallback try example.com domain if custom username
                        auth_res = supabase.auth.sign_in_with_password({'email': f"{identifier.lower()}@example.com", 'password': password})
                    else:
                        raise first_err

                session_data = auth_res.session if auth_res else None

                if not session_data:
                    error = 'Invalid username/email or password.'
                else:
                    # Store the JWTs in the Flask session for the before_request middleware.
                    session['jwt_token'] = session_data.access_token
                    session['refresh_token'] = session_data.refresh_token

                    auth_user = session_data.user

                    # Inject the JWT so any immediate PostgREST call runs as this user.
                    supabase.postgrest.auth(session_data.access_token)

                    # Populate the app session from the authenticated user + its metadata.
                    metadata = getattr(auth_user, 'user_metadata', None) or {}
                    user_email = getattr(auth_user, 'email', None) or ''
                    display_username = metadata.get('username') or (user_email.split('@')[0] if '@' in user_email else user_email)

                    session['user_id'] = auth_user.id
                    session['email'] = user_email
                    session['username'] = display_username
                    session['first_name'] = metadata.get('first_name', '')
                    session['last_name'] = metadata.get('last_name', '')
                    session['program'] = metadata.get('program', '')
                    session['profile_picture'] = metadata.get('profile_picture')
                    session['role'] = metadata.get('role', 'Viewer')

                    log_activity('login', 'auth', session['username'])

                    # Redirect Viewer to Section Schedule, others to home
                    if session['role'] == 'Viewer':
                        return redirect(url_for('schedules'))
                    else:
                        return redirect(url_for('home'))
            except Exception as err:
                logging.error(f"Login error for '{identifier}': {err}")
                error = 'Invalid username/email or password.'

    return render_template('login.html', error=error)

@app.route('/signup', methods=['POST'])
def signup():
    error = None
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        program = request.form.get('program', '').strip()
        username_input = (request.form.get('username') or request.form.get('email') or '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Validate required fields
        if not first_name or not last_name or not program or not username_input or not password or not confirm_password:
            error = 'All fields are required.'
        # Validate program selection
        elif program not in ['BSIT', 'BSBA']:
            error = 'Please select a valid program.'
        # Validate password match
        elif password != confirm_password:
            error = 'Passwords do not match.'
        else:
            try:
                email = username_input if '@' in username_input else f"{username_input.lower()}@example.com"

                res = supabase.auth.sign_up({
                    'email': email,
                    'password': password,
                    'options': {
                        'data': {
                            'first_name': first_name,
                            'last_name': last_name,
                            'username': username_input,
                            'program': program,
                            'role': 'Viewer',
                        }
                    }
                })

                if not res.user:
                    error = 'Unable to create account. Please try again.'
                else:
                    try:
                        full_name = f"{first_name} {last_name}".strip()
                        if not full_name:
                            full_name = username_input
                        supabase.table('activity_log').insert({
                            'username': full_name,
                            'action': 'create',
                            'target_type': 'user',
                            'target_detail': f'Self-registered: {username_input}',
                        }).execute()
                    except Exception:
                        pass

                    return redirect(url_for('login'))

            except Exception as err:
                logging.error(f"Signup error: {err}")
                error = 'Username/email already registered or database error. Please try again.'

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    try:
        access_token = session.get('jwt_token')
        refresh_token = session.get('refresh_token')
        if access_token and refresh_token:
            supabase.auth.set_session(access_token, refresh_token)
        supabase.auth.sign_out()
    except Exception:
        pass
    log_activity('logout', 'auth', session.get('username', ''))
    session.clear()
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    user = None
    try:
        res = supabase.auth.get_user(session.get('jwt_token'))
        if res and res.user:
            user = _user_to_dict(res.user)
    except Exception:
        pass

    if not user:
        return redirect(url_for('login'))

    return render_template('profile.html', active_page='profile', user=user)

ALLOWED_PICTURE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}

@app.route('/upload_profile_picture', methods=['POST'])
@login_required
def upload_profile_picture():
    file = request.files.get('profile_picture')
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('profile'))

    # Validate extension
    filename = file.filename
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_PICTURE_EXTENSIONS:
        flash('Invalid file type. Only JPG, JPEG, PNG, and WEBP are allowed.', 'error')
        return redirect(url_for('profile'))

    # Build a unique filename: user_<id>.<ext>
    user_id = session['user_id']
    safe_filename = f'user_{user_id}.{ext}'

    # Save the file
    upload_folder = os.path.join(app.static_folder, 'profile')
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, safe_filename))

    # Update the user's profile picture in Supabase Auth metadata.
    try:
        supabase.auth.set_session(session.get('jwt_token'), session.get('refresh_token'))
        supabase.auth.update_user({'data': {'profile_picture': safe_filename}})
    except Exception:
        flash('Failed to update profile picture.', 'error')
        return redirect(url_for('profile'))

    # Update session so header avatar refreshes immediately
    session['profile_picture'] = safe_filename

    log_activity('upload', 'profile', 'Changed profile picture')

    flash('Profile picture updated successfully.', 'success')
    return redirect(url_for('profile'))

#-------------------------------------------------------ACTIVITY LOG----------------------------------------------------------------------------------------------
@app.route('/activity_log')
@admin_required
def activity_log():
    search = request.args.get('search', '').strip()
    action_filter = request.args.get('action', '').strip()
    target_filter = request.args.get('target', '').strip()

    try:
        query = supabase.table('activity_log').select('*')

        if search:
            query = query.or_(f"username.ilike.%{search}%,target_detail.ilike.%{search}%")

        if action_filter:
            query = query.eq('action', action_filter)

        if target_filter:
            query = query.eq('target_type', target_filter)

        query = query.order('created_at', desc=True).limit(200)
        logs = _normalize_created_at(query.execute().data or [])

        # Get distinct values for filter dropdowns
        actions_res = supabase.table('activity_log').select('action').execute()
        actions = sorted({row['action'] for row in (actions_res.data or []) if row.get('action')})

        targets_res = supabase.table('activity_log').select('target_type').execute()
        targets = sorted({row['target_type'] for row in (targets_res.data or []) if row.get('target_type')})
    except Exception:
        logs = []
        actions = []
        targets = []

    return render_template('activity_log.html', active_page='activity_log', logs=logs,
                           actions=actions, targets=targets,
                           search=search, action_filter=action_filter, target_filter=target_filter)

@app.route('/add_user_columns')
def add_user_columns():
    # Schema migration is handled in Supabase; no runtime DDL required.
    return "Columns are part of the Supabase schema. You can close this page."

@app.route('/set_admin_role/<email>')
def set_admin_role(email):
    try:
        found = None
        for u in _admin_auth().list_users(per_page=1000):
            if getattr(u, 'email', None) == email:
                found = u
                break
        if not found:
            return f"User '{email}' not found."

        metadata = dict(getattr(found, 'user_metadata', None) or {})
        metadata['role'] = 'admin'
        _admin_auth().update_user_by_id(found.id, {'user_metadata': metadata})
        return f"User '{email}' set to admin role. You can close this page."
    except Exception as err:
        return f"Error: {err}"

#-------------------------------------------------------User Management----------------------------------------------------------------------------------------------
@app.route('/users')
@admin_required
def users():
    search_query = request.args.get('search', '').strip()

    try:
        users_list = _list_users()
        if search_query:
            q = search_query.lower()
            users_list = [
                u for u in users_list
                if any(q in (u.get(k) or '').lower() for k in ('first_name', 'last_name', 'username', 'program', 'role'))
            ]
        users_list.sort(key=lambda u: (str(u.get('role') or ''), str(u.get('last_name') or ''), str(u.get('first_name') or '')))
    except Exception:
        users_list = []

    return render_template('users.html', active_page='users', users=users_list, search_query=search_query)

@app.route('/search_users', methods=['GET'])
@admin_required
def search_users():
    query_str = request.args.get('q', '').strip()

    try:
        users_list = _list_users()
        if query_str:
            q = query_str.lower()
            users_list = [
                u for u in users_list
                if any(q in (u.get(k) or '').lower() for k in ('first_name', 'last_name', 'username', 'program', 'role'))
            ]
        users_list.sort(key=lambda u: (str(u.get('role') or ''), str(u.get('last_name') or ''), str(u.get('first_name') or '')))
    except Exception:
        users_list = []

    return jsonify({'users': users_list})

@app.route('/edit_user/<user_id>', methods=['POST'])
@admin_required
def edit_user(user_id):
    try:
        res = _admin_auth().get_user_by_id(user_id)
        user = res.user if res else None

        if not user:
            return jsonify({'success': False, 'message': 'User not found.'}), 404

        # Check if admin is trying to change their own role
        current_user_id = session.get('user_id')
        if str(current_user_id) == str(user_id):
            new_role = request.form.get('role', '').strip()
            if new_role != 'admin':
                return jsonify({'success': False, 'message': 'You cannot remove your own administrator access.'}), 403

        # Update user
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', '').strip()
        program = request.form.get('program', '').strip() if role.lower() != 'admin' else None

        if role.lower() != 'admin' and not program:
            return jsonify({'success': False, 'message': 'Program is required for Scheduler and Viewer roles.'}), 400

        # admin.update_user_by_id replaces user_metadata, so merge over the existing.
        metadata = dict(getattr(user, 'user_metadata', None) or {})
        metadata['first_name'] = first_name
        metadata['last_name'] = last_name
        metadata['program'] = program
        metadata['role'] = role

        attrs = {'user_metadata': metadata}
        if email:
            attrs['email'] = email
            attrs['email_confirm'] = True

        _admin_auth().update_user_by_id(user_id, attrs)

        log_activity('edit', 'user', f'{first_name} {last_name}')
        flash('Edited successfully', 'success')
        return jsonify({'success': True, 'message': 'User updated successfully.'})
    except Exception as err:
        return jsonify({'success': False, 'message': f'Database error: {str(err)}'}), 500

@app.route('/delete_user/<user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    try:
        current_user_id = session.get('user_id')

        # Prevent admin from deleting themselves
        if str(current_user_id) == str(user_id):
            return jsonify({'success': False, 'message': 'You cannot delete your own account.'}), 403

        _admin_auth().delete_user(user_id)

        log_activity('delete', 'user', f'User ID {user_id}')
        flash('Deleted successfully', 'success')
        return jsonify({'success': True, 'message': 'User deleted successfully.'})
    except Exception as err:
        return jsonify({'success': False, 'message': f'Database error: {str(err)}'}), 500

@app.route('/create_user', methods=['POST'])
@admin_required
def create_user():
    try:
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', '').strip()
        program = request.form.get('program', '').strip() if role.lower() != 'admin' else None

        # Validate required fields
        if not first_name or not last_name or not email or not password or not role:
            return jsonify({'success': False, 'message': 'All required fields must be filled.'}), 400

        if role.lower() != 'admin' and not program:
            return jsonify({'success': False, 'message': 'Program is required for Scheduler and Viewer roles.'}), 400

        _admin_auth().create_user({
            'email': email,
            'password': password,
            'email_confirm': True,
            'user_metadata': {
                'first_name': first_name,
                'last_name': last_name,
                'program': program,
                'role': role,
            },
        })

        log_activity('create', 'user', f'{first_name} {last_name}')
        flash('Created successfully', 'success')
        return jsonify({'success': True, 'message': 'User created successfully.'})
    except Exception as err:
        msg = str(err).lower()
        if 'already been registered' in msg or 'already exists' in msg or 'duplicate' in msg:
            return jsonify({'success': False, 'message': 'Email already exists.'}), 400
        return jsonify({'success': False, 'message': f'Database error: {str(err)}'}), 500

@app.route('/create_test_accounts')
def create_test_accounts():
    try:
        # Test accounts credentials
        test_accounts = [
            {
                'first_name': 'Admin',
                'last_name': 'User',
                'email': 'admin@example.com',
                'password': 'Admin123!',
                'program': 'BSIT',
                'role': 'admin'
            },
            {
                'first_name': 'Scheduler',
                'last_name': 'User',
                'email': 'scheduler@example.com',
                'password': 'Scheduler123!',
                'program': 'BSIT',
                'role': 'Scheduler'
            },
            {
                'first_name': 'Viewer',
                'last_name': 'User',
                'email': 'viewer@example.com',
                'password': 'Viewer123!',
                'program': 'BSIT',
                'role': 'Viewer'
            }
        ]

        created_accounts = []
        skipped_accounts = []

        for account in test_accounts:
            try:
                _admin_auth().create_user({
                    'email': account['email'],
                    'password': account['password'],
                    'email_confirm': True,
                    'user_metadata': {
                        'first_name': account['first_name'],
                        'last_name': account['last_name'],
                        'program': account['program'],
                        'role': account['role'],
                    },
                })
                created_accounts.append(account['email'])
            except Exception:
                skipped_accounts.append(account['email'])

        result = []
        if created_accounts:
            result.append(f"Created accounts: {', '.join(created_accounts)}")
        if skipped_accounts:
            result.append(f"Skipped existing accounts: {', '.join(skipped_accounts)}")

        return '<br>'.join(result) + '<br><br>You can close this page.'
    except Exception as err:
        return f"Error: {err}"

@app.route('/')
@scheduler_only_required
def home():
    semesters = ['1st Semester', '2nd Semester']
    return render_template('index.html', active_page='home', semesters=semesters)


@app.route('/index.html')
@login_required
def legacy_index_html():
    return redirect(url_for('home'))


@app.route('/courses.html')
@login_required
def legacy_courses_html():
    return redirect(url_for('show_courses'))


@app.route('/professors.html')
@login_required
def legacy_professors_html():
    return redirect(url_for('professors'))


@app.route('/section.html')
@login_required
def legacy_section_html():
    return redirect(url_for('sections'))


@app.route('/room.html')
@login_required
def legacy_room_html():
    return redirect(url_for('rooms'))


@app.route('/timeslot.html')
@login_required
def legacy_timeslot_html():
    return redirect(url_for('timeslot'))


@app.route('/schedules.html')
@login_required
def legacy_schedules_html():
    return redirect(url_for('schedules'))


@app.route('/generated_schedule.html')
@login_required
def legacy_generated_schedule_html():
    return redirect(url_for('schedules'))


@app.route('/schedule.html')
@login_required
def legacy_schedule_html():
    return redirect(url_for('schedules'))
#-------------------------------------------------------add_course----------------------------------------------------------------------------------------------
@app.route('/add_course', methods=['POST'])
@login_required
def add_course():
    # Insert new course into the database from submitted form
    try:
        course_name = request.form['course_name']
        lecture_hours = request.form['lecture_hours']
        lab_hours = request.form['lab_hours']
        ilp_hours = request.form.get('ilp_hours', 0)
        program = session.get('program', '')
        year_level = request.form.get('year_level', '')
        semester = request.form.get('semester', '').strip()

        if not semester:
            session['course_message'] = 'Semester is required.'
            session.modified = True
            return redirect(url_for('show_courses'))

        # Major is required for specific Year Level + Semester combinations
        def is_major_required(year, sem):
            # Major is required for 3rd Year - 2nd Semester
            if year == '3' and sem in ('2nd Semester', '2nd', '2'):
                return True
            # Major is required for 4th Year - 1st Semester
            if year == '4' and sem in ('1st Semester', '1st', '1'):
                return True
            # Major is required for 4th Year - 2nd Semester
            if year == '4' and sem in ('2nd Semester', '2nd', '2'):
                return True
            return False

        major_required = is_major_required(year_level, semester)
        major = request.form.get('major', '').strip() or None if major_required else None

        _ensure_course_semester_column()

        supabase.table('course').insert({
            'course_name': course_name,
            'lecture_hours': lecture_hours,
            'lab_hours': lab_hours,
            'ilp_hours': ilp_hours,
            'program': program,
            'year_level': year_level,
            'major': major,
            'semester': semester,
        }).execute()
        session.pop('course_message', None)

        log_activity('create', 'course', course_name)
        flash('Created successfully', 'success')
        return redirect(url_for('show_courses'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------show_courses----------------------------------------------------------------------------------------------
@app.route('/courses')
@scheduler_required
def show_courses():
    user_program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    program_filter = request.args.get('program', '').strip()
    _ensure_course_semester_column()

    query = supabase.table('course').select('*')
    if user_role == 'admin':
        if program_filter and program_filter.lower() != 'all':
            query = query.eq('program', program_filter)
    else:
        if user_program:
            query = query.eq('program', user_program)

    all_courses = query.execute().data or []
    all_courses.sort(key=lambda c: (str(c.get('year_level') or ''), str(c.get('major') or ''), str(c.get('course_name') or '')))

    counts = {
        'all': len(all_courses),
        '1st': len([c for c in all_courses if str(c.get('year_level')) == '1']),
        '2nd': len([c for c in all_courses if str(c.get('year_level')) == '2']),
        '3rd': len([c for c in all_courses if str(c.get('year_level')) == '3']),
        '4th': len([c for c in all_courses if str(c.get('year_level')) == '4']),
    }

    course_message = session.pop('course_message', None)
    return render_template('courses.html', active_page='courses', courses=all_courses, counts=counts, program=user_program, selected_program=program_filter, course_message=course_message)

#-------------------------------------------------------search_courses----------------------------------------------------------------------------------------------
@app.route('/search_courses', methods=['GET'])
@login_required
def search_courses():
    query_str = request.args.get('q', '').strip()
    user_program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    program_filter = request.args.get('program', '').strip()

    if not query_str:
        return jsonify({'courses': [], 'exact_match': False})

    try:
        cols = 'course_id, course_name, program, year_level, major, lecture_hours, lab_hours, ilp_hours'
        query = supabase.table('course').select(cols)
        exact_query = supabase.table('course').select('course_id')

        if user_role == 'admin':
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program', program_filter)
                exact_query = exact_query.eq('program', program_filter)
        else:
            if user_program:
                query = query.eq('program', user_program)
                exact_query = exact_query.eq('program', user_program)

        matching_courses = query.ilike('course_name', f'%{query_str}%').order('course_name').limit(10).execute().data or []
        exact = exact_query.ilike('course_name', query_str).limit(1).execute()
        exact_match_row = _first(exact.data or [])

        return jsonify({
            'courses': matching_courses,
            'exact_match': exact_match_row is not None
        })
    except Exception as err:
        return jsonify({'error': str(err), 'courses': [], 'exact_match': False}), 500


@app.route('/api/courses')
@login_required
def api_courses():
    user_role = session.get('role', 'Viewer')
    user_program = session.get('program', '')
    program_filter = request.args.get('program', '').strip()
    year_level = request.args.get('year_level', '').strip()
    semester = request.args.get('semester', '').strip()
    major = request.args.get('major', '').strip()

    _ensure_course_semester_column()

    try:
        query = supabase.table('course').select('course_id, course_name, program, year_level, major, semester')

        if user_role == 'admin':
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program', program_filter)
        else:
            if user_program:
                query = query.eq('program', user_program)

        if year_level and year_level.lower() != 'all':
            query = query.eq('year_level', year_level)

        if semester:
            query = query.eq('semester', semester)

        if major:
            query = query.eq('major', major)

        courses = query.order('course_name').execute().data or []
        return jsonify({'courses': courses})
    except Exception as err:
        return jsonify({'error': str(err), 'courses': []}), 500

#-------------------------------------------------------delete_course----------------------------------------------------------------------------------------------
@app.route('/delete_course/<int:course_id>')
@login_required
def delete_course(course_id):
    handled, resp = _request_delete_if_scheduler('course', course_id, f'Course ID {course_id}')
    if handled:
        return resp or redirect(url_for('show_courses'))
    try:
        supabase.table('course').delete().eq('course_id', course_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'course').eq('item_id', str(course_id)).eq('status', 'pending').execute()
        log_activity('delete', 'course', f'Course ID {course_id}')
        flash('Deleted successfully', 'success')
        return redirect(url_for('show_courses'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------edit_course----------------------------------------------------------------------------------------------
@app.route('/edit_course/<int:course_id>', methods=['POST'])
@login_required
def edit_course(course_id):
    try:
        course_name = request.form['course_name']
        lecture_hours = request.form['lecture_hours']
        lab_hours = request.form['lab_hours']
        ilp_hours = request.form['ilp_hours']
        program = session.get('program', '')
        year_level = request.form.get('year_level', '')
        semester = request.form.get('semester', '').strip()

        if not semester:
            session['course_message'] = 'Semester is required.'
            session.modified = True
            return redirect(url_for('show_courses'))

        # Major is required for specific Year Level + Semester combinations
        def is_major_required(year, sem):
            # Major is required for 3rd Year - 2nd Semester
            if year == '3' and sem in ('2nd Semester', '2nd', '2'):
                return True
            # Major is required for 4th Year - 1st Semester
            if year == '4' and sem in ('1st Semester', '1st', '1'):
                return True
            # Major is required for 4th Year - 2nd Semester
            if year == '4' and sem in ('2nd Semester', '2nd', '2'):
                return True
            return False

        major_required = is_major_required(year_level, semester)
        major = request.form.get('major', '').strip() or None if major_required else None

        _ensure_course_semester_column()

        supabase.table('course').update({
            'course_name': course_name,
            'lecture_hours': lecture_hours,
            'lab_hours': lab_hours,
            'ilp_hours': ilp_hours,
            'program': program,
            'year_level': year_level,
            'major': major,
            'semester': semester,
        }).eq('course_id', course_id).execute()
        session.pop('course_message', None)

        log_activity('edit', 'course', course_name)
        flash('Edited successfully', 'success')
        return redirect(url_for('show_courses'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------add_professor----------------------------------------------------------------------------------------------
@app.route('/add_professor', methods=['POST'])
@login_required
def add_professor():
    try:
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        department = _get_department()
        max_hours = int(request.form.get('max_hours', 40) or 40)

        if not first_name or not last_name:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'Please complete all required fields.'}), 400
            return redirect(url_for('professors'))

        # Server-side duplicate check (case-insensitive, trimmed)
        existing = supabase.table('professor').select('prof_id, first_name, last_name').ilike('first_name', first_name).ilike('last_name', last_name).execute()
        if existing.data:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': f'Professor "{first_name} {last_name}" already exists.'}), 400
            return redirect(url_for('professors'))

        insert_res = supabase.table('professor').insert({
            'first_name': first_name,
            'last_name': last_name,
            'department': department,
            'max_hours': max_hours,
        }).execute()
        new_id = _first(insert_res.data or [])
        new_id = new_id.get('prof_id') if isinstance(new_id, dict) else None

        log_activity('create', 'professor', f'{first_name} {last_name}')
        flash('Created successfully', 'success')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'message': 'Professor added successfully.', 'professor': {'prof_id': new_id, 'first_name': first_name, 'last_name': last_name, 'department': department, 'max_hours': max_hours}})

        return redirect(url_for('professors'))
    except Exception as err:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(err)}), 500
        return f"Error: {err}"
#-------------------------------------------------------edit_professor----------------------------------------------------------------------------------------------
@app.route('/edit_professor/<int:professor_id>', methods=['POST'])
@login_required
def edit_professor(professor_id):
    try:
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        department = request.form.get('department', '').strip()
        max_hours = request.form.get('max_hours', 40)

        supabase.table('professor').update({
            'first_name': first_name,
            'last_name': last_name,
            'department': department,
            'max_hours': max_hours,
        }).eq('prof_id', professor_id).execute()

        log_activity('edit', 'professor', f'{first_name} {last_name}')
        flash('Edited successfully', 'success')
        return redirect(url_for('professors'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------delete_professor----------------------------------------------------------------------------------------------
@app.route('/delete_professor/<int:professor_id>')
@login_required
def delete_professor(professor_id):
    handled, resp = _request_delete_if_scheduler('professor', professor_id, f'Professor ID {professor_id}')
    if handled:
        return resp or redirect(url_for('professors'))
    try:
        supabase.table('professor').delete().eq('prof_id', professor_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'professor').eq('item_id', str(professor_id)).eq('status', 'pending').execute()

        log_activity('delete', 'professor', f'Professor ID {professor_id}')
        flash('Deleted successfully', 'success')
        return redirect(url_for('professors'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------show_professors----------------------------------------------------------------------------------------------
@app.route('/professors')
@scheduler_required
def professors():
    user_role = session.get('role', 'Viewer')
    department = _get_department()
    dept_filter = request.args.get('department', '').strip()

    query = supabase.table('professor').select('*')
    if user_role == 'admin':
        if dept_filter and dept_filter.lower() != 'all':
            query = query.eq('department', dept_filter)
    else:
        if department:
            query = query.eq('department', department)
        else:
            return render_template('professors.html', active_page='professors', professors=[], department=department)

    all_professors = query.execute().data or []
    all_professors.sort(key=lambda p: (str(p.get('last_name') or ''), str(p.get('first_name') or '')))

    return render_template('professors.html', active_page='professors', professors=all_professors, department=department)
#-------------------------------------------------------show_rooms----------------------------------------------------------------------------------------------
@app.route('/rooms')
@scheduler_required
def rooms():
    user_role = session.get('role', 'Viewer')
    department = _get_department()
    dept_filter = request.args.get('department', '').strip()

    query = supabase.table('room').select('*')
    if user_role == 'admin':
        if dept_filter and dept_filter.lower() != 'all':
            query = query.eq('department', dept_filter)
    else:
        if department:
            query = query.eq('department', department)
        else:
            return render_template('room.html', active_page='rooms', rooms=[], department=department)

    all_rooms = query.execute().data or []
    all_rooms.sort(key=lambda r: str(r.get('room_name') or ''))

    return render_template('room.html', active_page='rooms', rooms=all_rooms, department=department)

#-------------------------------------------------------search_rooms----------------------------------------------------------------------------------------------
@app.route('/search_rooms', methods=['GET'])
@login_required
def search_rooms():
    query_str = request.args.get('q', '').strip()
    user_role = session.get('role', 'Viewer')
    department = _get_department()
    dept_filter = request.args.get('department', '').strip()

    if not query_str:
        return jsonify({'rooms': [], 'exact_match': False})

    try:
        cols = 'room_id, room_name, room_type, department'
        query = supabase.table('room').select(cols)
        exact_query = supabase.table('room').select('room_id')

        if user_role == 'admin':
            if dept_filter and dept_filter.lower() != 'all':
                query = query.eq('department', dept_filter)
                exact_query = exact_query.eq('department', dept_filter)
        else:
            if not department:
                return jsonify({'rooms': [], 'exact_match': False})
            query = query.eq('department', department)
            exact_query = exact_query.eq('department', department)

        matching_rooms = query.ilike('room_name', f'%{query_str}%').order('room_name').limit(10).execute().data or []
        exact = exact_query.ilike('room_name', query_str).limit(1).execute()
        exact_match_row = _first(exact.data or [])

        return jsonify({
            'rooms': matching_rooms,
            'exact_match': exact_match_row is not None
        })
    except Exception as err:
        return jsonify({'error': str(err), 'rooms': [], 'exact_match': False}), 500

@app.route('/add_room', methods=['POST'])
@login_required
def add_room():
    try:
        room_name = request.form['room_name']
        room_type = request.form['room_type']
        department = request.form['department']

        supabase.table('room').insert({
            'room_name': room_name,
            'room_type': room_type,
            'department': department,
        }).execute()

        log_activity('create', 'room', room_name)
        flash('Created successfully', 'success')
        return redirect(url_for('rooms'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------edit_room----------------------------------------------------------------------------------------------
@app.route('/edit_room/<int:room_id>', methods=['POST'])
@login_required
def edit_room(room_id):
    try:
        room_name = request.form['room_name']
        room_type = request.form['room_type']

        supabase.table('room').update({
            'room_name': room_name,
            'room_type': room_type,
        }).eq('room_id', room_id).execute()

        log_activity('edit', 'room', room_name)
        flash('Edited successfully', 'success')
        return redirect(url_for('rooms'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------delete_room----------------------------------------------------------------------------------------------
@app.route('/delete_room/<int:room_id>')
@login_required
def delete_room(room_id):
    handled, resp = _request_delete_if_scheduler('room', room_id, f'Room ID {room_id}')
    if handled:
        return resp or redirect(url_for('rooms'))
    try:
        supabase.table('room').delete().eq('room_id', room_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'room').eq('item_id', str(room_id)).eq('status', 'pending').execute()

        log_activity('delete', 'room', f'Room ID {room_id}')
        flash('Deleted successfully', 'success')
        return redirect(url_for('rooms'))
    except Exception as err:
        return f"Error: {err}"

#-------------------------------------------------------show_prof_course----------------------------------------------------------------------------------------------
@app.route('/prof_course')
@scheduler_required
def prof_course():
    user_role = session.get('role', 'Viewer')
    department = _get_department()
    program = session.get('program', '')

    # Admin and Scheduler see all prof_course relations, Viewer sees only their program/department
    if user_role == 'Viewer':
        if not department or not program:
            pc_rows = []
        else:
            pc_query = supabase.table('prof_course').select(
                'prof_course_id, prof_id, course_id, course(course_name, program), professor(first_name, last_name, department)'
            ).eq('course.program', program).eq('professor.department', department)
            pc_rows = pc_query.execute().data or []
    else:
        pc_query = supabase.table('prof_course').select(
            'prof_course_id, prof_id, course_id, course(course_name, program), professor(first_name, last_name, department)'
        )
        pc_rows = pc_query.execute().data or []

    all_prof_course = []
    for row in pc_rows:
        c = _rel(row, 'course') or {}
        p = _rel(row, 'professor') or {}
        all_prof_course.append({
            'prof_course_id': row.get('prof_course_id'),
            'prof_id': row.get('prof_id'),
            'course_id': row.get('course_id'),
            'course_name': c.get('course_name'),
            'program': c.get('program'),
            'prof_first_name': p.get('first_name'),
            'prof_last_name': p.get('last_name'),
            'prof_department': p.get('department'),
        })
    all_prof_course.sort(key=lambda x: (str(x.get('prof_id') or ''), str(x.get('course_id') or '')))

    # Admin and Scheduler see all professors, Viewer sees only their department's professors
    if user_role == 'Viewer':
        if not department:
            professors = []
        else:
            prof_query = supabase.table('professor').select('prof_id, first_name, last_name, department').eq('department', department)
            professors = prof_query.execute().data or []
    else:
        prof_query = supabase.table('professor').select('prof_id, first_name, last_name, department')
        professors = prof_query.execute().data or []
    professors.sort(key=lambda p: (str(p.get('last_name') or ''), str(p.get('first_name') or '')))

    # Admin and Scheduler see all courses, Viewer sees only their program's courses
    if user_role == 'Viewer':
        if not program:
            courses = []
        else:
            course_query = supabase.table('course').select('course_id, course_name, program, year_level').eq('program', program)
            courses = course_query.execute().data or []
    else:
        course_query = supabase.table('course').select('course_id, course_name, program, year_level')
        courses = course_query.execute().data or []
    courses.sort(key=lambda c: str(c.get('course_name') or ''))

    return render_template('prof_course.html', active_page='prof_course', prof_courses=all_prof_course, professors=professors, courses=courses)
#-------------------------------------------------------add_prof_course----------------------------------------------------------------------------------------------
@app.route('/add_prof_course', methods=['POST'])
@login_required
def add_prof_course():
        prof_id = request.form.get('prof_id')
        course_ids = request.form.getlist('course_ids')

        try:
            rows = [{'prof_id': int(prof_id), 'course_id': int(course_id)} for course_id in course_ids]
            if rows:
                supabase.table('prof_course').insert(rows).execute()

            log_activity('create', 'prof_course', f'Assigned {len(course_ids)} courses to Prof ID {prof_id}')
            flash('Saved successfully', 'success')

        except Exception:
            pass

        return redirect(url_for('prof_course'))
#-------------------------------------------------------update_prof_with_courses----------------------------------------------------------------------------------------------
@app.route('/update_prof_with_courses/<int:prof_id>', methods=['POST'])
@login_required
def update_prof_with_courses(prof_id):
    try:
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        department = request.form.get('department', '').strip()
        max_hours = request.form.get('max_hours', 40)
        course_ids = request.form.getlist('course_ids')

        # 1. Update professor table
        supabase.table('professor').update({
            'first_name': first_name,
            'last_name': last_name,
            'department': department,
            'max_hours': max_hours,
        }).eq('prof_id', prof_id).execute()

        # 2. Clear existing course assignments for this professor
        supabase.table('prof_course').delete().eq('prof_id', prof_id).execute()

        # 3. Insert newly selected course assignments
        if course_ids:
            rows = [{'prof_id': prof_id, 'course_id': int(cid)} for cid in course_ids]
            supabase.table('prof_course').insert(rows).execute()

        log_activity('edit', 'prof_course', f'Updated assignments for Prof ID {prof_id}')
        flash('Updated successfully', 'success')
    except Exception as err:
        return f"Error: {err}"

    return redirect(url_for('prof_course'))
#-------------------------------------------------------edit_prof_course----------------------------------------------------------------------------------------------
@app.route('/edit_prof_course/<int:prof_course_id>', methods=['POST'])
@login_required
def edit_prof_course(prof_course_id):
    try:
        prof_id = request.form['prof_id']
        course_id = request.form['course_id']

        supabase.table('prof_course').update({
            'prof_id': prof_id,
            'course_id': course_id,
        }).eq('prof_course_id', prof_course_id).execute()
        log_activity('edit', 'prof_course', f'Prof-Course ID {prof_course_id}')
        flash('Edited successfully', 'success')
        return redirect(url_for('prof_course'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------delete_prof_course_all----------------------------------------------------------------------------------------------
@app.route('/delete_prof_course_all/<int:prof_id>')
@login_required
def delete_prof_course_all(prof_id):
    handled, resp = _request_delete_if_scheduler('prof_course_all', prof_id, f'All Course Assignments for Prof ID {prof_id}')
    if handled:
        return resp or redirect(url_for('prof_course'))
    try:
        supabase.table('prof_course').delete().eq('prof_id', prof_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'prof_course_all').eq('item_id', str(prof_id)).eq('status', 'pending').execute()
        log_activity('delete', 'prof_course', f'All assignments for Prof ID {prof_id}')
        flash('Deleted successfully', 'success')
        return redirect(url_for('prof_course'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------delete_prof_course----------------------------------------------------------------------------------------------
@app.route('/delete_prof_course/<int:prof_course_id>')
@login_required
def delete_prof_course(prof_course_id):
    handled, resp = _request_delete_if_scheduler('prof_course', prof_course_id, f'Prof-Course ID {prof_course_id}')
    if handled:
        return resp or redirect(url_for('prof_course'))
    try:
        supabase.table('prof_course').delete().eq('prof_course_id', prof_course_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'prof_course').eq('item_id', str(prof_course_id)).eq('status', 'pending').execute()
        log_activity('delete', 'prof_course', f'Prof-Course ID {prof_course_id}')
        flash('Deleted successfully', 'success')
        return redirect(url_for('prof_course'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------time helpers----------------------------------------------------------------------------------------------
def _format_time(value):
    # Format a timedelta or time-like object/string as a 12-hour clock string (HH:MM AM/PM)
    if not value:
        return ''
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        suffix = 'AM' if hours < 12 else 'PM'
        hour_12 = hours % 12 or 12
        return f"{hour_12:02d}:{minutes:02d} {suffix}"
    if isinstance(value, str):
        td = _parse_time(value)
        if td is not None:
            total_seconds = int(td.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            suffix = 'AM' if hours < 12 else 'PM'
            hour_12 = hours % 12 or 12
            return f"{hour_12:02d}:{minutes:02d} {suffix}"
    return str(value)


def _parse_time(value):
    # Parse value (timedelta, time-like, or string) into a timedelta, preserving AM/PM correctly
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value
    if hasattr(value, 'hour'):
        return timedelta(hours=value.hour, minutes=value.minute, seconds=getattr(value, 'second', 0))
    if isinstance(value, str):
        val_str = value.strip().upper()
        if not val_str:
            return None

        is_pm = 'PM' in val_str
        is_am = 'AM' in val_str

        # Remove AM/PM indicators to extract numeric time string
        clean_time = val_str.replace('AM', '').replace('PM', '').strip()
        if ' ' in clean_time:
            clean_time = clean_time.split(' ', 1)[0]

        try:
            parts = clean_time.split(':')
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            seconds = int(parts[2]) if len(parts) > 2 else 0

            if is_pm:
                if hour < 12:
                    hour += 12
            elif is_am:
                if hour == 12:
                    hour = 0

            return timedelta(hours=hour, minutes=minute, seconds=seconds)
        except (ValueError, IndexError):
            return None
    return None


def _to_time_string(value):
    # Normalise any time-like value to a Postgres-friendly "HH:MM:SS" string.
    if value is None:
        return None
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    td = _parse_time(value)
    if td is not None:
        total = int(td.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return str(value) or None


def _to_seconds(value):
    # Convert time-like object, timedelta, or string to total seconds for reliable comparisons
    td = _parse_time(value)
    if td is not None:
        return int(td.total_seconds())
    return 0


def _is_blocked_by_lunch(slot_start, slot_end, lunch_time):
    # Return True if the candidate slot overlaps the lunch period
    if lunch_time is None:
        return False

    lunch_start_sec = _to_seconds(lunch_time)
    if lunch_start_sec == 0 and lunch_time != 0:
        return False

    slot_start_sec = _to_seconds(slot_start)
    slot_end_sec = _to_seconds(slot_end)
    lunch_end_sec = lunch_start_sec + 3600

    return slot_start_sec < lunch_end_sec and lunch_start_sec < slot_end_sec


def _build_candidate_slots(timeslots):
    # Build one-hour candidate slots using timeslot rows that specify a day range
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_to_index = {d: i for i, d in enumerate(days)}
    candidate_slots = []

    for row in timeslots:
        # Read day range from row; default to Monday..Friday when missing/invalid
        start_day = (row.get('start_day') or 'Monday').strip().title()
        end_day = (row.get('end_day') or 'Friday').strip().title()
        start_idx = day_to_index.get(start_day, 0)
        end_idx = day_to_index.get(end_day, 4)
        if end_idx < start_idx:
            # if end is before start, treat as single-day
            end_idx = start_idx

        start_time = _parse_time(row.get('start_time'))
        end_time = _parse_time(row.get('end_time'))
        lunch_time = _parse_time(row.get('lunch_time'))

        if start_time is None or end_time is None or end_time <= start_time:
            continue

        for idx in range(start_idx, end_idx + 1):
            day = days[idx]
            current = start_time
            while current + timedelta(hours=1) <= end_time:
                slot_end = current + timedelta(hours=1)
                if _is_blocked_by_lunch(current, slot_end, lunch_time):
                    current = slot_end
                    continue

                candidate_slots.append({
                    'day': day,
                    'start_time': current,
                    'end_time': slot_end,
                })
                current = slot_end

    # Fallback: if no timeslots configured, generate a default 7:00-20:00 Monday-Sunday grid
    if not candidate_slots:
        for day in days:
            current = timedelta(hours=7)
            while current + timedelta(hours=1) <= timedelta(hours=20):
                candidate_slots.append({'day': day, 'start_time': current, 'end_time': current + timedelta(hours=1)})
                current += timedelta(hours=1)

    return candidate_slots


def _is_contiguous_block(block_slots):
    # Check that each slot directly follows the previous (no gaps)
    for idx in range(1, len(block_slots)):
        previous = block_slots[idx - 1]
        current = block_slots[idx]
        if previous['end_time'] != current['start_time']:
            return False
    return True


def _has_conflict(day, start_time, end_time, bookings):
    # Check whether the given time range conflicts with any existing booking using total seconds comparison
    start_sec = _to_seconds(start_time)
    end_sec = _to_seconds(end_time)

    for existing_day, existing_start, existing_end in bookings:
        if day != existing_day:
            continue
        ex_start_sec = _to_seconds(existing_start)
        ex_end_sec = _to_seconds(existing_end)
        if start_sec < ex_end_sec and ex_start_sec < end_sec:
            return True
    return False


def _get_year_rules(year_level):
    year = int(year_level or 1)
    rules = {
        1: {'max_courses_per_day': 2, 'max_late_days': 0, 'label': '1st Year'},
        2: {'max_courses_per_day': 2, 'max_late_days': 2, 'label': '2nd Year'},
        3: {'max_courses_per_day': 3, 'max_late_days': 99, 'label': '3rd Year'},
        4: {'max_courses_per_day': 3, 'max_late_days': 99, 'label': '4th Year'},
    }
    return rules.get(year, rules[1])

def _slot_end_time(slot):
    return slot['end_time']

def _is_late_slot(slot, late_threshold=None):
    if late_threshold is None:
        late_threshold = timedelta(hours=17)
    return slot['start_time'] >= late_threshold

def _score_day_for_section(day, year_level, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used=False):
    rules = _get_year_rules(year_level)
    max_per_day = rules['max_courses_per_day']
    max_late = rules['max_late_days']
    current_count = courses_per_day.get(day, 0)
    current_late = len(late_days)

    # Hard-constraint violations return score of -1 (reject)
    if year_level == 1:
        if current_count >= max_per_day:
            return -1
        # For 1st year, only allow at most ONE day to have 2 courses
        if current_count == 1 and two_course_day_used:
            return -1
    else:
        if current_count >= max_per_day:
            return -1

    if slot_is_late and current_late >= max_late and day not in late_days:
        return -1

    score = 0
    if year_level == 1:
        # 1st Year: strongly prefer days with fewer courses (spread out)
        score -= current_count * 100
        score -= days_tried.get(day, 0) * 10
        if slot_is_late:
            score -= 1000  # Heavy penalty for late slots
    elif year_level == 2:
        score -= current_count * 50
        score -= days_tried.get(day, 0) * 5
        if slot_is_late:
            score -= 300
            if day in late_days:
                score -= 200
    elif year_level in (3, 4):
        # Prefer days with 1 course already (to reach 2/day ideal)
        if current_count == 0:
            score += 10  # Good to fill empty day
        elif current_count == 1:
            score += 50  # Ideal: 2 per day
        else:
            score -= 20 * current_count  # Penalize going beyond 2
        score -= days_tried.get(day, 0) * 2
        if slot_is_late:
            score -= 100

    return score

def _build_subject_session_queue(course):
    lec_hours = int(course.get('lecture_hours') or 0)
    lab_hours = int(course.get('lab_hours') or 0)

    if lec_hours > 0 and lab_hours > 0:
        return [{'paired': True, 'lec_duration': lec_hours, 'lab_duration': lab_hours}]
    elif lec_hours > 0:
        return [{'paired': False, 'session_type': 'Lecture', 'duration': lec_hours}]
    elif lab_hours > 0:
        return [{'paired': False, 'session_type': 'Laboratory', 'duration': lab_hours}]
    return []


def _canonical_major_name(major_key):
    if not major_key:
        return None
    k = str(major_key).strip().lower()
    if 'database' in k:
        return 'Database Systems'
    elif 'web' in k:
        return 'Web Development'
    elif 'network' in k:
        return 'Networking'
    return str(major_key).strip()


def _major_code(major_name):
    if not major_name:
        return ''
    k = str(major_name).strip().lower()
    if 'database' in k:
        return 'DB'
    elif 'web' in k:
        return 'WEB'
    elif 'network' in k:
        return 'NET'
    clean = ''.join(c for c in str(major_name) if c.isalnum())
    return clean[:3].upper() if clean else 'MAJ'


def _major_prefix_letter(major_name):
    k = str(major_name or '').strip().lower()
    if 'database' in k:
        return 'D'
    elif 'web' in k:
        return 'W'
    elif 'network' in k:
        return 'N'
    clean = ''.join(c for c in str(major_name) if c.isalpha())
    return clean[0].upper() if clean else 'M'


def _generate_sections(year_level, total_students, section_count=None, majors=None, sections_by_major=None):
    # Create section identifiers (e.g., 1A, 1B, 3A-DB, 3A-WEB...) and distribute students evenly
    year_level = str(year_level or '1')
    total_students = max(0, int(total_students or 0))
    sections = []

    # If sections_by_major is provided (e.g., {'database': 5, 'web': 4, 'networking': 4})
    if sections_by_major and isinstance(sections_by_major, dict):
        total_sec = sum(max(0, int(v or 0)) for v in sections_by_major.values())
        if total_sec > 0:
            base_students = total_students // total_sec
            remainder = total_students % total_sec
            student_idx = 0

            for m_key, count in sections_by_major.items():
                cnt = max(0, int(count or 0))
                if cnt <= 0:
                    continue
                canonical_m = _canonical_major_name(m_key)
                m_code = _major_code(canonical_m)

                for idx in range(cnt):
                    stu = base_students + (1 if student_idx < remainder else 0)
                    student_idx += 1
                    letter = chr(ord('A') + idx)
                    section_name = f"{year_level}{letter}-{m_code}" if m_code else f"{year_level}{letter}"
                    sections.append({
                        'section': section_name,
                        'section_name': section_name,
                        'year_level': year_level,
                        'student_count': stu,
                        'major': canonical_m,
                    })
            if sections:
                return sections

    # Fallback / standard single-pool section generation (1st, 2nd, 4th year, or 3rd year 1st sem)
    section_count = max(1, int(section_count or 1))
    base_students = total_students // section_count
    remainder = total_students % section_count

    clean_majors = [m for m in (majors or []) if m and str(m).strip().lower() not in ('general', 'none', 'null', '')]

    for index in range(section_count):
        students_in_section = base_students + (1 if index < remainder else 0)
        letter = chr(ord('A') + index)
        sec_major = clean_majors[index % len(clean_majors)] if clean_majors else None
        m_code = _major_code(sec_major) if sec_major else ''
        section_name = f"{year_level}{letter}-{m_code}" if m_code else f"{year_level}{letter}"
        sections.append({
            'section': section_name,
            'section_name': section_name,
            'year_level': year_level,
            'student_count': students_in_section,
            'major': sec_major,
        })

    return sections


def _build_section_name_for_year(section_name, year_level):
    """Build a section name using the selected year level while preserving any suffix."""
    if not section_name:
        return section_name

    year_level = str(year_level or '').strip()
    if not year_level:
        return section_name

    if len(section_name) > 1 and section_name[0].isdigit():
        suffix = section_name[1:]
    else:
        suffix = section_name

    return f"{year_level}{suffix}"

#------------------------------------------Show Schedules----------------------------------------------------------------------------------------------
_DAY_ORDER = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}

def _year_of_section(section):
    return str(section)[0] if section else ''

@app.route('/professor_schedule')
@login_required
def professor_schedule():
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    user_first_name = (session.get('first_name') or '').strip()
    user_last_name = (session.get('last_name') or '').strip()

    try:
        sched_cols = 'prof_id, section, semester, major, program'
        query = supabase.table('schedule').select(sched_cols)

        if user_role == 'admin':
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program', program_filter)
        else:
            if program:
                query = query.eq('program', program)

        sched_rows = query.execute().data or []

        year_options = sorted({_year_of_section(r.get('section')) for r in sched_rows if _year_of_section(r.get('section'))})
        semester_options = sorted({r.get('semester') for r in sched_rows if r.get('semester')})
        major_options = sorted({r.get('major') for r in sched_rows if r.get('major')})

        def _matches(r):
            if year_filter and _year_of_section(r.get('section')) != year_filter:
                return False
            if semester_filter and r.get('semester') != semester_filter:
                return False
            if major_filter and r.get('major') != major_filter:
                return False
            return True

        filtered = [r for r in sched_rows if _matches(r)]
        prof_count = {}
        for r in filtered:
            pid = r.get('prof_id')
            if pid is not None:
                prof_count[pid] = prof_count.get(pid, 0) + 1

        professors = []
        if user_role == 'Viewer':
            # Find professor matching user's name (case-insensitive)
            match = supabase.table('professor').select('prof_id, first_name, last_name, department').ilike('first_name', user_first_name).ilike('last_name', user_last_name).execute()
            professor = _first(match.data or [])
            if not professor:
                return render_template('professor_schedule.html', active_page='professor_schedule',
                                      professors=[], year_options=year_options,
                                      semester_options=semester_options, major_options=major_options,
                                      year_filter=year_filter, semester_filter=semester_filter,
                                      major_filter=major_filter, program=program,
                                      no_professor_match=True)
            pid = professor['prof_id']
            prof_name = f"{professor.get('first_name','')} {professor.get('last_name','')}".strip()
            professors.append({
                'professor_id': pid,
                'professor_name': prof_name,
                'department': professor.get('department'),
                'class_count': prof_count.get(pid, 0),
            })
        else:
            all_profs = (supabase.table('professor').select('prof_id, first_name, last_name, department').execute().data) or []
            for p in all_profs:
                pid = p.get('prof_id')
                if pid in prof_count:
                    professors.append({
                        'professor_id': pid,
                        'professor_name': f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                        'department': p.get('department'),
                        'class_count': prof_count.get(pid, 0),
                    })
            professors.sort(key=lambda x: x['professor_name'])
    except Exception:
        professors = []
        year_options = []
        semester_options = []
        major_options = []

    return render_template('professor_schedule.html', active_page='professor_schedule',
                          professors=professors, year_options=year_options,
                          semester_options=semester_options, major_options=major_options,
                          year_filter=year_filter, semester_filter=semester_filter,
                          major_filter=major_filter, program=program,
                          no_professor_match=False)


@app.route('/professor_schedule/<professor_id>')
@login_required
def view_professor_schedule(professor_id):
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    prof_res = supabase.table('professor').select('prof_id, first_name, last_name, department').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])

    if not professor:
        return redirect(url_for('professor_schedule'))

    professor_name = f"{professor['first_name']} {professor['last_name']}"

    query = supabase.table('schedule').select(
        'schedule_id, course_id, prof_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
        'course(course_name), room(room_name)'
    ).eq('prof_id', professor_id)

    if user_role == 'admin':
        if program_filter and program_filter.lower() != 'all':
            query = query.eq('program', program_filter)
    else:
        if program:
            query = query.eq('program', program)

    if year_filter:
        query = query.like('section', f'{year_filter}%')
    if semester_filter:
        query = query.eq('semester', semester_filter)
    if major_filter:
        query = query.eq('major', major_filter)

    rows = query.execute().data or []

    sort_day = request.args.get('sort_day', 'asc').lower()
    day_order = _DAY_ORDER if sort_day != 'desc' else {d: 6 - i for i, d in enumerate(_DAY_ORDER)}

    entries = []
    for row in rows:
        c = _rel(row, 'course') or {}
        r = _rel(row, 'room') or {}
        entries.append({
            'schedule_id': row['schedule_id'],
            'course_name': c.get('course_name'),
            'room': r.get('room_name'),
            'day': row['day'],
            'start_time': row['class_start'],
            'end_time': row['class_end'],
            'section': row['section'],
            'semester': row['semester'],
            'major': row['major'],
            'session_type': row['session_type'],
            'year_level': _year_of_section(row.get('section')),
        })

    entries.sort(key=lambda e: (day_order.get(e.get('day') or '', 99), str(e.get('start_time') or '')))

    return render_template('generated_professor_schedule.html', active_page='professor_schedule',
                          professor=professor, professor_name=professor_name, entries=entries,
                          year_filter=year_filter, semester_filter=semester_filter,
                          major_filter=major_filter, program=program, sort_day=sort_day)


@app.route('/room_schedule')
@login_required
def room_schedule():
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    user_first_name = (session.get('first_name') or '').strip()
    user_last_name = (session.get('last_name') or '').strip()

    try:
        sched_cols = 'prof_id, room_id, section, semester, major, program'
        query = supabase.table('schedule').select(sched_cols)

        if user_role == 'admin':
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program', program_filter)
        else:
            if program:
                query = query.eq('program', program)

        sched_rows = query.execute().data or []

        year_options = sorted({_year_of_section(r.get('section')) for r in sched_rows if _year_of_section(r.get('section'))})
        semester_options = sorted({r.get('semester') for r in sched_rows if r.get('semester')})
        major_options = sorted({r.get('major') for r in sched_rows if r.get('major')})

        def _matches(r):
            if year_filter and _year_of_section(r.get('section')) != year_filter:
                return False
            if semester_filter and r.get('semester') != semester_filter:
                return False
            if major_filter and r.get('major') != major_filter:
                return False
            return True

        filtered = [r for r in sched_rows if _matches(r)]
        room_count = {}
        for r in filtered:
            rid = r.get('room_id')
            if rid is not None:
                room_count[rid] = room_count.get(rid, 0) + 1

        rooms = []
        if user_role == 'Viewer':
            match = supabase.table('professor').select('prof_id').ilike('first_name', user_first_name).ilike('last_name', user_last_name).execute()
            professor = _first(match.data or [])
            if not professor:
                return render_template('room_schedule.html', active_page='room_schedule',
                                      rooms=[], year_options=year_options,
                                      semester_options=semester_options, major_options=major_options,
                                      year_filter=year_filter, semester_filter=semester_filter,
                                      major_filter=major_filter, program=program,
                                      no_professor_match=True)
            prof_id = professor['prof_id']
            prof_rooms = [r for r in filtered if r.get('prof_id') == prof_id]
            room_ids = {r.get('room_id') for r in prof_rooms if r.get('room_id') is not None}
            room_rows = []
            if room_ids:
                room_rows = (supabase.table('room').select('room_id, room_name, room_type').in_('room_id', list(room_ids)).execute().data) or []
            for rr in room_rows:
                rid = rr['room_id']
                rooms.append({
                    'room_id': rid,
                    'room_name': rr.get('room_name'),
                    'room_type': rr.get('room_type'),
                    'class_count': room_count.get(rid, 0),
                })
        else:
            all_rooms = (supabase.table('room').select('room_id, room_name, room_type').execute().data) or []
            for rr in all_rooms:
                rid = rr.get('room_id')
                if rid in room_count:
                    rooms.append({
                        'room_id': rid,
                        'room_name': rr.get('room_name'),
                        'room_type': rr.get('room_type'),
                        'class_count': room_count.get(rid, 0),
                    })
            rooms.sort(key=lambda x: str(x.get('room_name') or ''))
    except Exception:
        rooms = []
        year_options = []
        semester_options = []
        major_options = []

    return render_template('room_schedule.html', active_page='room_schedule',
                          rooms=rooms, year_options=year_options,
                          semester_options=semester_options, major_options=major_options,
                          year_filter=year_filter, semester_filter=semester_filter,
                          major_filter=major_filter, program=program,
                          no_professor_match=False)


@app.route('/room_schedule/<room_id>')
@login_required
def view_room_schedule(room_id):
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    room_res = supabase.table('room').select('room_id, room_name, room_type').eq('room_id', room_id).execute()
    room = _first(room_res.data or [])

    if not room:
        return redirect(url_for('room_schedule'))

    query = supabase.table('schedule').select(
        'schedule_id, course_id, prof_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
        'course(course_name), professor(first_name, last_name)'
    ).eq('room_id', room_id)

    if user_role == 'admin':
        if program_filter and program_filter.lower() != 'all':
            query = query.eq('program', program_filter)
    else:
        if program:
            query = query.eq('program', program)

    if year_filter:
        query = query.like('section', f'{year_filter}%')
    if semester_filter:
        query = query.eq('semester', semester_filter)
    if major_filter:
        query = query.eq('major', major_filter)

    rows = query.execute().data or []

    sort_day = request.args.get('sort_day', 'asc').lower()
    day_order = _DAY_ORDER if sort_day != 'desc' else {d: 6 - i for i, d in enumerate(_DAY_ORDER)}

    entries = []
    for row in rows:
        c = _rel(row, 'course') or {}
        p = _rel(row, 'professor') or {}
        entries.append({
            'schedule_id': row['schedule_id'],
            'course_name': c.get('course_name'),
            'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or None,
            'day': row['day'],
            'start_time': row['class_start'],
            'end_time': row['class_end'],
            'section': row['section'],
            'semester': row['semester'],
            'major': row['major'],
            'session_type': row['session_type'],
            'year_level': _year_of_section(row.get('section')),
        })

    entries.sort(key=lambda e: (day_order.get(e.get('day') or '', 99), str(e.get('start_time') or '')))

    return render_template('generated_room_schedule.html', active_page='room_schedule',
                          room=room, entries=entries,
                          year_filter=year_filter, semester_filter=semester_filter,
                          major_filter=major_filter, program=program, sort_day=sort_day)


@app.route('/schedules')
@login_required
def schedules():
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    user_first_name = (session.get('first_name') or '').strip()
    user_last_name = (session.get('last_name') or '').strip()

    try:
        sched_cols = (
            'schedule_id, prof_id, section, semester, major, program, day, class_start, class_end, session_type, course_id, room_id, '
            'course(course_name), room(room_name), professor(first_name, last_name)'
        )
        query = supabase.table('schedule').select(sched_cols)

        if user_role == 'admin':
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program', program_filter)
        elif user_role == 'Scheduler':
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program', program_filter)
            elif program:
                query = query.or_(f'program.eq.{program},program.is.null,program.eq.')
        else:
            if program:
                query = query.or_(f'program.eq.{program},program.is.null,program.eq.')

        sched_rows = query.execute().data or []

        year_options = sorted({_year_of_section(r.get('section')) for r in sched_rows if _year_of_section(r.get('section'))})
        semester_options = sorted({r.get('semester') for r in sched_rows if r.get('semester')})
        major_options = sorted({r.get('major') for r in sched_rows if r.get('major')})

        def _matches(r):
            if year_filter and _year_of_section(r.get('section')) != year_filter:
                return False
            if semester_filter and r.get('semester') != semester_filter:
                return False
            if major_filter and r.get('major') != major_filter:
                return False
            return True

        target_rows = []
        if user_role == 'Viewer':
            match = supabase.table('professor').select('prof_id').ilike('first_name', user_first_name).ilike('last_name', user_last_name).execute()
            professor = _first(match.data or [])
            if not professor:
                return render_template(
                    'schedules.html',
                    active_page='schedules',
                    sections=[],
                    sections_with_entries=[],
                    year_groups=[],
                    year_options=year_options,
                    semester_options=semester_options,
                    major_options=major_options,
                    year_filter=year_filter,
                    semester_filter=semester_filter,
                    major_filter=major_filter,
                    no_professor_match=True
                )

            prof_id = professor['prof_id']
            for r in sched_rows:
                if r.get('prof_id') == prof_id and _matches(r):
                    target_rows.append(r)
        else:
            for r in sched_rows:
                if _matches(r):
                    target_rows.append(r)

        sections = []
        sections_by_key = {}
        seen = set()

        for r in target_rows:
            sec = r.get('section')
            if not sec:
                continue
            semester = r.get('semester', '')
            major_key = r.get('major')
            key = (sec, semester, major_key)

            if key not in seen:
                seen.add(key)
                sections.append({
                    'section': sec,
                    'section_name': sec,
                    'semester': semester,
                    'major': major_key,
                    'year_level': _year_of_section(sec),
                })

            if key not in sections_by_key:
                sections_by_key[key] = {
                    'section': {
                        'section': sec,
                        'section_name': sec,
                        'semester': semester,
                        'major': major_key,
                        'year_level': _year_of_section(sec),
                    },
                    'entries': []
                }

            if r.get('schedule_id'):
                c = _rel(r, 'course') or {}
                rm = _rel(r, 'room') or {}
                p = _rel(r, 'professor') or {}
                fname = p.get('first_name') or ''
                lname = p.get('last_name') or ''
                prof_name = f"{fname} {lname}".strip() or 'TBA'
                start_fmt = _format_time(r.get('class_start'))
                end_fmt = _format_time(r.get('class_end'))
                sections_by_key[key]['entries'].append({
                    'id': r.get('schedule_id'),
                    'schedule_id': r.get('schedule_id'),
                    'course_id': r.get('course_id'),
                    'course_name': c.get('course_name') or 'TBA',
                    'professor_name': prof_name,
                    'room_name': rm.get('room_name') or 'TBA',
                    'day': r.get('day') or '',
                    'start': start_fmt,
                    'end': end_fmt,
                    'time_range': f"{r.get('day')} | {start_fmt} - {end_fmt}" if r.get('day') and start_fmt else 'TBA',
                    'session_type': r.get('session_type') or 'Lecture',
                    'section': sec,
                    'semester': semester,
                    'major': major_key,
                })

        sections.sort(key=lambda s: str(s.get('section') or ''))
        sections_with_entries = list(sections_by_key.values())
        for item in sections_with_entries:
            item['entries'].sort(key=lambda e: (_DAY_ORDER.get(e.get('day') or '', 99), str(e.get('start') or '')))

        year_groups = _group_preview_sections(sections_with_entries)
    except Exception as err:
        logging.error(f"Error loading schedules: {err}")
        sections = []
        sections_with_entries = []
        year_groups = []
        year_options = []
        semester_options = []
        major_options = []

    return render_template(
        'schedules.html',
        active_page='schedules',
        sections=sections,
        sections_with_entries=sections_with_entries,
        year_groups=year_groups,
        year_options=year_options,
        semester_options=semester_options,
        major_options=major_options,
        year_filter=year_filter,
        semester_filter=semester_filter,
        major_filter=major_filter,
        no_professor_match=False
    )


@app.route('/schedule/<section_name>')
@login_required
def view_schedule(section_name):
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    query = supabase.table('schedule').select(
        'schedule_id, day, class_start, class_end, session_type, semester, major, course_id, prof_id, room_id, '
        'course(course_name), room(room_name), professor(first_name, last_name)'
    ).eq('section', section_name)

    # Admin and Scheduler see all programs, Viewer sees only their program
    if user_role == 'Viewer' and program:
        query = query.or_(f'program.eq.{program},program.is.null,program.eq.')

    if semester_filter:
        query = query.eq('semester', semester_filter)
    if major_filter:
        query = query.or_(f'major.eq.{major_filter},major.is.null')

    rows = query.execute().data or []

    entries = []
    for row in rows:
        c = _rel(row, 'course') or {}
        r = _rel(row, 'room') or {}
        p = _rel(row, 'professor') or {}
        first_name = p.get('first_name') or ''
        last_name = p.get('last_name') or ''
        entry = {
            'schedule_id': row['schedule_id'],
            'day': row['day'],
            'class_start': row['class_start'],
            'class_end': row['class_end'],
            'session_type': row['session_type'],
            'semester': row['semester'],
            'major': row['major'],
            'course_name': c.get('course_name'),
            'room_name': r.get('room_name'),
            'first_name': first_name,
            'last_name': last_name,
        }
        entry['time_range'] = f"{entry['day']} | {_format_time(entry['class_start'])} - {_format_time(entry['class_end'])}"
        entry['professor_name'] = f"{first_name} {last_name}".strip() or 'TBA'
        entries.append(entry)

    entries.sort(key=lambda e: (_DAY_ORDER.get(e.get('day') or '', 99), str(e.get('class_start') or '')))

    section = {
        'section': section_name,
        'section_name': section_name,
        'semester': semester_filter or (entries[0].get('semester', '') if entries else ''),
        'major': major_filter or (entries[0].get('major') if entries else None)
    }

    return render_template('generated_schedule.html', active_page='schedules', sections_with_entries=[{'section': section, 'entries': entries}])


@app.route('/delete_schedule/<int:schedule_id>')
@login_required
def delete_schedule(schedule_id):
    section_name = request.args.get('section_name', '')
    semester = request.args.get('semester', '')
    major = request.args.get('major', '')
    handled, resp = _request_delete_if_scheduler('schedule', schedule_id, f'Schedule Entry #{schedule_id} ({section_name})')
    if handled:
        if resp: return resp
        if section_name:
            return redirect(url_for('view_schedule', section_name=section_name, semester=semester, major=major))
        return redirect(url_for('schedules'))
    try:
        supabase.table('schedule').delete().eq('schedule_id', schedule_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'schedule').eq('item_id', str(schedule_id)).eq('status', 'pending').execute()
        log_activity('delete', 'schedule', f'Schedule ID {schedule_id} in {section_name}')
        flash('Deleted successfully', 'success')
    except Exception as err:
        return f"Error: {err}"

    if section_name:
        return redirect(url_for('view_schedule', section_name=section_name, semester=semester, major=major))
    return redirect(url_for('schedules'))


@app.route('/delete_section_schedule/<section_name>', methods=['GET', 'POST'])
@login_required
def delete_section_schedule(section_name):
    major = request.args.get('major', '')
    semester = request.args.get('semester', '')
    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    handled, resp = _request_delete_if_scheduler('section_schedule', section_name, f'Section Schedule {section_name}')
    if handled:
        return resp or redirect(url_for('schedules'))

    try:
        query = supabase.table('schedule').delete().eq('section', section_name)

        if user_role == 'Viewer':
            query = query.eq('program', program)

        if semester:
            query = query.eq('semester', semester)
        if major:
            query = query.eq('major', major)

        query.execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'section_schedule').eq('item_id', str(section_name)).eq('status', 'pending').execute()
        log_activity('delete', 'schedule', f'All entries for section {section_name}')
        flash('Deleted successfully', 'success')
    except Exception as err:
        if request.method == 'POST':
            return jsonify({'success': False, 'message': 'Something went wrong. Please try again.'}), 500
        return f"Error: {err}"

    sections = session.get('generated_sections', [])
    session['generated_sections'] = [s for s in sections if str(s.get('section')) != str(section_name)]

    if request.method == 'POST':
        return jsonify({'success': True, 'message': 'Deleted successfully.'})
    return redirect(url_for('schedules'))


@app.route('/delete_all_schedules', methods=['POST'])
@login_required
def delete_all_schedules():
    payload = request.get_json(silent=True) or {}
    password = (payload.get('password') or '').strip()
    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    if not password:
        return jsonify({'success': False, 'message': 'Please enter your password.'}), 400

    handled, resp = _request_delete_if_scheduler('all_schedules', 'all', 'All Schedules')
    if handled:
        return resp or jsonify({'success': True, 'message': 'Delete Request Sent. Waiting for Administrator approval.'})

    try:
        # Re-verify the user's identity against Supabase Auth before the destructive action.
        email = session.get('email') or session.get('username')
        if not email:
            return jsonify({'success': False, 'message': 'Incorrect password.'}), 401

        check = supabase.auth.sign_in_with_password({'email': email, 'password': password})
        if not check.session:
            return jsonify({'success': False, 'message': 'Incorrect password.'}), 401

        # Admin and Scheduler can delete all schedules, Viewer can only delete their program's schedules
        if user_role == 'Viewer' and program:
            supabase.table('schedule').delete().eq('program', program).execute()
        else:
            supabase.table('schedule').delete().neq('schedule_id', 0).execute()
    except Exception:
        return jsonify({'success': False, 'message': 'Something went wrong. Please try again.'}), 500

    session['generated_sections'] = []
    log_activity('delete', 'schedule', 'Deleted all schedules')
    flash('Deleted successfully', 'success')
    return jsonify({'success': True, 'message': 'All schedules were deleted successfully.'})


@app.route('/edit_schedule/<section_name>', methods=['POST'])
@login_required
def edit_schedule(section_name):
    payload = request.get_json(silent=True) or {}
    year_level = (payload.get('year_level') or '').strip()
    semester = (payload.get('semester') or '').strip()
    major = (payload.get('major') or '').strip()
    program = session.get('program', '')

    if not year_level:
        return jsonify({'success': False, 'message': 'Please select a year level.'}), 400

    new_section_name = _build_section_name_for_year(section_name, year_level)

    try:
        supabase.table('schedule').update({
            'section': new_section_name,
            'semester': semester,
            'major': major,
        }).eq('section', section_name).eq('program', program).execute()
        log_activity('edit', 'schedule', f'Updated section {section_name} to {new_section_name}')
        flash('Updated successfully', 'success')
    except Exception:
        return jsonify({'success': False, 'message': 'Something went wrong. Please try again.'}), 500

    sections = session.get('generated_sections', [])
    for item in sections:
        if str(item.get('section')) == str(section_name):
            item['section'] = new_section_name
            item['section_name'] = new_section_name
            item['year_level'] = year_level
            item['semester'] = semester
            item['major'] = major
            break
    session['generated_sections'] = sections

    return jsonify({
        'success': True,
        'message': 'Schedule updated successfully.',
        'section_name': new_section_name,
        'year_level': year_level,
        'semester': semester,
        'major': major,
    })


@app.route('/edit_schedule_entry/<int:schedule_id>', methods=['POST'])
@login_required
def edit_schedule_entry(schedule_id):
    payload = request.get_json(silent=True) or {}
    course_name = (payload.get('course_name') or '').strip()
    professor_name = (payload.get('professor_name') or '').strip()
    room_name = (payload.get('room_name') or '').strip()
    timeslot = (payload.get('timeslot') or '').strip()
    session_type = (payload.get('session_type') or '').strip()
    program = session.get('program', '')

    if not course_name and not professor_name and not room_name and not timeslot and not session_type:
        return jsonify({'success': False, 'message': 'Please complete at least one field.'}), 400

    try:
        res = supabase.table('schedule').select('*').eq('schedule_id', schedule_id).eq('program', program).execute()
        existing = _first(res.data or [])

        if not existing:
            return jsonify({'success': False, 'message': 'Schedule entry not found.'}), 404

        course_id = existing.get('course_id')
        prof_id = existing.get('prof_id')
        room_id = existing.get('room_id')

        if course_name:
            course_res = supabase.table('course').select('course_id').eq('course_name', course_name).eq('program', program).limit(1).execute()
            course_row = _first(course_res.data or [])
            course_id = course_row['course_id'] if course_row else None

        if professor_name:
            dept = _get_department()
            prof_res = supabase.table('professor').select('prof_id, first_name, last_name').eq('department', dept).execute()
            prof_id = None
            for p in (prof_res.data or []):
                full = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
                if full.lower() == professor_name.lower():
                    prof_id = p['prof_id']
                    break

        if room_name:
            dept = _get_department()
            room_res = supabase.table('room').select('room_id').eq('room_name', room_name).eq('department', dept).limit(1).execute()
            room_row = _first(room_res.data or [])
            room_id = room_row['room_id'] if room_row else None

        supabase.table('schedule').update({
            'course_id': course_id,
            'prof_id': prof_id,
            'room_id': room_id,
            'session_type': session_type or existing.get('session_type'),
        }).eq('schedule_id', schedule_id).eq('program', program).execute()
        flash('Edited successfully', 'success')
    except Exception:
        return jsonify({'success': False, 'message': 'Something went wrong. Please try again.'}), 500

    return jsonify({'success': True, 'message': 'Schedule entry updated successfully.'})

#-------------------------------------------------------add_timeslot----------------------------------------------------------------------------------------------
@app.route('/add_timeslot', methods=['POST'])
@login_required
def add_timeslot():
    # Adding new timeslots via the UI is disabled. Redirect back to timeslot list.
    return redirect(url_for('timeslot'))
#-------------------------------------------------------edit_timeslot----------------------------------------------------------------------------------------------
@app.route('/edit_timeslot/<int:timeslot_id>', methods=['POST'])
@login_required
def edit_timeslot(timeslot_id):
    try:
        # Read editable fields including new day-range columns
        start_day = request.form.get('start_day', 'Monday')
        end_day = request.form.get('end_day', 'Friday')
        start_time = request.form['start_time']
        lunch_time = request.form.get('lunch_time') or None
        end_time = request.form['end_time']

        supabase.table('timeslot').update({
            'start_day': start_day,
            'end_day': end_day,
            'start_time': start_time,
            'lunch_time': lunch_time,
            'end_time': end_time,
        }).eq('timeslot_id', timeslot_id).execute()
        log_activity('edit', 'timeslot', f'Timeslot ID {timeslot_id}')
        flash('Edited successfully', 'success')
        return redirect(url_for('timeslot'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------delete_timeslot----------------------------------------------------------------------------------------------
@app.route('/delete_timeslot/<int:timeslot_id>')
@login_required
def delete_timeslot(timeslot_id):
    handled, resp = _request_delete_if_scheduler('timeslot', timeslot_id, f'Timeslot ID {timeslot_id}')
    if handled:
        return resp or redirect(url_for('timeslot'))
    try:
        supabase.table('timeslot').delete().eq('timeslot_id', timeslot_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'timeslot').eq('item_id', str(timeslot_id)).eq('status', 'pending').execute()
        log_activity('delete', 'timeslot', f'Timeslot ID {timeslot_id}')
        flash('Deleted successfully', 'success')
        return redirect(url_for('timeslot'))
    except Exception as err:
        return f"Error: {err}"

#-------------------------------------------------------Admin Delete Requests----------------------------------------------------------------------------------------------
@app.route('/admin/delete_requests/<int:req_id>/approve', methods=['POST'])
@admin_required
def approve_delete_request(req_id):
    try:
        res = supabase.table('delete_requests').select('*').eq('id', req_id).eq('status', 'pending').execute()
        req = _first(res.data or [])

        if not req:
            msg = 'Delete request not found or already processed.'
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': msg})
            flash(msg, 'error')
            return redirect(request.referrer or url_for('schedules'))

        item_type = req['item_type']
        item_id = req['item_id']
        item_details = req['item_details']

        if item_type == 'course':
            supabase.table('prof_course').delete().eq('course_id', item_id).execute()
            supabase.table('schedule').delete().eq('course_id', item_id).execute()
            supabase.table('course').delete().eq('course_id', item_id).execute()
        elif item_type == 'professor':
            supabase.table('prof_course').delete().eq('prof_id', item_id).execute()
            supabase.table('schedule').delete().eq('prof_id', item_id).execute()
            supabase.table('professor').delete().eq('prof_id', item_id).execute()
        elif item_type == 'room':
            supabase.table('schedule').delete().eq('room_id', item_id).execute()
            supabase.table('room').delete().eq('room_id', item_id).execute()
        elif item_type == 'timeslot':
            supabase.table('timeslot').delete().eq('timeslot_id', item_id).execute()
        elif item_type == 'prof_course':
            supabase.table('prof_course').delete().eq('prof_course_id', item_id).execute()
        elif item_type == 'prof_course_all':
            supabase.table('prof_course').delete().eq('prof_id', item_id).execute()
        elif item_type == 'schedule':
            supabase.table('schedule').delete().eq('schedule_id', item_id).execute()
        elif item_type == 'section_schedule':
            supabase.table('schedule').delete().eq('section', item_id).execute()
        elif item_type == 'all_schedules':
            program = session.get('program', '')
            user_role = session.get('role', '')
            if user_role == 'Viewer' and program:
                supabase.table('schedule').delete().eq('program', program).execute()
            else:
                supabase.table('schedule').delete().neq('schedule_id', 0).execute()

        supabase.table('delete_requests').update({'status': 'approved'}).eq('id', req_id).execute()

        notif_msg = f"Admin approved the deletion of {item_details}."
        supabase.table('scheduler_notifications').insert({
            'user_id': str(req['user_id']) if req.get('user_id') else None,
            'request_id': req_id,
            'message': notif_msg,
            'status': 'approved',
        }).execute()

        cnt_res = supabase.table('delete_requests').select('*', count='exact').eq('status', 'pending').execute()
        rem_cnt = cnt_res.count if cnt_res.count is not None else 0

        log_activity('approve_delete', item_type, f'Approved deletion of {item_details}')
        msg = f'Delete request approved. {item_details} deleted successfully.'
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': msg, 'req_id': req_id, 'remaining_count': rem_cnt})
        flash(msg, 'success')
    except Exception as err:
        msg = f'Error executing deletion: {err}'
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': msg})
        flash(msg, 'error')

    return redirect(request.referrer or url_for('schedules'))


@app.route('/admin/delete_requests/<int:req_id>/reject', methods=['POST'])
@admin_required
def reject_delete_request(req_id):
    try:
        res = supabase.table('delete_requests').select('*').eq('id', req_id).eq('status', 'pending').execute()
        req = _first(res.data or [])

        if not req:
            msg = 'Delete request not found or already processed.'
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': msg})
            flash(msg, 'error')
            return redirect(request.referrer or url_for('schedules'))

        supabase.table('delete_requests').update({'status': 'rejected'}).eq('id', req_id).execute()

        notif_msg = f"Admin rejected the deletion of {req['item_details']}."
        supabase.table('scheduler_notifications').insert({
            'user_id': str(req['user_id']) if req.get('user_id') else None,
            'request_id': req_id,
            'message': notif_msg,
            'status': 'rejected',
        }).execute()

        cnt_res = supabase.table('delete_requests').select('*', count='exact').eq('status', 'pending').execute()
        rem_cnt = cnt_res.count if cnt_res.count is not None else 0

        log_activity('reject_delete', req['item_type'], f'Rejected deletion of {req["item_details"]}')
        msg = f'Delete request for {req["item_details"]} rejected.'
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': msg, 'req_id': req_id, 'remaining_count': rem_cnt})
        flash(msg, 'info')
    except Exception as err:
        msg = f'Database error: {err}'
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': msg})
        flash(msg, 'error')

    return redirect(request.referrer or url_for('schedules'))


@app.route('/notifications/mark_read', methods=['POST'])
@login_required
def mark_notifications_read():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User not logged in.'}), 401
    try:
        supabase.table('scheduler_notifications').update({'is_read': True}).eq('user_id', user_id).execute()
        return jsonify({'success': True})
    except Exception as err:
        return jsonify({'success': False, 'message': str(err)}), 500


#-------------------------------------------------------Irregular Student Scheduling----------------------------------------------------------------------------------------------
@app.route('/irregular_students', methods=['GET', 'POST'])
@scheduler_required
def irregular_students():
    user_role = session.get('role', 'Viewer')
    program = session.get('program', '')

    if request.method == 'POST':
        if user_role == 'Viewer':
            flash('Access denied.', 'error')
            return redirect(url_for('irregular_students'))

        student_id_number = request.form.get('student_id_number', '').strip()
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        student_program = request.form.get('program', '').strip()

        year_levels = [y.strip() for y in request.form.getlist('year_level') if y and y.strip()]
        if not year_levels:
            single_yl = request.form.get('year_level', '').strip()
            if single_yl:
                year_levels = [single_yl]

        year_level_str = ", ".join(year_levels)

        if not student_id_number or not first_name or not last_name or not student_program or not year_level_str:
            flash('All fields are required. Please enter Student ID Number and select at least one Year Level.', 'warning')
            return redirect(url_for('irregular_students'))

        try:
            dup = supabase.table('irregular_students').select('student_id').eq('student_id_number', student_id_number).execute()
            if dup.data:
                flash(f"A student with ID {student_id_number} is already registered.", "warning")
                return redirect(url_for('irregular_students'))

            supabase.table('irregular_students').insert({
                'student_id_number': student_id_number,
                'first_name': first_name,
                'last_name': last_name,
                'program': student_program,
                'year_level': year_level_str,
            }).execute()

            log_activity('add', 'irregular_student', f'[{student_id_number}] {first_name} {last_name} ({student_program} Years: {year_level_str})')
            flash('Irregular student added successfully.', 'success')
        except Exception as err:
            flash(f'Database error: {err}', 'error')

        return redirect(url_for('irregular_students'))

    search_query = request.args.get('search', '').strip()

    try:
        students_query = supabase.table('irregular_students').select(
            'student_id, student_id_number, first_name, last_name, program, year_level, created_at'
        )
        if user_role == 'Viewer':
            students_query = students_query.eq('program', program)
        if search_query:
            students_query = students_query.or_(
                f"student_id_number.ilike.%{search_query}%,first_name.ilike.%{search_query}%,last_name.ilike.%{search_query}%"
            )
        students = students_query.execute().data or []

        # Compute distinct-course class count per student from irregular_student_schedule
        iss_rows = (supabase.table('irregular_student_schedule').select('student_id, course_id').execute().data) or []
        class_count = {}
        for r in iss_rows:
            sid = r.get('student_id')
            cid = r.get('course_id')
            if sid is None:
                continue
            class_count.setdefault(sid, set())
            if cid is not None:
                class_count[sid].add(cid)

        for s in students:
            s['class_count'] = len(class_count.get(s.get('student_id'), set()))

        students.sort(key=lambda s: (str(s.get('last_name') or ''), str(s.get('first_name') or '')))
    except Exception:
        students = []

    return render_template('irregular_students.html', active_page='irregular_students', students=students, user_role=user_role, search_query=search_query)


@app.route('/delete_irregular_student/<int:student_id>')
@login_required
def delete_irregular_student(student_id):
    handled, resp = _request_delete_if_scheduler('irregular_student', student_id, f'Irregular Student ID {student_id}')
    if handled:
        return resp or redirect(url_for('irregular_students'))

    try:
        supabase.table('irregular_students').delete().eq('student_id', student_id).execute()
        supabase.table('delete_requests').update({'status': 'approved'}).eq('item_type', 'irregular_student').eq('item_id', str(student_id)).eq('status', 'pending').execute()

        log_activity('delete', 'irregular_student', f'Irregular Student ID {student_id}')
        flash('Deleted successfully', 'success')
    except Exception as err:
        flash(f'Error: {err}', 'error')

    return redirect(url_for('irregular_students'))


@app.route('/irregular_students/<int:student_id>/schedule')
@scheduler_required
def manage_irregular_student_schedule(student_id):
    semester_filter = request.args.get('semester', '1st Semester')
    year_filter = request.args.get('year', '')

    student = _first((supabase.table('irregular_students').select('*').eq('student_id', student_id).execute().data) or [])

    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('irregular_students'))

    raw_yls = [y.strip() for y in (student.get('year_level') or '').split(',') if y.strip()]

    # Available courses for the student's program, filtered in Python by semester/year level
    program_courses = (supabase.table('course').select('*').eq('program', student['program']).execute().data) or []
    available_courses = []
    for c in program_courses:
        if semester_filter:
            c_sem = c.get('semester') or ''
            if not (c_sem == semester_filter or c_sem == ''):
                continue
        if raw_yls:
            yl = str(c.get('year_level') or '')
            matched = yl == ''
            for raw in raw_yls:
                short_yl = raw.replace('st Year', '').replace('nd Year', '').replace('rd Year', '').replace('th Year', '').strip()
                if yl == raw or yl == short_yl:
                    matched = True
                    break
            if not matched:
                continue
        available_courses.append(c)
    available_courses.sort(key=lambda c: (str(c.get('year_level') or ''), c.get('course_name') or ''))

    courses_by_year = {}
    for c in available_courses:
        yl = str(c.get('year_level') or 'Other Year').strip()
        if yl == '1': yl = '1st Year'
        elif yl == '2': yl = '2nd Year'
        elif yl == '3': yl = '3rd Year'
        elif yl == '4': yl = '4th Year'

        if yl not in courses_by_year:
            courses_by_year[yl] = []
        courses_by_year[yl].append(c)

    # All schedule entries for the student's program / semester
    sched_query = supabase.table('schedule').select(
        'schedule_id, course_id, section, day, class_start, class_end, room_id, prof_id, session_type, '
        'course(course_name), room(room_name), professor(first_name, last_name)'
    ).eq('program', student['program'])
    if semester_filter:
        sched_query = sched_query.eq('semester', semester_filter)
    sched_rows = sched_query.execute().data or []

    all_schedule_entries = []
    for row in sched_rows:
        c = _rel(row, 'course') or {}
        r = _rel(row, 'room') or {}
        p = _rel(row, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''
        all_schedule_entries.append({
            'schedule_id': row.get('schedule_id'),
            'course_id': row.get('course_id'),
            'section': row.get('section'),
            'day': row.get('day'),
            'class_start': row.get('class_start'),
            'class_end': row.get('class_end'),
            'room_id': row.get('room_id'),
            'room': r.get('room_name'),
            'prof_id': row.get('prof_id'),
            'professor': f"{pf} {pl}".strip() or None,
            'course_name': c.get('course_name'),
            'session_type': row.get('session_type'),
        })

    course_sections = {}
    for entry in all_schedule_entries:
        c_id = entry['course_id']
        sec = entry['section']
        if c_id not in course_sections:
            course_sections[c_id] = {}
        if sec not in course_sections[c_id]:
            course_sections[c_id][sec] = {
                'section': sec,
                'professor': entry['professor'] or 'TBA',
                'room': entry['room'] or 'TBA',
                'entries': []
            }
        course_sections[c_id][sec]['entries'].append(entry)

    # Assigned entries for this student
    assigned_rows = (supabase.table('irregular_student_schedule').select(
        'id, course_id, section, schedule(day, class_start, class_end, session_type, room(room_name), professor(first_name, last_name)), course(course_name)'
    ).eq('student_id', student_id).execute().data) or []

    assigned_entries = []
    for row in assigned_rows:
        sch = _rel(row, 'schedule') or {}
        crs = _rel(row, 'course') or {}
        r = _rel(sch, 'room') or {}
        p = _rel(sch, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''
        assigned_entries.append({
            'assigned_id': row.get('id'),
            'course_id': row.get('course_id'),
            'section': row.get('section'),
            'course_name': crs.get('course_name'),
            'day': sch.get('day'),
            'class_start': sch.get('class_start'),
            'class_end': sch.get('class_end'),
            'room': r.get('room_name'),
            'professor': f"{pf} {pl}".strip() or None,
            'session_type': sch.get('session_type'),
        })
    assigned_entries.sort(key=lambda a: (_DAY_ORDER.get(a.get('day') or '', 99), str(a.get('class_start') or '')))

    assigned_course_ids = list(set([a['course_id'] for a in assigned_entries]))

    return render_template(
        'manage_irregular_schedule.html',
        active_page='irregular_students',
        student=student,
        available_courses=available_courses,
        courses_by_year=courses_by_year,
        course_sections=course_sections,
        assigned_entries=assigned_entries,
        assigned_course_ids=assigned_course_ids,
        semester_filter=semester_filter,
        year_filter=year_filter
    )


@app.route('/irregular_students/<int:student_id>/view_schedule')
@scheduler_required
def view_irregular_student_schedule(student_id):
    student = _first((supabase.table('irregular_students').select('*').eq('student_id', student_id).execute().data) or [])

    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('irregular_students'))

    assigned_rows = (supabase.table('irregular_student_schedule').select(
        'id, course_id, section, schedule(day, class_start, class_end, session_type, room(room_name), professor(first_name, last_name)), course(course_name)'
    ).eq('student_id', student_id).execute().data) or []

    assigned_entries = []
    for row in assigned_rows:
        sch = _rel(row, 'schedule') or {}
        crs = _rel(row, 'course') or {}
        r = _rel(sch, 'room') or {}
        p = _rel(sch, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''
        assigned_entries.append({
            'assigned_id': row.get('id'),
            'course_id': row.get('course_id'),
            'section': row.get('section'),
            'course_name': crs.get('course_name'),
            'day': sch.get('day'),
            'class_start': sch.get('class_start'),
            'class_end': sch.get('class_end'),
            'room': r.get('room_name'),
            'professor': f"{pf} {pl}".strip() or None,
            'session_type': sch.get('session_type'),
        })

    for entry in assigned_entries:
        entry['start_time_fmt'] = _format_time(entry['class_start']) or str(entry['class_start'])
        entry['end_time_fmt'] = _format_time(entry['class_end']) or str(entry['class_end'])

    assigned_entries.sort(key=lambda a: (_DAY_ORDER.get(a.get('day') or '', 99), str(a.get('class_start') or '')))

    return render_template(
        'view_irregular_schedule.html',
        active_page='irregular_students',
        student=student,
        assigned_entries=assigned_entries
    )


@app.route('/irregular_students/<int:student_id>/assign_section', methods=['POST'])
@scheduler_required
def assign_irregular_section(student_id):
    data = request.get_json(silent=True) or request.form
    course_id = data.get('course_id')
    section = data.get('section')

    if not course_id or not section:
        return jsonify({'success': False, 'message': 'Course and section are required.'}), 400

    res = supabase.table('schedule').select('*, course(course_name)').eq('course_id', course_id).eq('section', section).execute()
    new_entries = []
    for row in (res.data or []):
        crs = _rel(row, 'course') or {}
        row['course_name'] = crs.get('course_name')
        new_entries.append(row)

    if not new_entries:
        return jsonify({'success': False, 'message': 'No schedule found for the selected course section.'}), 404

    has_conflict, conflict_msg = _check_schedule_conflict(student_id, new_entries)
    if has_conflict:
        return jsonify({'success': False, 'conflict': True, 'message': conflict_msg}), 409

    try:
        rows = []
        for entry in new_entries:
            rows.append({
                'student_id': student_id,
                'schedule_id': entry['schedule_id'],
                'course_id': int(course_id),
                'section': section,
            })
        supabase.table('irregular_student_schedule').upsert(rows, on_conflict='student_id,course_id').execute()

        log_activity('assign_irregular_schedule', 'irregular_student', f'Student #{student_id} assigned Course #{course_id} Section {section}')
        return jsonify({'success': True, 'message': 'Section assigned successfully.'})
    except Exception as err:
        return jsonify({'success': False, 'message': f'Database error: {err}'}), 500


@app.route('/irregular_students/<int:student_id>/unassign_section/<int:course_id>', methods=['POST'])
@scheduler_required
def unassign_irregular_section(student_id, course_id):
    try:
        supabase.table('irregular_student_schedule').delete().eq('student_id', student_id).eq('course_id', course_id).execute()

        log_activity('unassign_irregular_schedule', 'irregular_student', f'Student #{student_id} unassigned Course #{course_id}')
        return jsonify({'success': True, 'message': 'Section unassigned successfully.'})
    except Exception as err:
        return jsonify({'success': False, 'message': f'Database error: {err}'}), 500
#-------------------------------------------------------show_timeslots----------------------------------------------------------------------------------------------
@app.route('/timeslot')
@scheduler_required
def timeslot():
    all_timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    return render_template('timeslot.html', active_page='timeslot', timeslot=all_timeslots)

@app.route('/check_schedule_exists')
@login_required
def check_schedule_exists():
    year_level = (request.args.get('year_level') or '').strip()
    semester = (request.args.get('semester') or '').strip()
    major = (request.args.get('major') or '').strip()
    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    if not semester:
        return jsonify({'exists': False})

    try:
        query = supabase.table('schedule').select('section, program, major').eq('semester', semester)

        if user_role == 'Viewer':
            query = query.eq('program', program)
        elif program:
            query = query.or_(f'program.eq.{program},program.is.null')

        rows = query.execute().data or []
        matching = []
        for r in rows:
            sec = r.get('section') or ''
            if year_level and not sec.startswith(str(year_level)):
                continue
            rmaj = r.get('major')
            if major:
                if rmaj != major:
                    continue
            matching.append(sec)

        total_entries = len(matching)
        total_sections = len(set(matching))
        exists = total_entries > 0

        view_url = url_for('schedules', semester=semester)

        return jsonify({
            'exists': exists,
            'year_level': year_level or 'All',
            'semester': semester,
            'program': program,
            'major': major,
            'total_entries': total_entries,
            'total_sections': total_sections,
            'view_url': view_url
        })
    except Exception as err:
        return jsonify({'exists': False, 'error': str(err)})

#------------------------------------------------------------Generate Schedule------------------------------------------------------------------------------------------
@app.route('/generate_schedule', methods=['GET', 'POST'])
@login_required
def generate_schedule():
    semesters = ['1st Semester', '2nd Semester']

    if request.method == 'GET':
        preview_context = _build_preview_context()
        return render_template(
            'index.html',
            active_page='home',
            semesters=semesters,
            show_preview=bool(session.get('schedule_preview', [])),
            **preview_context
        )

    _clear_preview_generation_state()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    is_admin = (session.get('role') or '').strip().lower() == 'admin'
    is_viewer = (session.get('role') or '').strip().lower() == 'viewer'
    department = _get_department()

    try:
        semester = (request.form.get('semester') or '').strip()
        if not semester:
            return "Error: Semester is required to generate a schedule."

        # Normalize semester aliases
        if semester in ('1st Semester', '1st', '1'):
            sem_aliases = ['1st Semester', '1st', '1']
            standard_semester = '1st Semester'
        elif semester in ('2nd Semester', '2nd', '2'):
            sem_aliases = ['2nd Semester', '2nd', '2']
            standard_semester = '2nd Semester'
        else:
            sem_aliases = [semester]
            standard_semester = semester

        _ensure_course_semester_column()

        # Fetch curriculum for all four year levels for this semester
        query = supabase.table('course').select('*').in_('semester', sem_aliases)
        if is_viewer:
            query = query.eq('program', program)
        elif not is_admin and program:
            query = query.eq('program', program)

        all_courses = query.order('year_level').order('course_name').execute().data or []
        if not all_courses:
            all_c = (supabase.table('course').select('*').execute().data) or []
            all_courses = [c for c in all_c if str(c.get('semester') or '').strip().lower() in [s.lower() for s in sem_aliases]]

        if not all_courses:
            flash(f"No courses found for {standard_semester}.", 'warning')
            return redirect(url_for('generate_schedule'))

        # Group and deduplicate courses by year_level (1, 2, 3, 4)
        courses_by_year = {1: [], 2: [], 3: [], 4: []}
        for c in all_courses:
            try:
                yl = int(c.get('year_level') or 1)
            except (ValueError, TypeError):
                yl = 1
            courses_by_year.setdefault(yl, []).append(c)

        for yl in courses_by_year:
            seen_cnames = set()
            deduped = []
            for c in courses_by_year[yl]:
                cname = c.get('course_name')
                if cname not in seen_cnames:
                    seen_cnames.add(cname)
                    deduped.append(c)
            courses_by_year[yl] = deduped

        # Generate sections for all active year levels (4th year excluded for 2nd Semester)
        all_sections = []
        active_years = [1, 2, 3] if standard_semester == '2nd Semester' else [1, 2, 3, 4]

        for y in active_years:
            if not courses_by_year.get(y):
                continue

            raw_stu = (request.form.get(f'students[{y}]') or 
                       request.form.get(f'students_{y}') or 
                       request.form.get(f'number_of_students_{y}') or 
                       request.form.get('number_of_students'))
            raw_sec = (request.form.get(f'sections[{y}]') or 
                       request.form.get(f'sections_{y}') or 
                       request.form.get(f'number_of_sections_{y}') or 
                       request.form.get('number_of_sections'))

            if raw_stu:
                try:
                    stu_count = int(raw_stu)
                except (ValueError, TypeError):
                    stu_count = random.randint(300, 400)
            else:
                stu_count = random.randint(300, 400)

            if raw_sec:
                try:
                    sec_count = int(raw_sec)
                except (ValueError, TypeError):
                    sec_count = math.ceil(stu_count / 30)
            else:
                sec_count = math.ceil(stu_count / 30)

            sec_count = max(1, sec_count)
            distinct_majors = []
            for c in courses_by_year.get(y, []):
                m = c.get('major')
                if m and str(m).strip().lower() not in ('general', 'none', 'null', ''):
                    m_clean = str(m).strip()
                    if m_clean.lower() in ('web', 'web dev', 'web development'):
                        m_clean = 'Web Development'
                    elif m_clean.lower() in ('database', 'database systems', 'database system'):
                        m_clean = 'Database Systems'
                    elif m_clean.lower() == 'networking':
                        m_clean = 'Networking'
                    if m_clean not in distinct_majors:
                        distinct_majors.append(m_clean)

            if standard_semester == '2nd Semester' and y == 3:
                raw_db = (request.form.get('sections_major[3][database]') or
                          request.form.get('sections[3][database]') or
                          request.form.get('sections_database') or
                          request.form.get('database_sections'))
                raw_web = (request.form.get('sections_major[3][web]') or
                           request.form.get('sections[3][web]') or
                           request.form.get('sections_web') or
                           request.form.get('web_sections'))
                raw_net = (request.form.get('sections_major[3][networking]') or
                           request.form.get('sections[3][networking]') or
                           request.form.get('sections_networking') or
                           request.form.get('networking_sections'))

                if raw_db or raw_web or raw_net:
                    try:
                        db_cnt = max(1, int(raw_db)) if raw_db else 5
                    except (ValueError, TypeError):
                        db_cnt = 5
                    try:
                        web_cnt = max(1, int(raw_web)) if raw_web else 4
                    except (ValueError, TypeError):
                        web_cnt = 4
                    try:
                        net_cnt = max(1, int(raw_net)) if raw_net else 4
                    except (ValueError, TypeError):
                        net_cnt = 4
                    sections_by_major = {
                        'Database Systems': db_cnt,
                        'Web Development': web_cnt,
                        'Networking': net_cnt,
                    }
                else:
                    base_sec = sec_count // 3
                    rem_sec = sec_count % 3
                    sections_by_major = {
                        'Database Systems': max(1, base_sec + (1 if rem_sec > 0 else 0)),
                        'Web Development': max(1, base_sec + (1 if rem_sec > 1 else 0)),
                        'Networking': max(1, base_sec),
                    }
                yr_sections = _generate_sections(y, stu_count, sections_by_major=sections_by_major)
            else:
                yr_sections = _generate_sections(y, stu_count, sec_count, majors=distinct_majors)

            for s in yr_sections:
                s['semester'] = standard_semester
                all_sections.append(s)

        if not all_sections:
            all_sections = [{'section': '1A', 'section_name': '1A', 'year_level': '1', 'student_count': 40, 'semester': standard_semester, 'major': None}]

        session['generated_sections'] = all_sections

        # Fetch prof_course mappings
        if is_viewer and department:
            pc_res = supabase.table('prof_course').select('course_id, prof_id, professor(first_name, last_name, max_hours)').eq('professor.department', department).execute()
        else:
            pc_res = supabase.table('prof_course').select('course_id, prof_id, professor(first_name, last_name, max_hours)').execute()
        pc_data = pc_res.data or []

        professors_by_course = {}
        for row in pc_data:
            p = _rel(row, 'professor')
            if p:
                professors_by_course.setdefault(row['course_id'], []).append({
                    'course_id': row.get('course_id'),
                    'prof_id': row.get('prof_id'),
                    'first_name': p.get('first_name'),
                    'last_name': p.get('last_name'),
                    'max_hours': p.get('max_hours'),
                })

        # Fetch rooms
        if is_viewer and department:
            rooms_res = supabase.table('room').select('*').eq('department', department).execute()
        else:
            rooms_res = supabase.table('room').select('*').execute()
        rooms = rooms_res.data or []
        lecture_rooms = [r for r in rooms if 'lecture' in (r.get('room_type') or '').strip().lower()]
        lab_rooms = [r for r in rooms if 'laboratory' in (r.get('room_type') or '').strip().lower() or 'lab' in (r.get('room_type') or '').strip().lower()]
        if not lecture_rooms:
            lecture_rooms = rooms
        if not lab_rooms:
            lab_rooms = rooms

        # Timeslots & candidate slots
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
        timeslots.sort(key=lambda t: str(t.get('start_time') or ''))
        candidate_slots = _build_candidate_slots(timeslots)

        day_order = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
        slot_groups = {}
        for slot in candidate_slots:
            slot_groups.setdefault(slot['day'], []).append(slot)

        # Global conflict tracking across all 4 years
        section_bookings = {(sec['section'], sec.get('major')): [] for sec in all_sections}
        room_bookings = {}
        professor_bookings = {}
        professor_hours = {}
        preview_entries = []

        # Load existing bookings from DB to avoid collision across different semesters/programs
        existing_rows = (supabase.table('schedule').select('section, room_id, day, class_start, class_end, prof_id, semester, program, major').execute().data) or []
        for existing in existing_rows:
            # If the existing row is for the same semester and program that we are actively regenerating,
            # it will be overwritten on confirmation, so don't let it block generating new slots
            if existing.get('semester') == standard_semester and (not program or existing.get('program') == program):
                continue
            sec_n = existing.get('section')
            sec_m = existing.get('major')
            rid = existing.get('room_id')
            p_id = existing.get('prof_id')
            d = existing.get('day')
            st = existing.get('class_start')
            et = existing.get('class_end')
            if sec_n:
                section_bookings.setdefault((sec_n, sec_m), []).append((d, st, et))
            if rid is not None:
                room_bookings.setdefault(rid, []).append((d, st, et))
            if p_id is not None:
                professor_bookings.setdefault(p_id, []).append((d, st, et))

        # Assignment loop across all sections in the batch
        for section in all_sections:
            section_name = section['section']
            yr = int(section.get('year_level') or 1)
            sec_major = section.get('major')
            sec_key = (section_name, sec_major)
            section_courses = [
                c for c in courses_by_year.get(yr, [])
                if _major_matches(c.get('major'), sec_major)
            ]
            section_bookings.setdefault(sec_key, [])
            courses_per_day = {}
            two_course_day_used = False
            late_days = set()
            days_tried = {}

            for course in section_courses:
                course_id = course['course_id']
                professors_for_course = professors_by_course.get(course_id, [])
                subject_session_queue = _build_subject_session_queue(course)

                for session_item in subject_session_queue:
                    assigned = False

                    if session_item.get('paired'):
                        lec_dur = session_item['lec_duration']
                        lab_dur = session_item['lab_duration']
                        total_dur = lec_dur + lab_dur

                        if not lecture_rooms or not lab_rooms:
                            continue

                        sorted_profs = sorted(professors_for_course, key=lambda p: professor_hours.get(p['prof_id'], 0))

                        for professor in sorted_profs:
                            prof_key = professor['prof_id']
                            max_h = professor.get('max_hours') or 40
                            if professor_hours.get(prof_key, 0) + total_dur > max_h:
                                continue

                            all_days = sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99))
                            scored_days = []
                            for day in all_days:
                                day_slots = slot_groups[day]
                                if len(day_slots) < total_dur:
                                    continue
                                test_slot = day_slots[0] if day_slots else None
                                slot_is_late = _is_late_slot(test_slot) if test_slot else False
                                s = _score_day_for_section(day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used)
                                if s >= 0:
                                    scored_days.append((s, day))
                            scored_days.sort(key=lambda x: x[0], reverse=True)

                            for _, day in scored_days:
                                day_slots = slot_groups[day]
                                if len(day_slots) < total_dur:
                                    continue

                                late_threshold = timedelta(hours=17)
                                for start_index in range(0, len(day_slots) - total_dur + 1):
                                    full_block = day_slots[start_index:start_index + total_dur]
                                    if not _is_contiguous_block(full_block):
                                        continue

                                    slot_is_late = _is_late_slot(full_block[0], late_threshold)
                                    if slot_is_late and len(late_days) >= _get_year_rules(yr)['max_late_days'] and day not in late_days:
                                        continue

                                    lec_start = full_block[0]['start_time']
                                    lec_end = full_block[lec_dur - 1]['end_time'] if lec_dur > 0 else lec_start
                                    lab_start = full_block[lec_dur]['start_time'] if lab_dur > 0 else lec_end
                                    lab_end = full_block[-1]['end_time']

                                    if _has_conflict(day, lec_start, lab_end, section_bookings[sec_key]):
                                        continue
                                    if _has_conflict(day, lec_start, lab_end, professor_bookings.get(prof_key, [])):
                                        continue

                                    lec_room = None
                                    for lr in lecture_rooms:
                                        if not _has_conflict(day, lec_start, lec_end, room_bookings.get(lr['room_id'], [])):
                                            lec_room = lr
                                            break
                                    if lec_room is None:
                                        continue

                                    lab_room = None
                                    for br in lab_rooms:
                                        if not _has_conflict(day, lab_start, lab_end, room_bookings.get(br['room_id'], [])):
                                            lab_room = br
                                            break
                                    if lab_room is None:
                                        continue

                                    preview_entries.append({
                                        'course_id': course_id,
                                        'course_name': course.get('course_name'),
                                        'prof_id': prof_key,
                                        'professor_name': f"{professor.get('first_name','')} {professor.get('last_name','')}".strip(),
                                        'section': section_name,
                                        'room_id': lec_room['room_id'],
                                        'room_name': lec_room.get('room_name'),
                                        'day': day,
                                        'start': lec_start,
                                        'end': lec_end,
                                        'session_type': 'Lecture',
                                        'semester': standard_semester,
                                        'major': sec_major or course.get('major'),
                                        'program': course.get('program') or program or session.get('program', ''),
                                    })
                                    section_bookings[sec_key].append((day, lec_start, lec_end))
                                    room_bookings.setdefault(lec_room['room_id'], []).append((day, lec_start, lec_end))
                                    professor_bookings.setdefault(prof_key, []).append((day, lec_start, lec_end))

                                    preview_entries.append({
                                        'course_id': course_id,
                                        'course_name': course.get('course_name'),
                                        'prof_id': prof_key,
                                        'professor_name': f"{professor.get('first_name','')} {professor.get('last_name','')}".strip(),
                                        'section': section_name,
                                        'room_id': lab_room['room_id'],
                                        'room_name': lab_room.get('room_name'),
                                        'day': day,
                                        'start': lab_start,
                                        'end': lab_end,
                                        'session_type': 'Laboratory',
                                        'semester': standard_semester,
                                        'major': sec_major or course.get('major'),
                                        'program': course.get('program') or program or session.get('program', ''),
                                    })
                                    section_bookings[sec_key].append((day, lab_start, lab_end))
                                    room_bookings.setdefault(lab_room['room_id'], []).append((day, lab_start, lab_end))
                                    professor_bookings.setdefault(prof_key, []).append((day, lab_start, lab_end))

                                    professor_hours[prof_key] = professor_hours.get(prof_key, 0) + total_dur
                                    if courses_per_day.get(day, 0) == 1:
                                        two_course_day_used = True
                                    courses_per_day[day] = 2
                                    days_tried[day] = days_tried.get(day, 0) + 1
                                    if slot_is_late:
                                        late_days.add(day)
                                    assigned = True
                                    break
                                if assigned:
                                    break
                            if assigned:
                                break

                        if not assigned:
                            for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
                                day_slots = slot_groups[day]
                                if len(day_slots) < total_dur:
                                    continue
                                for start_index in range(0, len(day_slots) - total_dur + 1):
                                    full_block = day_slots[start_index:start_index + total_dur]
                                    if not _is_contiguous_block(full_block):
                                        continue
                                    lec_start = full_block[0]['start_time']
                                    lec_end = full_block[lec_dur - 1]['end_time'] if lec_dur > 0 else lec_start
                                    lab_start = full_block[lec_dur]['start_time'] if lab_dur > 0 else lec_end
                                    lab_end = full_block[-1]['end_time']
                                    if _has_conflict(day, lec_start, lab_end, section_bookings[sec_key]):
                                        continue
                                    if _score_day_for_section(day, yr, courses_per_day, late_days, _is_late_slot(full_block[0]), days_tried, two_course_day_used) < 0:
                                        continue

                                    prof_key = None
                                    for p in sorted(professors_for_course, key=lambda x: professor_hours.get(x['prof_id'], 0)):
                                        pk = p['prof_id']
                                        mh = p.get('max_hours') or 40
                                        if professor_hours.get(pk, 0) + total_dur <= mh:
                                            if not _has_conflict(day, lec_start, lab_end, professor_bookings.get(pk, [])):
                                                prof_key = pk
                                                break

                                    lec_room = None
                                    for lr in lecture_rooms:
                                        if not _has_conflict(day, lec_start, lec_end, room_bookings.get(lr['room_id'], [])):
                                            lec_room = lr
                                            break
                                    lab_room = None
                                    for br in lab_rooms:
                                        if not _has_conflict(day, lab_start, lab_end, room_bookings.get(br['room_id'], [])):
                                            lab_room = br
                                            break

                                    lec_room_id = lec_room['room_id'] if lec_room else None
                                    preview_entries.append({
                                        'course_id': course_id,
                                        'course_name': course.get('course_name'),
                                        'prof_id': prof_key,
                                        'professor_name': f"{p.get('first_name','')} {p.get('last_name','')}".strip() if prof_key else None,
                                        'section': section_name,
                                        'room_id': lec_room_id,
                                        'room_name': lec_room.get('room_name') if lec_room else None,
                                        'day': day,
                                        'start': lec_start,
                                        'end': lec_end,
                                        'session_type': 'Lecture',
                                        'semester': standard_semester,
                                        'major': sec_major or course.get('major'),
                                        'program': course.get('program') or program or session.get('program', ''),
                                    })
                                    section_bookings[sec_key].append((day, lec_start, lec_end))
                                    if lec_room:
                                        room_bookings.setdefault(lec_room_id, []).append((day, lec_start, lec_end))
                                    if prof_key:
                                        professor_bookings.setdefault(prof_key, []).append((day, lec_start, lec_end))
                                        professor_hours[prof_key] = professor_hours.get(prof_key, 0) + total_dur

                                    lab_room_id = lab_room['room_id'] if lab_room else None
                                    preview_entries.append({
                                        'course_id': course_id,
                                        'course_name': course.get('course_name'),
                                        'prof_id': prof_key,
                                        'professor_name': f"{p.get('first_name','')} {p.get('last_name','')}".strip() if prof_key else None,
                                        'section': section_name,
                                        'room_id': lab_room_id,
                                        'room_name': lab_room.get('room_name') if lab_room else None,
                                        'day': day,
                                        'start': lab_start,
                                        'end': lab_end,
                                        'session_type': 'Laboratory',
                                        'semester': standard_semester,
                                        'major': sec_major or course.get('major'),
                                        'program': course.get('program') or program or session.get('program', ''),
                                    })
                                    section_bookings[sec_key].append((day, lab_start, lab_end))
                                    if lab_room:
                                        room_bookings.setdefault(lab_room_id, []).append((day, lab_start, lab_end))
                                    if prof_key:
                                        professor_bookings.setdefault(prof_key, []).append((day, lab_start, lab_end))
                                    if courses_per_day.get(day, 0) == 1:
                                        two_course_day_used = True
                                    courses_per_day[day] = 2
                                    days_tried[day] = days_tried.get(day, 0) + 1
                                    if _is_late_slot(full_block[0]):
                                        late_days.add(day)
                                    assigned = True
                                    break
                                if assigned:
                                    break

                    else:
                        # Unpaired session (Lecture or Lab)
                        session_type = session_item['session_type']
                        duration = session_item['duration']
                        if duration <= 0:
                            continue

                        candidate_rooms = lecture_rooms if session_type == 'Lecture' else lab_rooms
                        if not candidate_rooms:
                            continue

                        sorted_profs = sorted(professors_for_course, key=lambda p: professor_hours.get(p['prof_id'], 0))

                        for professor in sorted_profs:
                            prof_key = professor['prof_id']
                            max_h = professor.get('max_hours') or 40
                            if professor_hours.get(prof_key, 0) + duration > max_h:
                                continue

                            all_days = sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99))
                            scored_days = []
                            for day in all_days:
                                day_slots = slot_groups[day]
                                if len(day_slots) < duration:
                                    continue
                                test_slot = day_slots[0] if day_slots else None
                                slot_is_late = _is_late_slot(test_slot) if test_slot else False
                                s = _score_day_for_section(day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used)
                                if s >= 0:
                                    scored_days.append((s, day))
                            scored_days.sort(key=lambda x: x[0], reverse=True)

                            for _, day in scored_days:
                                day_slots = slot_groups[day]
                                if len(day_slots) < duration:
                                    continue

                                late_threshold = timedelta(hours=17)
                                for start_index in range(0, len(day_slots) - duration + 1):
                                    block_slots = day_slots[start_index:start_index + duration]
                                    if not _is_contiguous_block(block_slots):
                                        continue

                                    slot_is_late = _is_late_slot(block_slots[0], late_threshold)
                                    if slot_is_late and len(late_days) >= _get_year_rules(yr)['max_late_days'] and day not in late_days:
                                        continue

                                    block_start = block_slots[0]['start_time']
                                    block_end = block_slots[-1]['end_time']

                                    if _has_conflict(day, block_start, block_end, section_bookings[sec_key]):
                                        continue
                                    if _has_conflict(day, block_start, block_end, professor_bookings.get(prof_key, [])):
                                        continue

                                    for room in candidate_rooms:
                                        room_key = room['room_id']
                                        if _has_conflict(day, block_start, block_end, room_bookings.get(room_key, [])):
                                            continue

                                        preview_entries.append({
                                            'course_id': course_id,
                                            'course_name': course.get('course_name'),
                                            'prof_id': prof_key,
                                            'professor_name': f"{professor.get('first_name','')} {professor.get('last_name','')}".strip(),
                                            'section': section_name,
                                            'room_id': room['room_id'],
                                            'room_name': room.get('room_name'),
                                            'day': day,
                                            'start': block_start,
                                            'end': block_end,
                                            'session_type': session_type,
                                            'semester': standard_semester,
                                            'major': sec_major or course.get('major'),
                                            'program': course.get('program') or program or session.get('program', ''),
                                        })

                                        section_bookings[sec_key].append((day, block_start, block_end))
                                        room_bookings.setdefault(room_key, []).append((day, block_start, block_end))
                                        professor_bookings.setdefault(prof_key, []).append((day, block_start, block_end))
                                        professor_hours[prof_key] = professor_hours.get(prof_key, 0) + duration
                                        if courses_per_day.get(day, 0) == 1:
                                            two_course_day_used = True
                                        courses_per_day[day] = 2
                                        days_tried[day] = days_tried.get(day, 0) + 1
                                        if slot_is_late:
                                            late_days.add(day)
                                        assigned = True
                                        break

                                    if assigned:
                                        break
                                if assigned:
                                    break
                            if assigned:
                                break

                        if not assigned:
                            for day in sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99)):
                                day_slots = slot_groups[day]
                                if len(day_slots) < duration:
                                    continue
                                for start_index in range(0, len(day_slots) - duration + 1):
                                    block_slots = day_slots[start_index:start_index + duration]
                                    if not _is_contiguous_block(block_slots):
                                        continue
                                    block_start = block_slots[0]['start_time']
                                    block_end = block_slots[-1]['end_time']
                                    if _has_conflict(day, block_start, block_end, section_bookings[sec_key]):
                                        continue
                                    if _score_day_for_section(day, yr, courses_per_day, late_days, _is_late_slot(block_slots[0]), days_tried, two_course_day_used) < 0:
                                        continue

                                    prof_key = None
                                    for p in sorted(professors_for_course, key=lambda x: professor_hours.get(x['prof_id'], 0)):
                                        pk = p['prof_id']
                                        mh = p.get('max_hours') or 40
                                        if professor_hours.get(pk, 0) + duration <= mh:
                                            if not _has_conflict(day, block_start, block_end, professor_bookings.get(pk, [])):
                                                prof_key = pk
                                                break

                                    found_room = None
                                    for room in candidate_rooms:
                                        if not _has_conflict(day, block_start, block_end, room_bookings.get(room['room_id'], [])):
                                            found_room = room
                                            break
                                    room_id = found_room['room_id'] if found_room else None
                                    preview_entries.append({
                                        'course_id': course_id,
                                        'course_name': course.get('course_name'),
                                        'prof_id': prof_key,
                                        'professor_name': f"{p.get('first_name','')} {p.get('last_name','')}".strip() if prof_key else None,
                                        'section': section_name,
                                        'room_id': room_id,
                                        'room_name': found_room.get('room_name') if found_room else None,
                                        'day': day,
                                        'start': block_start,
                                        'end': block_end,
                                        'session_type': session_type,
                                        'semester': standard_semester,
                                        'major': sec_major or course.get('major'),
                                        'program': course.get('program') or program or session.get('program', ''),
                                    })
                                    section_bookings[sec_key].append((day, block_start, block_end))
                                    if found_room:
                                        room_bookings.setdefault(room_id, []).append((day, block_start, block_end))
                                    if prof_key:
                                        professor_bookings.setdefault(prof_key, []).append((day, block_start, block_end))
                                        professor_hours[prof_key] = professor_hours.get(prof_key, 0) + duration
                                    if courses_per_day.get(day, 0) == 1:
                                        two_course_day_used = True
                                    courses_per_day[day] = 2
                                    days_tried[day] = days_tried.get(day, 0) + 1
                                    if _is_late_slot(block_slots[0]):
                                        late_days.add(day)
                                    assigned = True
                                    break
                                if assigned:
                                    break

        for idx, entry in enumerate(preview_entries, start=1):
            entry['id'] = idx
            if not isinstance(entry.get('start'), str):
                entry['start'] = _format_time(entry['start'])
            if not isinstance(entry.get('end'), str):
                entry['end'] = _format_time(entry['end'])
            entry['time_range'] = f"{entry['day']} | {entry['start']} - {entry['end']}"

        _set_preview_for_user(preview_entries)
        flash('Schedule generated successfully across all 4 year levels.', 'success')

        preview_context = _build_preview_context(preview_entries)
        return render_template(
            'index.html',
            active_page='home',
            semesters=semesters,
            show_preview=bool(preview_entries),
            **preview_context
        )
    except Exception as err:
        _clear_preview_generation_state()
        return f"Error: {err}", 500


@app.route('/preview_schedule')
@login_required
def preview_schedule():
    preview = _get_preview_for_user()
    preview_context = _build_preview_context(preview)
    return render_template(
        'preview_schedule.html',
        active_page='home',
        show_preview=bool(preview),
        **preview_context
    )


@app.route('/edit_preview_entry', methods=['POST'])
@login_required
def edit_preview_entry():
    data = request.form or request.get_json(silent=True) or {}
    entry_id = int(data.get('id') or 0)
    preview = _get_preview_for_user()
    if not preview:
        return jsonify({'error': 'No preview available.'}), 400

    try:
        course_id = data.get('course_id')
        section = (data.get('section') or '').strip()
        prof_id = data.get('prof_id')
        room_id = data.get('room_id')
        day = data.get('day')
        start = data.get('start')
        end = data.get('end')

        if not course_id:
            return jsonify({'error': 'Course must be selected.'}), 400

        try:
            course_id = int(course_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid course selection.'}), 400

        course_res = supabase.table('course').select('course_name').eq('course_id', course_id).execute()
        course_row = _first(course_res.data or [])
        if not course_row:
            return jsonify({'error': 'Selected course is invalid.'}), 400

        if not section:
            return jsonify({'error': 'Section is required.'}), 400

        if day not in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
            return jsonify({'error': 'Invalid day selected.'}), 400

        start_time = _parse_time(start)
        end_time = _parse_time(end)
        if start_time is None or end_time is None or end_time <= start_time:
            return jsonify({'error': 'Invalid start or end time.'}), 400

        prof_name = None
        prof_id_int = None
        if prof_id:
            try:
                prof_id_int = int(prof_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid professor selection.'}), 400

            dept = _get_department()
            pc_res = supabase.table('prof_course').select('prof_id, professor(first_name, last_name)').eq('course_id', course_id).eq('prof_id', prof_id_int).eq('professor.department', dept).execute()
            row = _first(pc_res.data or [])
            if not row:
                return jsonify({'error': 'Selected professor is not assigned to this course.'}), 400
            p = _rel(row, 'professor') or {}
            prof_name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()

        room_name = None
        room_id_int = None
        if room_id:
            try:
                room_id_int = int(room_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid room selection.'}), 400

            room_res = supabase.table('room').select('room_name').eq('room_id', room_id_int).execute()
            row = _first(room_res.data or [])
            if not row:
                return jsonify({'error': 'Selected room is invalid.'}), 400
            room_name = row.get('room_name')

        target_entry = None
        for entry in preview:
            if entry.get('id') == entry_id:
                target_entry = entry
                break

        if not target_entry:
            return jsonify({'error': 'Preview entry not found.'}), 404

        section_bookings = []
        room_bookings = []
        professor_bookings = []
        for entry in preview:
            if entry.get('id') == entry_id:
                continue
            existing_day = entry.get('day')
            existing_start = _parse_time(entry.get('start'))
            existing_end = _parse_time(entry.get('end'))
            if existing_day is None or existing_start is None or existing_end is None:
                continue
            if entry.get('section'):
                section_bookings.append((existing_day, existing_start, existing_end, entry.get('section')))
            if entry.get('room_id') is not None:
                room_bookings.append((existing_day, existing_start, existing_end, entry.get('room_id')))
            if entry.get('prof_id') is not None:
                professor_bookings.append((existing_day, existing_start, existing_end, entry.get('prof_id')))

        for existing_day, existing_start, existing_end, existing_section in section_bookings:
            if existing_section == section and existing_day == day and _has_conflict(day, start_time, end_time, [(existing_day, existing_start, existing_end)]):
                return jsonify({'error': 'This section already has a time conflict in the preview.'}), 400

        if room_id_int is not None:
            for existing_day, existing_start, existing_end, existing_room_id in room_bookings:
                if existing_room_id == room_id_int and existing_day == day and _has_conflict(day, start_time, end_time, [(existing_day, existing_start, existing_end)]):
                    return jsonify({'error': 'The selected room has a conflicting booking in the preview.'}), 400

        if prof_id_int is not None:
            for existing_day, existing_start, existing_end, existing_prof_id in professor_bookings:
                if existing_prof_id == prof_id_int and existing_day == day and _has_conflict(day, start_time, end_time, [(existing_day, existing_start, existing_end)]):
                    return jsonify({'error': 'The selected professor has a conflicting booking in the preview.'}), 400

        target_entry['course_id'] = course_id
        target_entry['course_name'] = course_row.get('course_name')
        target_entry['section'] = section
        target_entry['prof_id'] = prof_id_int
        target_entry['professor_name'] = prof_name
        target_entry['room_id'] = room_id_int
        target_entry['room_name'] = room_name
        target_entry['day'] = day
        target_entry['start'] = start
        target_entry['end'] = end
        target_entry['time_range'] = f"{day} | {start} - {end}"

        _set_preview_for_user(preview)
        return jsonify({'success': True})
    except Exception as err:
        return jsonify({'error': str(err)}), 500


@app.route('/confirm_preview', methods=['POST'])
@login_required
def confirm_preview():
    preview = _get_preview_for_user()
    if not preview:
        flash('No preview schedule found to confirm.', 'warning')
        return redirect(url_for('generate_schedule'))

    try:
        # Determine the semester, program, and sections from preview
        sem_val = preview[0].get('semester', '')
        prog_val = preview[0].get('program') or session.get('program', '')
        sections_in_preview = list({e.get('section') for e in preview if e.get('section')})

        # Overwrite previous schedule entries for these sections/semester/program to avoid stale duplicates
        if sections_in_preview and sem_val:
            del_query = supabase.table('schedule').delete().eq('semester', sem_val).in_('section', sections_in_preview)
            if prog_val:
                del_query = del_query.or_(f'program.eq.{prog_val},program.is.null,program.eq.')
            del_query.execute()

        rows = []
        for entry in preview:
            entry_prog = entry.get('program') or prog_val or session.get('program', '')
            prof_id = entry.get('prof_id')
            room_id = entry.get('room_id')
            rows.append({
                'course_id': int(entry['course_id']) if entry.get('course_id') else None,
                'prof_id': int(prof_id) if prof_id not in (None, '', 0, '0') else None,
                'room_id': int(room_id) if room_id not in (None, '', 0, '0') else None,
                'day': entry.get('day'),
                'class_start': _to_time_string(entry.get('start')),
                'class_end': _to_time_string(entry.get('end')),
                'session_type': entry.get('session_type'),
                'section': entry.get('section'),
                'semester': entry.get('semester') or sem_val,
                'major': entry.get('major'),
                'program': entry_prog,
            })

        # Batch insert into schedule table (in chunks to avoid payload limits)
        chunk_size = 50
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            supabase.table('schedule').insert(chunk).execute()

        # Update session generated_sections so state is consistent
        saved_sections = []
        seen_sec = set()
        for entry in preview:
            sec = entry.get('section')
            sec_key = (sec, entry.get('major'), entry.get('semester'))
            if sec and sec_key not in seen_sec:
                seen_sec.add(sec_key)
                saved_sections.append({
                    'section': sec,
                    'section_name': sec,
                    'semester': entry.get('semester') or sem_val,
                    'major': entry.get('major'),
                    'year_level': _year_of_section(sec),
                })
        session['generated_sections'] = saved_sections

        log_activity('confirm', 'schedule', f'Generated and saved {len(rows)} entries for {len(sections_in_preview)} sections ({sem_val})')
        flash(f'Schedule saved successfully! ({len(rows)} class entries confirmed)', 'success')
        _clear_preview_for_user()

        if sem_val:
            return redirect(url_for('schedules', semester=sem_val))
        return redirect(url_for('schedules'))
    except Exception as err:
        logging.exception(f"Error saving schedule in confirm_preview: {err}")
        flash(f'Error saving schedule: {err}', 'error')
        return redirect(url_for('preview_schedule'))


@app.route('/discard_preview', methods=['POST'])
@login_required
def discard_preview():
    _clear_preview_for_user()
    return redirect(url_for('home'))


# ──────────────────────────────────────────────────────────────────────────────
# BACKUP & RESTORE (JSON export/import via the Supabase SDK)
# ──────────────────────────────────────────────────────────────────────────────

BACKUP_TABLES = [
    'users', 'course', 'professor', 'room', 'timeslot',
    'prof_course', 'schedule', 'irregular_students',
    'irregular_student_schedule', 'scheduler_notifications',
    'delete_requests', 'activity_log',
]

BACKUP_PKS = {
    'users': 'id',
    'course': 'course_id',
    'professor': 'prof_id',
    'room': 'room_id',
    'timeslot': 'timeslot_id',
    'prof_course': 'prof_course_id',
    'schedule': 'schedule_id',
    'irregular_students': 'student_id',
    'irregular_student_schedule': 'id',
    'scheduler_notifications': 'id',
    'delete_requests': 'id',
    'activity_log': 'id',
}

@app.route('/backup', methods=['GET'])
@login_required
@role_required(['admin', 'scheduler'])
def backup_database():
    """Export every table to a JSON file and stream it as a download."""
    try:
        dump = {}
        for table in BACKUP_TABLES:
            res = supabase.table(table).select('*').execute()
            dump[table] = res.data or []

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'backup_{timestamp}.json'

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='w', encoding='utf-8')
        try:
            json.dump(dump, tmp, default=str, indent=2)
        finally:
            tmp.close()

        log_activity('backup', 'database', filename)
        return send_file(
            tmp.name,
            as_attachment=True,
            download_name=filename,
            mimetype='application/json',
        )
    except Exception as exc:
        logging.exception('Unexpected backup error: %s', exc)
        flash(f'An unexpected error occurred: {exc}', 'error')
        return redirect(request.referrer or url_for('home'))


@app.route('/restore', methods=['POST'])
@login_required
@role_required(['admin', 'scheduler'])
def restore_database():
    """Import a JSON backup produced by the backup endpoint."""
    if 'sql_file' not in request.files:
        flash('No file uploaded.', 'error')
        return redirect(request.referrer or url_for('home'))

    sql_file = request.files['sql_file']

    if not sql_file or sql_file.filename == '':
        flash('No file selected.', 'error')
        return redirect(request.referrer or url_for('home'))

    original_filename = secure_filename(sql_file.filename)
    if not original_filename.lower().endswith('.json'):
        flash('Invalid file type. Only .json backup files are accepted.', 'error')
        return redirect(request.referrer or url_for('home'))

    try:
        data = json.load(sql_file)

        for table in BACKUP_TABLES:
            rows = data.get(table)
            if not rows:
                continue
            pk = BACKUP_PKS.get(table)
            supabase.table(table).upsert(rows, on_conflict=pk).execute()

        performer = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip() or session.get('username', 'unknown')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        logging.info('Database restore performed by %s at %s from file %s', performer, timestamp, original_filename)
        log_activity('restore', 'database', original_filename)

        flash('Database restored successfully.', 'success')
        return redirect(request.referrer or url_for('home'))
    except Exception as exc:
        logging.exception('Unexpected restore error: %s', exc)
        flash(f'An unexpected error occurred: {exc}', 'error')
        return redirect(request.referrer or url_for('home'))


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1').lower() in ('1', 'true', 'yes')
    app.run(host=host, port=port, debug=debug)
