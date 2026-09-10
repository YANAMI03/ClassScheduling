from flask import Flask, request, render_template, redirect, url_for, session, jsonify, flash, send_file, abort
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

# All database reads and writes go through the authenticated Supabase client
# using the public/anon key with the user session JWT. Row Level Security (RLS)
# is strictly enforced by PostgreSQL policies. No service role or secret key is used.

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
    """Convert a database row or user object into the dict shape the templates expect."""
    if isinstance(user, dict):
        email = user.get('email') or ''
        username = user.get('username') or (email.split('@')[0] if '@' in email else email)
        return {
            'id': user.get('id'),
            'email': email,
            'username': username,
            'first_name': user.get('first_name', '') or '',
            'last_name': user.get('last_name', '') or '',
            'program': user.get('program', '') or '',
            'role': user.get('role', 'Viewer') or 'Viewer',
            'profile_picture': user.get('profile_picture'),
        }
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
    """Return all users from public.users as template-ready dicts.

    Queries the public.users database table via the authenticated Supabase client.
    Row Level Security (RLS) policies enforce that only authenticated users
    with the 'admin' role can read all user records.
    """
    res = supabase.table('users').select('*').execute()
    raw_users = res.data or []
    if isinstance(raw_users, list):
        return [_user_to_dict(u) for u in raw_users]
    return []


def _find_email_by_username_or_email(identifier):
    """Resolve a username or email input to the registered email by checking both columns simultaneously."""
    if not identifier:
        return None
    identifier = identifier.strip()

    # 1. Try secure RPC lookup checking both email and username simultaneously
    try:
        if hasattr(supabase, 'rpc'):
            rpc_res = supabase.rpc('get_email_by_username', {'p_username': identifier}).execute()
            if rpc_res and rpc_res.data:
                return str(rpc_res.data).strip()
    except Exception as err:
        logging.debug(f"RPC get_email_by_username fallback: {err}")

    # 2. Try table select checking both email and username columns simultaneously
    try:
        res = supabase.table('users').select('email, username').or_(f"email.ilike.{identifier},username.ilike.{identifier}").limit(1).execute()
        if res.data and len(res.data) > 0:
            user_row = res.data[0]
            if user_row.get('email'):
                return user_row['email']
            elif user_row.get('username'):
                return f"{user_row['username'].lower()}@example.com"
    except Exception as err:
        logging.debug(f"Could not resolve identifier via public.users: {err}")

    # 3. Fallback: If formatted as an email, return directly
    if '@' in identifier:
        return identifier

    # 4. Default domain convention fallback for username
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
        'course_id, schedule(day, class_start, class_end, prof_course_id, prof_course(course_id, course(course_name)))'
    ).eq('student_id', student_id).execute()

    existing_entries = []
    for row in (res.data or []):
        sch = _rel(row, 'schedule') or {}
        pc = _rel(sch, 'prof_course') or {}
        crs = _rel(pc, 'course') or _rel(sch, 'course') or {}
        existing_entries.append({
            'course_id': row.get('course_id') or pc.get('course_id'),
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
    if has_request_context() and session.get('schedule_preview'):
        return session.get('schedule_preview')
    req_preview_id = session.get('preview_id') if has_request_context() else None
    req_user_id = session.get('user_id') if has_request_context() else None
    key = str(preview_id or req_preview_id or user_id or req_user_id or 'anon')
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
    return []

def _set_preview_for_user(preview_data, user_id=None, preview_id=None):
    """Save schedule preview in server-side storage and prevent cookie size overflow."""
    from flask import has_request_context
    if has_request_context() and not session.get('preview_id'):
        session['preview_id'] = str(uuid.uuid4())
    req_preview_id = session.get('preview_id') if has_request_context() else None
    req_user_id = session.get('user_id') if has_request_context() else None
    key = str(preview_id or req_preview_id or user_id or req_user_id or 'anon')
    _SERVER_PREVIEW_STORE[key] = preview_data
    tmp_path = os.path.join(tempfile.gettempdir(), f'sched_preview_{key}.json')
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(preview_data, f)
    except Exception as e:
        logging.error(f"Error saving preview cache for key {key}: {e}")

    if has_request_context():
        if 'schedule_preview' in session:
            session['schedule_preview'] = preview_data
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
            'id': '3rd-year-wst',
            'title': '3rd Year Schedules — WST',
            'year_level': '3',
            'track': 'WST',
            'sections': [],
        },
        {
            'id': '3rd-year-dst',
            'title': '3rd Year Schedules — DST',
            'year_level': '3',
            'track': 'DST',
            'sections': [],
        },
        {
            'id': '3rd-year-nst',
            'title': '3rd Year Schedules — NST',
            'year_level': '3',
            'track': 'NST',
            'sections': [],
        },
        {
            'id': '4th-year',
            'title': '4th Year Schedules',
            'year_level': '4',
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

        combined_str = f"{sec_name} {sec_major}".upper()
        for entry in entries:
            combined_str += f" {entry.get('course_name', '')} {entry.get('major', '')}".upper()

        if year_level == '1':
            group_map['1st-year']['sections'].append(item)
        elif year_level == '2':
            group_map['2nd-year']['sections'].append(item)
        elif year_level == '3':
            if any(k in combined_str for k in ['WST', 'WEB', 'WEB DEVELOPMENT']) or sec_name.endswith('WST') or sec_name.endswith('WEB') or ' WST' in combined_str or ' W ' in f" {combined_str} ":
                group_map['3rd-year-wst']['sections'].append(item)
            elif any(k in combined_str for k in ['DST', 'DB', 'DATABASE', 'DATABASE SYSTEMS']) or sec_name.endswith('DST') or sec_name.endswith('DB') or ' DST' in combined_str or ' D ' in f" {combined_str} ":
                group_map['3rd-year-dst']['sections'].append(item)
            elif any(k in combined_str for k in ['NST', 'NET', 'NETWORKING', 'NETWORK', 'NETWORK SYSTEMS']) or sec_name.endswith('NST') or sec_name.endswith('NET') or ' NST' in combined_str or ' N ' in f" {combined_str} ":
                group_map['3rd-year-nst']['sections'].append(item)
            else:
                group_map['3rd-year']['sections'].append(item)
        elif year_level == '4':
            if any(k in combined_str for k in ['WST', 'WEB', 'WEB DEVELOPMENT']) or sec_name.endswith('WST') or sec_name.endswith('WEB') or ' WST' in combined_str or ' W ' in f" {combined_str} ":
                group_map['4th-year-wst']['sections'].append(item)
            elif any(k in combined_str for k in ['DST', 'DB', 'DATABASE', 'DATABASE SYSTEMS']) or sec_name.endswith('DST') or sec_name.endswith('DB') or ' DST' in combined_str or ' D ' in f" {combined_str} ":
                group_map['4th-year-dst']['sections'].append(item)
            elif any(k in combined_str for k in ['NST', 'NET', 'NETWORKING', 'NETWORK', 'NETWORK SYSTEMS']) or sec_name.endswith('NST') or sec_name.endswith('NET') or ' NST' in combined_str or ' N ' in f" {combined_str} ":
                group_map['4th-year-nst']['sections'].append(item)
            else:
                group_map['4th-year']['sections'].append(item)
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

        if major_filter and g['year_level'] in ('3', '4'):
            m_str = str(major_filter).strip().upper()
            g_track = str(g['track'] or '').upper()
            g_title = str(g['title']).upper()
            match = False
            if g_track:
                if 'WST' in g_track or 'WST' in g_title:
                    if any(k in m_str for k in ['WST', 'WEB']):
                        match = True
                if 'DST' in g_track or 'DST' in g_title:
                    if any(k in m_str for k in ['DST', 'DB', 'DATABASE']):
                        match = True
                if 'NST' in g_track or 'NST' in g_title:
                    if any(k in m_str for k in ['NST', 'NET', 'NETWORK']):
                        match = True
            else:
                if any(k in m_str for k in ['GENERAL', 'NONE', 'ALL', '']):
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
        all_prof_courses = []
        if user_role == 'Viewer' and department:
            pc_res = supabase.table('prof_course').select('prof_course_id, course_id, prof_id, professor(prof_id, first_name, last_name), course(course_id, course_name, program)').eq('professor.department', department).execute()
        else:
            pc_res = supabase.table('prof_course').select('prof_course_id, course_id, prof_id, professor(prof_id, first_name, last_name), course(course_id, course_name, program)').execute()
        assignments = pc_res.data or []
        for row in assignments:
            p = _rel(row, 'professor') or {}
            c = _rel(row, 'course') or {}
            prof_name = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or 'TBA'
            prof_entry = {'id': p.get('prof_id'), 'name': prof_name}
            if row.get('course_id'):
                course_key = str(row['course_id'])
                prof_course_assignments.setdefault(course_key, []).append(prof_entry)
            c_name = (c.get('course_name') if c else None) or f"Course #{row.get('course_id')}"
            all_prof_courses.append({
                'prof_course_id': row.get('prof_course_id'),
                'prof_id': p.get('prof_id'),
                'course_id': row.get('course_id'),
                'prof_name': prof_name,
                'course_name': c_name,
                'label': f"{prof_name} - {c_name}",
            })
        all_prof_courses.sort(key=lambda x: (x['prof_name'].lower(), x['course_name'].lower()))
    except Exception:
        courses = []
        rooms = []
        prof_course_assignments = {}
        all_prof_courses = []

    room_utilization = []
    if preview:
        usage_counts = {}
        for entry in preview:
            rid = entry.get('room_id')
            rname = entry.get('room_name') or (f"Room {rid}" if rid else 'Unassigned (TBA)')
            if rid is not None:
                usage_counts[rid] = usage_counts.get(rid, {'name': rname, 'count': 0})
                usage_counts[rid]['count'] += 1
            elif entry.get('time_range') or entry.get('start'):
                usage_counts['tba'] = usage_counts.get('tba', {'name': 'Unassigned (TBA)', 'count': 0})
                usage_counts['tba']['count'] += 1

        seen_rids = set()
        for r in rooms:
            rid = r.get('room_id')
            if rid is not None:
                seen_rids.add(rid)
                info = usage_counts.get(rid)
                count = info['count'] if info else 0
                rname = r.get('room_name') or f"Room {rid}"
                room_utilization.append({
                    'room_id': rid,
                    'room_name': rname,
                    'room_type': r.get('room_type', ''),
                    'count': count
                })

        for rid, info in usage_counts.items():
            if rid != 'tba' and rid not in seen_rids:
                room_utilization.append({
                    'room_id': rid,
                    'room_name': info['name'],
                    'room_type': '',
                    'count': info['count']
                })
        if 'tba' in usage_counts:
            room_utilization.append({
                'room_id': None,
                'room_name': usage_counts['tba']['name'],
                'room_type': '',
                'count': usage_counts['tba']['count']
            })

    return {
        'sections_with_entries': sections_with_entries,
        'year_groups': year_groups,
        'courses': courses,
        'rooms': rooms,
        'prof_course_assignments': prof_course_assignments,
        'all_prof_courses': all_prof_courses,
        'room_utilization': room_utilization,
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
            elif not is_second_sem and y == 4:
                # Distribute 4th year sections across Database, Web, Networking in 1st Semester
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
        email = request.form.get('email', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        # Validate required fields
        if not first_name or not last_name or not program or not password or not confirm_password:
            error = 'All fields are required.'
        elif not email and not username:
            error = 'Please provide an email, a username, or both.'
        # Validate email format if email was provided
        elif email and ('@' not in email or '.' not in email.split('@')[-1]):
            error = 'Please enter a valid email address.'
        # Validate program selection
        elif program not in ['BSIT', 'BSBA']:
            error = 'Please select a valid program.'
        # Validate password match
        elif password != confirm_password:
            error = 'Passwords do not match.'
        else:
            try:
                provided_email = bool(email)
                provided_username = bool(username)

                effective_email = email if provided_email else f"{username.lower()}@example.com"
                effective_username = username if provided_username else email.split('@')[0]

                res = supabase.auth.sign_up({
                    'email': effective_email,
                    'password': password,
                    'options': {
                        'data': {
                            'first_name': first_name,
                            'last_name': last_name,
                            'username': effective_username,
                            'program': program,
                            'role': 'Viewer',
                            'provided_email': provided_email,
                            'provided_username': provided_username,
                        }
                    }
                })

                if not res or not res.user:
                    error = 'Unable to create account. Please try again.'
                else:
                    try:
                        full_name = f"{first_name} {last_name}".strip()
                        if not full_name:
                            full_name = effective_username

                        if provided_email and provided_username:
                            detail = f'Self-registered: {effective_username} ({effective_email})'
                        elif provided_email:
                            detail = f'Self-registered: {effective_email}'
                        else:
                            detail = f'Self-registered: {effective_username}'

                        supabase.table('activity_log').insert({
                            'username': full_name,
                            'action': 'create',
                            'target_type': 'user',
                            'target_detail': detail,
                        }).execute()
                    except Exception:
                        pass

                    return redirect(url_for('login'))

            except Exception as err:
                logging.error(f"Signup error: {err}")
                msg = str(err).lower()
                if 'already registered' in msg or 'already been registered' in msg or 'already exists' in msg:
                    error = 'Email or username already registered.'
                else:
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
        supabase.table('users').update({'role': 'admin'}).eq('email', email).execute()
        return f"User '{email}' updated to admin role in database. You can close this page."
    except Exception as err:
        return f"Error: {err}"

#-------------------------------------------------------User Management----------------------------------------------------------------------------------------------
@app.route('/users')
@admin_required
def users():
    search_query = request.args.get('search', '').strip()
    users_list = []

    try:
        users_list = _list_users()
        if search_query:
            q = search_query.lower()
            users_list = [
                u for u in users_list
                if any(q in (u.get(k) or '').lower() for k in ('first_name', 'last_name', 'username', 'email', 'program', 'role'))
            ]
        users_list.sort(key=lambda u: (str(u.get('role') or ''), str(u.get('last_name') or ''), str(u.get('first_name') or '')))
    except Exception as err:
        logging.error(f"Error fetching users: {err}")
        flash(f'Error fetching users: {err}', 'error')

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
                if any(q in (u.get(k) or '').lower() for k in ('first_name', 'last_name', 'username', 'email', 'program', 'role'))
            ]
        users_list.sort(key=lambda u: (str(u.get('role') or ''), str(u.get('last_name') or ''), str(u.get('first_name') or '')))
    except Exception as err:
        logging.error(f"Error fetching users: {err}")
        return jsonify({'users': [], 'error': str(err)}), 500

    return jsonify({'users': users_list})

@app.route('/edit_user/<user_id>', methods=['POST'])
@admin_required
def edit_user(user_id):
    try:
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

        update_payload = {
            'first_name': first_name,
            'last_name': last_name,
            'role': role,
            'program': program,
        }
        if email:
            update_payload['email'] = email

        res = supabase.table('users').update(update_payload).eq('id', user_id).execute()
        if not res.data:
            return jsonify({'success': False, 'message': 'User not found.'}), 404

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

        supabase.table('users').delete().eq('id', user_id).execute()

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

        user_id = None
        try:
            auth_res = supabase.auth.sign_up({
                'email': email,
                'password': password,
                'options': {
                    'data': {
                        'first_name': first_name,
                        'last_name': last_name,
                        'username': email.split('@')[0],
                        'program': program,
                        'role': role,
                    }
                }
            })
            if auth_res and auth_res.user:
                user_id = auth_res.user.id
        except Exception as auth_err:
            msg = str(auth_err).lower()
            if 'already registered' in msg or 'already been registered' in msg or 'already exists' in msg or 'duplicate' in msg:
                return jsonify({'success': False, 'message': 'Email already exists.'}), 400
            logging.warning(f"Auth sign_up info during create_user: {auth_err}")

        payload = {
            'email': email,
            'username': email.split('@')[0],
            'first_name': first_name,
            'last_name': last_name,
            'program': program,
            'role': role,
        }
        if user_id:
            payload['id'] = str(user_id)

        supabase.table('users').upsert(payload, on_conflict='email').execute()

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
                user_id = None
                try:
                    auth_res = supabase.auth.sign_up({
                        'email': account['email'],
                        'password': account['password'],
                        'options': {
                            'data': {
                                'first_name': account['first_name'],
                                'last_name': account['last_name'],
                                'username': account['email'].split('@')[0],
                                'program': account['program'],
                                'role': account['role'],
                            },
                        },
                    })
                    if auth_res and auth_res.user:
                        user_id = auth_res.user.id
                except Exception:
                    pass

                payload = {
                    'email': account['email'],
                    'username': account['email'].split('@')[0],
                    'first_name': account['first_name'],
                    'last_name': account['last_name'],
                    'program': account['program'],
                    'role': account['role'],
                }
                if user_id:
                    payload['id'] = str(user_id)
                supabase.table('users').upsert(payload, on_conflict='email').execute()
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

def _score_day_for_section(day, year_level, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used=False, strict=True):
    rules = _get_year_rules(year_level)
    max_per_day = rules['max_courses_per_day']
    max_late = rules['max_late_days']
    current_count = courses_per_day.get(day, 0)
    current_late = len(late_days)

    if strict:
        # Hard-constraint violations return score of -1 (reject)
        if year_level == 1:
            if current_count >= max_per_day:
                return -1
            if current_count == 1 and two_course_day_used:
                return -1
        else:
            if current_count >= max_per_day:
                return -1

        if slot_is_late and current_late >= max_late and day not in late_days:
            return -1

    score = 1000
    if year_level == 1:
        score -= current_count * 100
        score -= days_tried.get(day, 0) * 10
        if slot_is_late:
            score -= 300
    elif year_level == 2:
        score -= current_count * 50
        score -= days_tried.get(day, 0) * 5
        if slot_is_late:
            score -= 150
            if day in late_days:
                score -= 100
    elif year_level in (3, 4):
        if current_count == 0:
            score += 10
        elif current_count == 1:
            score += 50
        else:
            score -= 20 * current_count
        score -= days_tried.get(day, 0) * 2
        if slot_is_late:
            score -= 50

    return score

def _is_lab_room_type(room_type):
    if not room_type:
        return False
    rt = str(room_type).strip().lower()
    return 'laboratory' in rt or 'lab' in rt


def _is_lecture_room_type(room_type):
    if not room_type:
        return False
    rt = str(room_type).strip().lower()
    if 'laboratory' in rt or 'lab' in rt:
        return False
    return 'lecture' in rt or rt != ''


def _room_matches_session(room, session_type):
    if not room:
        return False
    rtype = room.get('room_type') or ''
    stype = str(session_type or '').strip().lower()
    if 'lab' in stype or 'laboratory' in stype:
        return _is_lab_room_type(rtype)
    return _is_lecture_room_type(rtype)


def _validate_schedule_room_types(entries, all_rooms_map=None):
    """
    Strict validation to ensure:
    - No lecture course is assigned to a lab room.
    - No lab course is assigned to a lecture room.
    Returns (is_valid: bool, errors: list[str]).
    """
    if all_rooms_map is None:
        all_rooms_map = {}
    errors = []
    for entry in entries:
        room_id = entry.get('room_id')
        if not room_id:
            continue
        try:
            rid_int = int(room_id)
        except (ValueError, TypeError):
            continue
        room_data = all_rooms_map.get(rid_int)
        if not room_data:
            continue
        room_name = entry.get('room_name') or room_data.get('room_name') or f"Room ID {room_id}"
        room_type = room_data.get('room_type') or ''
        session_type = entry.get('session_type') or 'Lecture'
        course_name = entry.get('course_name') or f"Course ID {entry.get('course_id')}"
        section = entry.get('section') or ''

        if not _room_matches_session(room_data, session_type):
            err_msg = (
                f"Room mismatch: Course '{course_name}' section '{section}' is a {session_type} session "
                f"but is assigned to '{room_name}' which is a {room_type}."
            )
            errors.append(err_msg)
    return len(errors) == 0, errors


def _select_least_used_room(candidate_rooms, day, start_time, end_time, room_bookings, room_usage, room_last_used=None, room_order=None):
    """
    Select the optimal room for a class session based on:
    Priority 1 (Hard Constraints):
      - Must be an eligible candidate room (e.g., lecture room for lecture, lab room for lab).
      - Must NOT have a scheduling conflict for the given (day, start_time, end_time).
    Priority 2 (Room Balancing):
      - Select the valid room with the lowest current assignment count in room_usage.
      - Fair tie-breaking:
        1. Lowest room_usage count across the schedule batch.
        2. Lowest room_last_used step (least recently used room).
        3. Deterministic initial room ordering (room_order) or room ID.

    Returns:
      selected_room (dict) or None if no valid room is available.
    """
    if not candidate_rooms:
        return None

    valid_rooms = []
    for rm in candidate_rooms:
        rk = rm.get('room_id')
        if rk is None:
            continue
        bookings = room_bookings.get(rk, [])
        if not _has_conflict(day, start_time, end_time, bookings):
            valid_rooms.append(rm)

    if not valid_rooms:
        return None

    last_used_map = room_last_used if room_last_used is not None else {}
    order_map = room_order if room_order is not None else {}

    selected_room = min(
        valid_rooms,
        key=lambda rm: (
            room_usage.get(rm['room_id'], 0),
            last_used_map.get(rm['room_id'], 0),
            order_map.get(rm['room_id'], 0)
        )
    )

    logging.debug(
        f"[ROOM_SELECTION] Selected Room '{selected_room.get('room_name')}' (ID: {selected_room.get('room_id')}) "
        f"for {day} {start_time}-{end_time} | Current Usage: {room_usage.get(selected_room.get('room_id'), 0)} | "
        f"Reason: Lowest valid room utilization (candidates evaluated: {len(valid_rooms)})"
    )
    return selected_room


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

def _calculate_professor_workload(entries, max_hours=40):
    """
    Calculate the total scheduled hours by summing assigned schedule durations within the week.
    Each schedule entry's duration is computed using start and end times.
    To avoid double-counting overlapping schedules on the same day, time intervals are merged per day.
    Only valid scheduled classes (non-empty, valid start/end, non-TBA day) are included.
    Returns a dict with total_scheduled_hours, remaining_hours, max_hours, workload_percentage,
    overload_hours, is_overloaded, and human-friendly display strings.
    Also annotates each entry in `entries` with `duration_hours` and `duration_text`.
    """
    try:
        max_hours = float(max_hours)
    except (ValueError, TypeError):
        max_hours = 40.0
    if max_hours <= 0:
        max_hours = 40.0

    day_intervals = {}
    valid_entries_count = 0

    for entry in (entries or []):
        day = (entry.get('day') or '').strip()
        if not day or day.upper() == 'TBA':
            entry['duration_hours'] = 0.0
            entry['duration_text'] = 'TBA'
            continue

        raw_start = entry.get('start_time_raw') or entry.get('class_start') or entry.get('start')
        raw_end = entry.get('end_time_raw') or entry.get('class_end') or entry.get('end')

        st_td = _parse_time(raw_start)
        et_td = _parse_time(raw_end)

        if st_td is None or et_td is None:
            entry['duration_hours'] = 0.0
            entry['duration_text'] = 'TBA'
            continue

        st_sec = st_td.total_seconds()
        et_sec = et_td.total_seconds()
        if et_sec <= st_sec:
            entry['duration_hours'] = 0.0
            entry['duration_text'] = '0 hrs'
            continue

        entry_dur = round((et_sec - st_sec) / 3600.0, 2)
        entry['duration_hours'] = entry_dur
        dur_display = int(entry_dur) if entry_dur == int(entry_dur) else entry_dur
        entry['duration_text'] = f"{dur_display} hr{'s' if dur_display != 1 else ''}"

        day_intervals.setdefault(day, []).append((st_sec, et_sec))
        valid_entries_count += 1

    total_seconds = 0.0
    for day, intervals in day_intervals.items():
        intervals.sort(key=lambda x: (x[0], x[1]))
        merged = []
        for start, end in intervals:
            if not merged:
                merged.append([start, end])
            else:
                prev_start, prev_end = merged[-1]
                if start <= prev_end:
                    merged[-1][1] = max(prev_end, end)
                else:
                    merged.append([start, end])
        day_total = sum(end - start for start, end in merged)
        total_seconds += day_total

    total_hours = round(total_seconds / 3600.0, 2)
    remaining_hours = round(max(0.0, max_hours - total_hours), 2)
    overload_hours = round(max(0.0, total_hours - max_hours), 2)
    is_overloaded = total_hours > max_hours
    workload_pct = round((total_hours / max_hours) * 100, 1) if max_hours > 0 else 0.0

    total_hours_disp = int(total_hours) if total_hours == int(total_hours) else total_hours
    remaining_hours_disp = int(remaining_hours) if remaining_hours == int(remaining_hours) else remaining_hours
    overload_hours_disp = int(overload_hours) if overload_hours == int(overload_hours) else overload_hours
    max_hours_disp = int(max_hours) if max_hours == int(max_hours) else max_hours

    assigned_hours_text = f"{total_hours_disp} hour{'s' if total_hours_disp != 1 else ''} assigned"
    if is_overloaded:
        remaining_hours_text = f"Overload by {overload_hours_disp} hour{'s' if overload_hours_disp != 1 else ''}"
    else:
        remaining_hours_text = f"{remaining_hours_disp} hour{'s' if remaining_hours_disp != 1 else ''} remaining"

    return {
        'total_scheduled_hours': total_hours,
        'total_scheduled_hours_display': total_hours_disp,
        'remaining_hours': remaining_hours,
        'remaining_hours_display': remaining_hours_disp,
        'overload_hours': overload_hours,
        'overload_hours_display': overload_hours_disp,
        'max_hours': max_hours,
        'max_hours_display': max_hours_disp,
        'is_overloaded': is_overloaded,
        'workload_percentage': workload_pct,
        'assigned_hours_text': assigned_hours_text,
        'remaining_hours_text': remaining_hours_text,
        'valid_classes_count': valid_entries_count,
    }


def _calculate_schedule_availability(entries, timeslots=None, context=None):
    """
    Calculate occupied and available (unoccupied) time slots and weekly calendar grid blocks
    for a specific room, section, or professor based on assigned schedule entries.

    Returns a comprehensive dictionary containing:
    - 'operating_days': sorted list of active days (Monday through Saturday)
    - 'operating_hours': dict with 'start_time', 'end_time', 'start_sec', 'end_sec', and human display
    - 'hourly_slots': list of hour intervals across the operating day (e.g. '08:00 AM - 09:00 AM')
    - 'time_markers': single hourly interval markers for the left-hand time axis
    - 'day_calendar_blocks': contiguous blocks per day for occupied classes, lunch, and available slots
    - 'day_statuses' & 'day_status_map': daily statuses (Fully Free, Partially Used, Fully Booked)
    - 'free_periods': continuous available free periods outside of classes and lunch
    - 'metrics': utilization and hour totals
    """
    context = context or {}
    entity_type = context.get('type', 'room')
    room = context.get('room')
    section = context.get('section')
    professor = context.get('professor')

    if entity_type == 'room':
        room_type = (room.get('room_type') or 'Lecture').strip() if isinstance(room, dict) else 'Lecture'
        room_name = (room.get('room_name') or 'Room').strip() if isinstance(room, dict) else (str(room) if room else 'Room')
        entity_label = f"Room {room_name}"
        section_name = ''
        prof_name = ''
    elif entity_type == 'section':
        if isinstance(section, dict):
            section_name = (section.get('section_name') or section.get('section') or 'Section').strip()
        else:
            section_name = str(section or 'Section').strip()
        room_type = 'Lecture'
        room_name = 'Room'
        entity_label = f"Section {section_name}"
        prof_name = ''
    elif entity_type == 'professor':
        if isinstance(professor, dict):
            prof_name = f"{professor.get('first_name','')} {professor.get('last_name','')}".strip() or professor.get('professor_name') or 'Professor'
        else:
            prof_name = str(professor or 'Professor').strip()
        room_type = 'Lecture'
        room_name = 'Room'
        entity_label = f"Professor {prof_name}"
        section_name = ''
    else:
        room_type = 'Lecture'
        room_name = 'Room'
        entity_label = 'Schedule'
        section_name = ''
        prof_name = ''

    if timeslots is None:
        try:
            timeslots = (supabase.table('timeslot').select('*').execute().data) or []
        except Exception:
            timeslots = []

    all_weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    weekday_idx = {d: i for i, d in enumerate(all_weekdays)}

    # Determine operating days, daily operating hours, and lunch break from timeslots or defaults
    active_days_set = set()
    earliest_start_sec = None
    latest_end_sec = None
    lunch_time_sec = None

    for ts in (timeslots or []):
        s_day = (ts.get('start_day') or 'Monday').strip().title()
        e_day = (ts.get('end_day') or 'Friday').strip().title()
        s_idx = weekday_idx.get(s_day, 0)
        e_idx = weekday_idx.get(e_day, 4)
        if e_idx < s_idx:
            e_idx = s_idx
        for idx in range(s_idx, e_idx + 1):
            active_days_set.add(all_weekdays[idx])

        st_td = _parse_time(ts.get('start_time'))
        et_td = _parse_time(ts.get('end_time'))
        if st_td is not None:
            s_sec = int(st_td.total_seconds())
            earliest_start_sec = s_sec if earliest_start_sec is None else min(earliest_start_sec, s_sec)
        if et_td is not None:
            e_sec = int(et_td.total_seconds())
            latest_end_sec = e_sec if latest_end_sec is None else max(latest_end_sec, e_sec)

        lt_td = _parse_time(ts.get('lunch_time'))
        if lt_td is not None and lunch_time_sec is None:
            lunch_time_sec = int(lt_td.total_seconds())

    # Defaults if missing or invalid: Monday through Saturday, 7:00 AM to 8:00 PM, Lunch at 12:00 PM
    if not active_days_set:
        active_days_set = {'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'}
    if earliest_start_sec is None or earliest_start_sec < 0:
        earliest_start_sec = 7 * 3600  # 7:00 AM
    if latest_end_sec is None or latest_end_sec <= earliest_start_sec:
        latest_end_sec = 20 * 3600    # 8:00 PM
    if lunch_time_sec is None:
        lunch_time_sec = 12 * 3600    # 12:00 PM

    # Lunch break parameters (1 hour duration as standard)
    lunch_start_sec = lunch_time_sec
    lunch_end_sec = lunch_start_sec + 3600
    lunch_active = (earliest_start_sec <= lunch_start_sec < latest_end_sec)
    lunch_duration_sec = (min(latest_end_sec, lunch_end_sec) - lunch_start_sec) if lunch_active else 0
    lunch_duration_hours = round(lunch_duration_sec / 3600.0, 2)

    # Ensure any day with an assigned class is included in operating days
    for e in (entries or []):
        day_val = (e.get('day') or '').strip()
        if day_val and day_val in weekday_idx:
            active_days_set.add(day_val)

    # Order operating days (Monday through Saturday standard)
    operating_days = [d for d in all_weekdays if d in active_days_set]
    operating_days.sort(key=lambda d: _DAY_ORDER.get(d, 99))

    # Parse and organize occupied intervals per day
    day_occupied_map = {d: [] for d in operating_days}
    day_entries_map = {d: [] for d in operating_days}

    for e in (entries or []):
        d = (e.get('day') or '').strip()
        if d not in day_occupied_map:
            continue
        raw_start = e.get('start_time_raw') or e.get('class_start') or e.get('start') or e.get('start_time')
        raw_end = e.get('end_time_raw') or e.get('class_end') or e.get('end') or e.get('end_time')
        st_td = _parse_time(raw_start)
        et_td = _parse_time(raw_end)
        if st_td is None or et_td is None or et_td <= st_td:
            continue
        s_sec = int(st_td.total_seconds())
        e_sec = int(et_td.total_seconds())
        day_occupied_map[d].append((s_sec, e_sec))
        day_entries_map[d].append({
            'start_sec': s_sec,
            'end_sec': e_sec,
            'entry': e
        })

    # Usable scheduling hours per day (strictly excluding lunch break)
    raw_day_operating_hours = (latest_end_sec - earliest_start_sec) / 3600.0
    day_usable_hours = max(0.0, raw_day_operating_hours - (lunch_duration_hours if lunch_active else 0.0))

    free_periods = []
    free_periods_by_day = {d: [] for d in operating_days}
    day_statuses = []
    day_status_map = {}

    for d in operating_days:
        raw_intervals = list(day_occupied_map[d])
        raw_intervals.sort(key=lambda x: (x[0], x[1]))

        # Calculate actual class occupied seconds (excluding lunch)
        class_merged = []
        for s, e in raw_intervals:
            clamped_s = max(earliest_start_sec, min(latest_end_sec, s))
            clamped_e = max(earliest_start_sec, min(latest_end_sec, e))
            if clamped_e <= clamped_s:
                continue
            if not class_merged:
                class_merged.append([clamped_s, clamped_e])
            else:
                if clamped_s <= class_merged[-1][1]:
                    class_merged[-1][1] = max(class_merged[-1][1], clamped_e)
                else:
                    class_merged.append([clamped_s, clamped_e])

        day_occupied_sec = sum(e - s for s, e in class_merged)
        day_occupied_h = round(day_occupied_sec / 3600.0, 2)
        effective_occupied_h = min(day_occupied_h, day_usable_hours)
        day_free_h = round(max(0.0, day_usable_hours - effective_occupied_h), 2)
        day_occ_disp = int(day_occupied_h) if day_occupied_h.is_integer() else day_occupied_h
        day_free_disp = int(day_free_h) if day_free_h.is_integer() else day_free_h

        # Compute continuous free periods outside of both classes and lunch break
        blocked_intervals = list(raw_intervals)
        if lunch_active:
            blocked_intervals.append((lunch_start_sec, lunch_end_sec))
        blocked_intervals.sort(key=lambda x: (x[0], x[1]))

        merged_blocked = []
        for s, e in blocked_intervals:
            clamped_s = max(earliest_start_sec, min(latest_end_sec, s))
            clamped_e = max(earliest_start_sec, min(latest_end_sec, e))
            if clamped_e <= clamped_s:
                continue
            if not merged_blocked:
                merged_blocked.append([clamped_s, clamped_e])
            else:
                if clamped_s <= merged_blocked[-1][1]:
                    merged_blocked[-1][1] = max(merged_blocked[-1][1], clamped_e)
                else:
                    merged_blocked.append([clamped_s, clamped_e])

        cursor = earliest_start_sec
        for blk_s, blk_e in merged_blocked:
            if blk_s > cursor:
                free_dur_h = round((blk_s - cursor) / 3600.0, 2)
                dur_disp = int(free_dur_h) if free_dur_h.is_integer() else free_dur_h
                period_data = {
                    'day': d,
                    'start_sec': cursor,
                    'end_sec': blk_s,
                    'start_time': _format_time(timedelta(seconds=cursor)),
                    'end_time': _format_time(timedelta(seconds=blk_s)),
                    'time_range': f"{_format_time(timedelta(seconds=cursor))} - {_format_time(timedelta(seconds=blk_s))}",
                    'duration_hours': free_dur_h,
                    'duration_text': f"{dur_disp} hr{'s' if dur_disp != 1 else ''}",
                    'room_type': room_type,
                    'room_name': room_name,
                    'section': section_name,
                    'status': 'available'
                }
                free_periods.append(period_data)
                free_periods_by_day[d].append(period_data)
            cursor = max(cursor, blk_e)

        if cursor < latest_end_sec:
            free_dur_h = round((latest_end_sec - cursor) / 3600.0, 2)
            dur_disp = int(free_dur_h) if free_dur_h.is_integer() else free_dur_h
            period_data = {
                'day': d,
                'start_sec': cursor,
                'end_sec': latest_end_sec,
                'start_time': _format_time(timedelta(seconds=cursor)),
                'end_time': _format_time(timedelta(seconds=latest_end_sec)),
                'time_range': f"{_format_time(timedelta(seconds=cursor))} - {_format_time(timedelta(seconds=latest_end_sec))}",
                'duration_hours': free_dur_h,
                'duration_text': f"{dur_disp} hr{'s' if dur_disp != 1 else ''}",
                'room_type': room_type,
                'room_name': room_name,
                'section': section_name,
                'status': 'available'
            }
            free_periods.append(period_data)
            free_periods_by_day[d].append(period_data)

        if day_occupied_h <= 0.0:
            status_key = 'free'
            status_label = 'Fully Free'
            badge_class = 'badge-success'
            pill_class = 'bg-success text-white'
            summary_text = f"{day_free_disp} hrs available"
        elif day_free_h <= 0.0:
            status_key = 'booked'
            status_label = 'Fully Booked'
            badge_class = 'badge-danger'
            pill_class = 'bg-danger text-white'
            summary_text = f"{day_occ_disp} hrs booked"
        else:
            status_key = 'partial'
            status_label = 'Partially Used'
            badge_class = 'badge-warning'
            pill_class = 'bg-warning text-dark'
            summary_text = f"{day_occ_disp}h used • {day_free_disp}h free"

        d_status = {
            'day': d,
            'operating_hours': day_usable_hours,
            'raw_operating_hours': raw_day_operating_hours,
            'occupied_hours': day_occupied_h,
            'occupied_hours_display': day_occ_disp,
            'free_hours': day_free_h,
            'free_hours_display': day_free_disp,
            'status': status_key,
            'label': status_label,
            'badge_class': badge_class,
            'pill_class': pill_class,
            'summary': summary_text,
            'classes_count': len(raw_intervals),
            'free_periods_count': len(free_periods_by_day[d]),
            'lunch_hours': lunch_duration_hours if lunch_active else 0.0,
        }
        day_statuses.append(d_status)
        day_status_map[d] = d_status

    # Build 1-hour slots for the timetable grid matrix
    hourly_slots = []
    slot_curr = earliest_start_sec
    while slot_curr + 3600 <= latest_end_sec:
        s_next = slot_curr + 3600
        is_lunch = lunch_active and (slot_curr < lunch_end_sec and lunch_start_sec < s_next)
        hourly_slots.append({
            'start_sec': slot_curr,
            'end_sec': s_next,
            'start_time': _format_time(timedelta(seconds=slot_curr)),
            'end_time': _format_time(timedelta(seconds=s_next)),
            'slot_label': f"{_format_time(timedelta(seconds=slot_curr))} - {_format_time(timedelta(seconds=s_next))}",
            'is_lunch_slot': is_lunch,
        })
        slot_curr = s_next

    # Build matrix rows: each row represents an hourly slot with a cell for each operating day
    timetable_grid = []
    for slot in hourly_slots:
        s_sec = slot['start_sec']
        e_sec = slot['end_sec']
        is_lunch_slot = slot['is_lunch_slot']
        row = {
            'slot_label': slot['slot_label'],
            'start_time': slot['start_time'],
            'end_time': slot['end_time'],
            'is_lunch_slot': is_lunch_slot,
            'days': {}
        }
        for d in operating_days:
            matching_entry = None
            for item in day_entries_map[d]:
                if s_sec < item['end_sec'] and item['start_sec'] < e_sec:
                    matching_entry = item
                    break

            if matching_entry:
                ent = matching_entry['entry']
                is_partial = (matching_entry['start_sec'] > s_sec or matching_entry['end_sec'] < e_sec)
                row['days'][d] = {
                    'type': 'occupied',
                    'is_partial': is_partial,
                    'course_name': ent.get('course_name') or 'Scheduled Class',
                    'professor': ent.get('professor') or ent.get('professor_name') or (prof_name if entity_type == 'professor' else 'TBA'),
                    'section': ent.get('section') or (section_name if entity_type == 'section' else ''),
                    'session_type': ent.get('session_type') or 'Lecture',
                    'room_name': ent.get('room_name') or ent.get('room') or (room_name if entity_type == 'room' else 'TBA'),
                    'semester': ent.get('semester') or '',
                    'major': ent.get('major') or '',
                    'year_level': ent.get('year_level') or '',
                    'time_range': f"{_format_time(timedelta(seconds=matching_entry['start_sec']))} - {_format_time(timedelta(seconds=matching_entry['end_sec']))}",
                    'raw_start': ent.get('start_time_raw') or ent.get('class_start') or ent.get('start'),
                    'raw_end': ent.get('end_time_raw') or ent.get('class_end') or ent.get('end'),
                    'schedule_id': ent.get('schedule_id') or ent.get('id'),
                }
            elif is_lunch_slot:
                row['days'][d] = {
                    'type': 'lunch',
                    'is_partial': False,
                    'label': 'Lunch Break (Unavailable)',
                    'room_type': room_type,
                    'time_range': slot['slot_label'],
                    'room_name': room_name,
                    'is_schedulable': False,
                }
            else:
                row['days'][d] = {
                    'type': 'available',
                    'is_partial': False,
                    'label': 'Available',
                    'room_type': room_type,
                    'time_range': slot['slot_label'],
                    'room_name': room_name,
                    'is_schedulable': True,
                }
        timetable_grid.append(row)

    # Build single hour markers for the calendar time axis
    time_markers = []
    marker_curr = earliest_start_sec
    total_window_sec = max(3600, latest_end_sec - earliest_start_sec)
    total_window_hours = round(total_window_sec / 3600.0, 2)
    total_window_minutes = round(total_window_sec / 60.0, 2)

    while marker_curr <= latest_end_sec:
        m_offset_sec = marker_curr - earliest_start_sec
        m_offset_min = round(m_offset_sec / 60.0, 2)
        m_offset_h = round(m_offset_sec / 3600.0, 2)
        top_pct = round((m_offset_sec / total_window_sec) * 100.0, 4)
        time_markers.append({
            'label': _format_time(timedelta(seconds=marker_curr)),
            'time_sec': marker_curr,
            'offset_minutes': m_offset_min,
            'offset_hours': m_offset_h,
            'top_percent': top_pct,
            'is_last': (marker_curr == latest_end_sec),
        })
        marker_curr += 3600

    # Build contiguous calendar blocks for each operating day (prevents duplicate subject renders)
    day_calendar_blocks = {d: [] for d in operating_days}
    for d in operating_days:
        blocks = []

        # 1. Occupied class blocks (each subject renders ONCE with full contiguous duration)
        for item in day_entries_map[d]:
            ent = item['entry']
            s_sec = item['start_sec']
            e_sec = item['end_sec']
            clamped_s = max(earliest_start_sec, min(latest_end_sec, s_sec))
            clamped_e = max(earliest_start_sec, min(latest_end_sec, e_sec))
            dur_sec = clamped_e - clamped_s
            if dur_sec <= 0:
                continue

            top_min = round((clamped_s - earliest_start_sec) / 60.0, 2)
            dur_min = round(dur_sec / 60.0, 2)
            dur_h = round(dur_sec / 3600.0, 2)
            dur_disp = int(dur_h) if dur_h.is_integer() else dur_h
            top_pct = round(((clamped_s - earliest_start_sec) / total_window_sec) * 100.0, 4)
            height_pct = round((dur_sec / total_window_sec) * 100.0, 4)

            # Clean time range display (08:00 AM - 10:00 AM)
            start_fmt = _format_time(timedelta(seconds=s_sec))
            end_fmt = _format_time(timedelta(seconds=e_sec))
            clean_time_range = f"{start_fmt} - {end_fmt}"

            assigned_prof = ent.get('professor') or ent.get('professor_name') or (prof_name if entity_type == 'professor' else 'TBA')
            assigned_sec = ent.get('section') or (section_name if entity_type == 'section' else '')
            assigned_room = ent.get('room_name') or ent.get('room') or (room_name if entity_type == 'room' else 'TBA')

            blocks.append({
                'id': ent.get('schedule_id') or ent.get('id') or f"occ_{d}_{clamped_s}",
                'schedule_id': ent.get('schedule_id') or ent.get('id'),
                'type': 'occupied',
                'prof_course_id': ent.get('prof_course_id'),
                'room_id': ent.get('room_id'),
                'course_name': ent.get('course_name') or 'Scheduled Class',
                'section': assigned_sec,
                'professor': assigned_prof,
                'professor_name': assigned_prof,
                'session_type': ent.get('session_type') or 'Lecture',
                'room_name': assigned_room,
                'room': assigned_room,
                'room_type': ent.get('room_type') or room_type,
                'semester': ent.get('semester') or '',
                'major': ent.get('major') or '',
                'year_level': ent.get('year_level') or '',
                'start_time': start_fmt,
                'end_time': end_fmt,
                'time_range': clean_time_range,
                'start_sec': s_sec,
                'end_sec': e_sec,
                'top_offset_minutes': top_min,
                'duration_minutes': dur_min,
                'duration_hours': dur_h,
                'duration_text': f"{dur_disp} hr{'s' if dur_disp != 1 else ''}",
                'top_percent': top_pct,
                'height_percent': height_pct,
                'is_schedulable': False,
            })

        # 2. Lunch Break block (system-blocked non-schedulable contiguous period)
        if lunch_active:
            clamped_ls = max(earliest_start_sec, min(latest_end_sec, lunch_start_sec))
            clamped_le = max(earliest_start_sec, min(latest_end_sec, lunch_end_sec))
            if clamped_le > clamped_ls:
                l_dur_sec = clamped_le - clamped_ls
                l_top_min = round((clamped_ls - earliest_start_sec) / 60.0, 2)
                l_dur_min = round(l_dur_sec / 60.0, 2)
                l_dur_h = round(l_dur_sec / 3600.0, 2)
                l_dur_disp = int(l_dur_h) if l_dur_h.is_integer() else l_dur_h
                lunch_time_fmt = f"{_format_time(timedelta(seconds=lunch_start_sec))} - {_format_time(timedelta(seconds=lunch_end_sec))}"
                blocks.append({
                    'id': f"lunch_{d}",
                    'type': 'lunch',
                    'label': 'Lunch Break (Unavailable)',
                    'start_time': _format_time(timedelta(seconds=lunch_start_sec)),
                    'end_time': _format_time(timedelta(seconds=lunch_end_sec)),
                    'time_range': lunch_time_fmt,
                    'start_sec': lunch_start_sec,
                    'end_sec': lunch_end_sec,
                    'top_offset_minutes': l_top_min,
                    'duration_minutes': l_dur_min,
                    'duration_hours': l_dur_h,
                    'duration_text': f"{l_dur_disp} hr{'s' if l_dur_disp != 1 else ''}",
                    'top_percent': round(((clamped_ls - earliest_start_sec) / total_window_sec) * 100.0, 4),
                    'height_percent': round((l_dur_sec / total_window_sec) * 100.0, 4),
                    'is_schedulable': False,
                })

        # 3. Available free period blocks (strictly outside classes and lunch)
        for idx, period in enumerate(free_periods_by_day[d]):
            p_s = period['start_sec']
            p_e = period['end_sec']
            p_dur_sec = p_e - p_s
            if p_dur_sec <= 0:
                continue
            p_top_min = round((p_s - earliest_start_sec) / 60.0, 2)
            p_dur_min = round(p_dur_sec / 60.0, 2)
            blocks.append({
                'id': f"avail_{d}_{idx}",
                'type': 'available',
                'label': 'Available',
                'room_name': room_name,
                'room_type': room_type,
                'section': section_name,
                'start_time': period['start_time'],
                'end_time': period['end_time'],
                'time_range': period['time_range'],
                'start_sec': p_s,
                'end_sec': p_e,
                'top_offset_minutes': p_top_min,
                'duration_minutes': p_dur_min,
                'duration_hours': period['duration_hours'],
                'duration_text': period['duration_text'],
                'top_percent': round(((p_s - earliest_start_sec) / total_window_sec) * 100.0, 4),
                'height_percent': round((p_dur_sec / total_window_sec) * 100.0, 4),
                'is_schedulable': True,
            })

        # Sort blocks chronologically for consistent rendering
        blocks.sort(key=lambda b: (b['top_offset_minutes'], b['start_sec']))
        day_calendar_blocks[d] = blocks

    total_usable_operating_hours = round(len(operating_days) * day_usable_hours, 2)
    raw_operating_hours = round(len(operating_days) * raw_day_operating_hours, 2)
    total_lunch_hours = round(len(operating_days) * lunch_duration_hours, 2) if lunch_active else 0.0
    total_occupied_hours = round(sum(d['occupied_hours'] for d in day_statuses), 2)
    total_available_hours = round(max(0.0, total_usable_operating_hours - total_occupied_hours), 2)
    utilization_rate = round((total_occupied_hours / total_usable_operating_hours) * 100, 1) if total_usable_operating_hours > 0 else 0.0

    total_occ_disp = int(total_occupied_hours) if total_occupied_hours.is_integer() else total_occupied_hours
    total_avail_disp = int(total_available_hours) if total_available_hours.is_integer() else total_available_hours
    total_oper_disp = int(total_usable_operating_hours) if total_usable_operating_hours.is_integer() else total_usable_operating_hours
    total_lunch_disp = int(total_lunch_hours) if total_lunch_hours.is_integer() else total_lunch_hours

    free_days_count = sum(1 for d in day_statuses if d['status'] == 'free')
    partial_days_count = sum(1 for d in day_statuses if d['status'] == 'partial')
    booked_days_count = sum(1 for d in day_statuses if d['status'] == 'booked')

    lunch_break_info = {
        'start_time': _format_time(timedelta(seconds=lunch_start_sec)),
        'end_time': _format_time(timedelta(seconds=lunch_end_sec)),
        'start_sec': lunch_start_sec,
        'end_sec': lunch_end_sec,
        'duration_hours': lunch_duration_hours,
        'is_active': lunch_active,
        'range_text': f"{_format_time(timedelta(seconds=lunch_start_sec))} - {_format_time(timedelta(seconds=lunch_end_sec))}",
    }

    metrics = {
        'total_operating_hours': total_usable_operating_hours,
        'total_operating_hours_display': total_oper_disp,
        'raw_operating_hours': raw_operating_hours,
        'total_lunch_hours': total_lunch_hours,
        'total_lunch_hours_display': total_lunch_disp,
        'lunch_break_info': lunch_break_info,
        'total_occupied_hours': total_occupied_hours,
        'total_occupied_hours_display': total_occ_disp,
        'total_available_hours': total_available_hours,
        'total_available_hours_display': total_avail_disp,
        'utilization_rate': utilization_rate,
        'free_days_count': free_days_count,
        'partial_days_count': partial_days_count,
        'booked_days_count': booked_days_count,
        'room_type': room_type,
        'room_name': room_name,
        'section_name': section_name,
        'professor_name': prof_name,
        'entity_type': entity_type,
        'entity_label': entity_label,
        'operating_start_time': _format_time(timedelta(seconds=earliest_start_sec)),
        'operating_end_time': _format_time(timedelta(seconds=latest_end_sec)),
        'operating_range_text': f"{_format_time(timedelta(seconds=earliest_start_sec))} - {_format_time(timedelta(seconds=latest_end_sec))}",
        'operating_days_count': len(operating_days),
        'total_classes_count': len(entries or []),
        'total_free_periods_count': len(free_periods),
    }

    return {
        'operating_days': operating_days,
        'operating_hours': {
            'start_time': _format_time(timedelta(seconds=earliest_start_sec)),
            'end_time': _format_time(timedelta(seconds=latest_end_sec)),
            'start_sec': earliest_start_sec,
            'end_sec': latest_end_sec,
            'range_text': f"{_format_time(timedelta(seconds=earliest_start_sec))} - {_format_time(timedelta(seconds=latest_end_sec))}",
            'day_hours': day_usable_hours,
            'raw_day_hours': raw_day_operating_hours,
            'total_window_hours': total_window_hours,
            'total_window_minutes': total_window_minutes,
        },
        'lunch_break': lunch_break_info,
        'time_markers': time_markers,
        'day_calendar_blocks': day_calendar_blocks,
        'total_window_hours': total_window_hours,
        'total_window_minutes': total_window_minutes,
        'hourly_slots': hourly_slots,
        'timetable_grid': timetable_grid,
        'free_periods': free_periods,
        'free_periods_by_day': free_periods_by_day,
        'day_statuses': day_statuses,
        'day_status_map': day_status_map,
        'metrics': metrics,
        'entity_type': entity_type,
        'entity_label': entity_label,
    }


def _calculate_room_availability(entries, timeslots=None, room=None):
    """
    Backwards-compatible wrapper for calculating room availability.
    """
    return _calculate_schedule_availability(entries, timeslots=timeslots, context={'type': 'room', 'room': room})


def _calculate_section_availability(entries, timeslots=None, section=None):
    """
    Calculate schedule availability and weekly calendar blocks for a section.
    """
    return _calculate_schedule_availability(entries, timeslots=timeslots, context={'type': 'section', 'section': section})


def _calculate_professor_availability(entries, timeslots=None, professor=None):
    """
    Calculate schedule availability and weekly calendar blocks for a professor.
    """
    return _calculate_schedule_availability(entries, timeslots=timeslots, context={'type': 'professor', 'professor': professor})



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
        sched_cols = 'prof_course_id, section, semester, major, program, day, class_start, class_end, prof_course(prof_id)'
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
        prof_entries = {}
        for r in filtered:
            pid = (r.get('prof_course') or {}).get('prof_id') or r.get('prof_id')
            if pid is not None:
                prof_count[pid] = prof_count.get(pid, 0) + 1
                prof_entries.setdefault(pid, []).append(r)

        professors = []
        if user_role == 'Viewer':
            # Find professor matching user's name (case-insensitive)
            match = supabase.table('professor').select('prof_id, first_name, last_name, department, max_hours').ilike('first_name', user_first_name).ilike('last_name', user_last_name).execute()
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
            p_max = professor.get('max_hours') or 40
            p_wl = _calculate_professor_workload(prof_entries.get(pid, []), max_hours=p_max)
            professors.append({
                'professor_id': pid,
                'professor_name': prof_name,
                'department': professor.get('department'),
                'max_hours': p_wl['max_hours_display'],
                'class_count': prof_count.get(pid, 0),
                'total_hours': p_wl['total_scheduled_hours_display'],
                'remaining_hours': p_wl['remaining_hours_display'],
                'is_overloaded': p_wl['is_overloaded'],
                'workload_pct': p_wl['workload_percentage'],
            })
        else:
            all_profs = (supabase.table('professor').select('prof_id, first_name, last_name, department, max_hours').execute().data) or []
            for p in all_profs:
                pid = p.get('prof_id')
                if pid in prof_count:
                    p_max = p.get('max_hours') or 40
                    p_wl = _calculate_professor_workload(prof_entries.get(pid, []), max_hours=p_max)
                    professors.append({
                        'professor_id': pid,
                        'professor_name': f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                        'department': p.get('department'),
                        'max_hours': p_wl['max_hours_display'],
                        'class_count': prof_count.get(pid, 0),
                        'total_hours': p_wl['total_scheduled_hours_display'],
                        'remaining_hours': p_wl['remaining_hours_display'],
                        'is_overloaded': p_wl['is_overloaded'],
                        'workload_pct': p_wl['workload_percentage'],
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
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    prof_res = supabase.table('professor').select('prof_id, first_name, last_name, department, max_hours').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])

    if not professor:
        return redirect(url_for('professor_schedule'))

    professor_name = f"{professor['first_name']} {professor['last_name']}"
    max_hours = professor.get('max_hours') or 40

    pc_res = supabase.table('prof_course').select('prof_course_id, course_id, course(course_name)').eq('prof_id', professor_id).execute()
    pc_data = pc_res.data or []
    prof_pc_ids = {item['prof_course_id'] for item in pc_data if item.get('prof_course_id')}
    pc_course_names = {}
    for item in pc_data:
        pcid = item.get('prof_course_id')
        c = _rel(item, 'course') or {}
        if pcid and c.get('course_name'):
            pc_course_names[pcid] = c.get('course_name')

    preview_pool = _get_preview_for_user()
    has_preview = bool(preview_pool)
    is_preview = (mode == 'preview') and has_preview

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('prof_course_id')
            p_pid = p_entry.get('prof_id')
            matches_prof = False
            if p_cid and p_cid in prof_pc_ids:
                matches_prof = True
            elif p_pid and str(p_pid) == str(professor_id):
                matches_prof = True

            if not matches_prof:
                continue

            sec = str(p_entry.get('section') or '')
            sem = str(p_entry.get('semester') or '')
            maj = str(p_entry.get('major') or '')
            prog = str(p_entry.get('program') or '')

            if user_role == 'admin':
                if program_filter and program_filter.lower() != 'all' and prog != program_filter:
                    continue
            else:
                if program and prog and prog != program:
                    continue

            if year_filter and not sec.startswith(str(year_filter)):
                continue
            if semester_filter and sem != semester_filter:
                continue
            if major_filter and maj != major_filter:
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            course_name = p_entry.get('course_name') or pc_course_names.get(p_cid) or 'TBA'

            entries.append({
                'schedule_id': p_entry.get('id'),
                'prof_course_id': p_cid,
                'course_name': course_name,
                'room': p_entry.get('room_name') or 'TBA',
                'day': p_entry.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'section': sec,
                'semester': sem,
                'major': maj,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'year_level': _year_of_section(sec),
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, prof_course_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'prof_course(prof_course_id, prof_id, course_id, course(course_id, course_name)), '
            'room(room_name)'
        )
        if prof_pc_ids:
            query = query.in_('prof_course_id', list(prof_pc_ids))
        else:
            query = query.eq('prof_course_id', -1)

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
        for row in rows:
            pc = _rel(row, 'prof_course') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'prof_course_id': row.get('prof_course_id'),
                'course_name': c.get('course_name') or 'TBA',
                'room': r.get('room_name') or 'TBA',
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'section': row.get('section'),
                'semester': row.get('semester'),
                'major': row.get('major'),
                'session_type': row.get('session_type') or 'Lecture',
                'year_level': _year_of_section(row.get('section')),
            })

    workload = _calculate_professor_workload(entries, max_hours=max_hours)

    sort_day = request.args.get('sort_day', 'asc').lower()
    day_order = _DAY_ORDER if sort_day != 'desc' else {d: 6 - i for i, d in enumerate(_DAY_ORDER)}
    entries.sort(key=lambda e: (day_order.get(e.get('day') or '', 99), str(e.get('start_time_raw') or '')))

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    availability = _calculate_professor_availability(entries, timeslots=timeslots, professor=professor)

    return render_template('generated_professor_schedule.html', active_page='professor_schedule',
                          professor=professor, professor_name=professor_name, entries=entries,
                          workload=workload, availability=availability,
                          is_preview=is_preview, has_preview=has_preview,
                          year_filter=year_filter, semester_filter=semester_filter,
                          major_filter=major_filter, program=program, sort_day=sort_day)


@app.route('/api/professor_availability/<int:professor_id>', methods=['GET'])
@login_required
def api_professor_availability(professor_id):
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    prof_res = supabase.table('professor').select('prof_id, first_name, last_name, department, max_hours').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])
    if not professor:
        return jsonify({'success': False, 'error': 'Professor not found.'}), 404

    prof_name = f"{professor.get('first_name','')} {professor.get('last_name','')}".strip()
    pc_res = supabase.table('prof_course').select('prof_course_id, course_id, course(course_name)').eq('prof_id', professor_id).execute()
    pc_data = pc_res.data or []
    prof_pc_ids = {item['prof_course_id'] for item in pc_data if item.get('prof_course_id')}
    pc_course_names = {item['prof_course_id']: (_rel(item, 'course') or {}).get('course_name') for item in pc_data if item.get('prof_course_id')}

    preview_pool = _get_preview_for_user()
    is_preview = (mode == 'preview') and bool(preview_pool)

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('prof_course_id')
            p_pid = p_entry.get('prof_id')
            if (p_cid and p_cid in prof_pc_ids) or (p_pid and str(p_pid) == str(professor_id)):
                st = p_entry.get('start')
                et = p_entry.get('end')
                st_fmt = _format_time(st) or str(st or '')
                et_fmt = _format_time(et) or str(et or '')
                entries.append({
                    'schedule_id': p_entry.get('id'),
                    'course_name': p_entry.get('course_name') or pc_course_names.get(p_cid) or 'TBA',
                    'room': p_entry.get('room_name') or 'TBA',
                    'room_name': p_entry.get('room_name') or 'TBA',
                    'day': p_entry.get('day'),
                    'start_time': st_fmt,
                    'end_time': et_fmt,
                    'start_time_raw': st,
                    'end_time_raw': et,
                    'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                    'section': p_entry.get('section'),
                    'session_type': p_entry.get('session_type') or 'Lecture',
                    'professor': prof_name,
                })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, prof_course_id, room_id, day, class_start, class_end, section, session_type, '
            'prof_course(prof_course_id, prof_id, course(course_name)), room(room_name)'
        )
        if prof_pc_ids:
            query = query.in_('prof_course_id', list(prof_pc_ids))
        else:
            query = query.eq('prof_course_id', -1)
        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'prof_course') or {}
            c = _rel(pc, 'course') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'course_name': c.get('course_name') or 'TBA',
                'room': r.get('room_name') or 'TBA',
                'room_name': r.get('room_name') or 'TBA',
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'section': row.get('section'),
                'session_type': row.get('session_type') or 'Lecture',
                'professor': prof_name,
            })

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    avail = _calculate_professor_availability(entries, timeslots=timeslots, professor=professor)
    return jsonify({'success': True, 'availability': avail})



@app.route('/api/professor_workload/<int:professor_id>', methods=['GET'])
@login_required
def api_professor_workload(professor_id):
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    prof_res = supabase.table('professor').select('prof_id, first_name, last_name, department, max_hours').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])
    if not professor:
        return jsonify({'error': 'Professor not found.'}), 404

    prof_name = f"{professor.get('first_name','')} {professor.get('last_name','')}".strip()
    max_hours = professor.get('max_hours') or 40

    pc_res = supabase.table('prof_course').select('prof_course_id').eq('prof_id', professor_id).execute()
    prof_pc_ids = {item['prof_course_id'] for item in (pc_res.data or []) if item.get('prof_course_id')}

    preview_pool = _get_preview_for_user()
    is_preview = (mode == 'preview') and bool(preview_pool)

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('prof_course_id')
            p_pid = p_entry.get('prof_id')
            if (p_cid and p_cid in prof_pc_ids) or (p_pid and str(p_pid) == str(professor_id)):
                entries.append(p_entry)
    else:
        query = supabase.table('schedule').select('class_start, class_end, day, prof_course_id')
        if prof_pc_ids:
            query = query.in_('prof_course_id', list(prof_pc_ids))
        else:
            query = query.eq('prof_course_id', -1)
        entries = query.execute().data or []

    workload = _calculate_professor_workload(entries, max_hours=max_hours)
    return jsonify({
        'professor_id': professor_id,
        'professor_name': prof_name,
        'is_preview': is_preview,
        **workload
    })


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
        sched_cols = 'prof_course_id, room_id, section, semester, major, program, prof_course(prof_id)'
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
            prof_rooms = [r for r in filtered if ((r.get('prof_course') or {}).get('prof_id') or r.get('prof_id')) == prof_id]
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
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    room_res = supabase.table('room').select('room_id, room_name, room_type').eq('room_id', room_id).execute()
    room = _first(room_res.data or [])

    if not room:
        return redirect(url_for('room_schedule'))

    preview_pool = _get_preview_for_user()
    has_preview = bool(preview_pool)
    is_preview = (mode == 'preview') and has_preview

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_rid = p_entry.get('room_id')
            if p_rid is None or str(p_rid) != str(room_id):
                continue

            sec = str(p_entry.get('section') or '')
            sem = str(p_entry.get('semester') or '')
            maj = str(p_entry.get('major') or '')
            prog = str(p_entry.get('program') or '')

            if user_role == 'admin':
                if program_filter and program_filter.lower() != 'all' and prog != program_filter:
                    continue
            else:
                if program and prog and prog != program:
                    continue

            if year_filter and not sec.startswith(str(year_filter)):
                continue
            if semester_filter and sem != semester_filter:
                continue
            if major_filter and maj != major_filter:
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            entries.append({
                'schedule_id': p_entry.get('id'),
                'course_name': p_entry.get('course_name') or 'TBA',
                'professor': p_entry.get('professor_name') or 'TBA',
                'day': p_entry.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'section': p_entry.get('section'),
                'semester': sem,
                'major': maj,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'year_level': _year_of_section(p_entry.get('section')),
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, prof_course_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'prof_course(prof_course_id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
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
        for row in rows:
            pc = _rel(row, 'prof_course') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'course_name': c.get('course_name'),
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or None,
                'day': row['day'],
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'section': row['section'],
                'semester': row['semester'],
                'major': row['major'],
                'session_type': row['session_type'],
                'year_level': _year_of_section(row.get('section')),
            })

    sort_day = request.args.get('sort_day', 'asc').lower()
    day_order = _DAY_ORDER if sort_day != 'desc' else {d: 6 - i for i, d in enumerate(_DAY_ORDER)}
    entries.sort(key=lambda e: (day_order.get(e.get('day') or '', 99), str(e.get('start_time_raw') or '')))

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    availability = _calculate_room_availability(entries, timeslots=timeslots, room=room)

    return render_template('generated_room_schedule.html', active_page='room_schedule',
                          room=room, entries=entries,
                          availability=availability,
                          is_preview=is_preview,
                          has_preview=has_preview,
                          year_filter=year_filter, semester_filter=semester_filter,
                          major_filter=major_filter, program=program, sort_day=sort_day)


@app.route('/api/room_availability/<room_id>', methods=['GET'])
@login_required
def api_room_availability(room_id):
    try:
        mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
        room_res = supabase.table('room').select('room_id, room_name, room_type').eq('room_id', room_id).execute()
        room = _first(room_res.data or [])
        if not room:
            return jsonify({'success': False, 'error': 'Room not found.'}), 404

        preview_pool = _get_preview_for_user()
        has_preview = bool(preview_pool)
        is_preview = (mode == 'preview') and has_preview

        entries = []
        if is_preview:
            for p_entry in preview_pool:
                p_rid = p_entry.get('room_id')
                if p_rid is not None and str(p_rid) == str(room_id):
                    st = p_entry.get('start')
                    et = p_entry.get('end')
                    entries.append({
                        'schedule_id': p_entry.get('id'),
                        'course_name': p_entry.get('course_name') or 'TBA',
                        'professor': p_entry.get('professor_name') or 'TBA',
                        'day': p_entry.get('day'),
                        'start_time': _format_time(st),
                        'end_time': _format_time(et),
                        'start_time_raw': st,
                        'end_time_raw': et,
                        'section': p_entry.get('section'),
                        'session_type': p_entry.get('session_type') or 'Lecture',
                    })
        else:
            rows = (supabase.table('schedule').select(
                'schedule_id, prof_course_id, room_id, day, class_start, class_end, section, session_type, '
                'prof_course(course(course_name), professor(first_name, last_name))'
            ).eq('room_id', room_id).execute().data) or []

            for row in rows:
                pc = _rel(row, 'prof_course') or {}
                c = _rel(pc, 'course') or _rel(row, 'course') or {}
                p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
                st_raw = row.get('class_start')
                et_raw = row.get('class_end')
                entries.append({
                    'schedule_id': row.get('schedule_id'),
                    'course_name': c.get('course_name'),
                    'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or 'TBA',
                    'day': row.get('day'),
                    'start_time': _format_time(st_raw),
                    'end_time': _format_time(et_raw),
                    'start_time_raw': st_raw,
                    'end_time_raw': et_raw,
                    'section': row.get('section'),
                    'session_type': row.get('session_type') or 'Lecture',
                })

        try:
            timeslots = (supabase.table('timeslot').select('*').execute().data) or []
        except Exception:
            timeslots = []

        availability = _calculate_room_availability(entries, timeslots=timeslots, room=room)
        return jsonify({
            'success': True,
            'room_id': room_id,
            'room_name': room.get('room_name'),
            'room_type': room.get('room_type'),
            'is_preview': is_preview,
            'availability': availability,
        })
    except Exception as err:
        logging.exception(f"Error in api_room_availability: {err}")
        return jsonify({'success': False, 'error': str(err)}), 500


@app.route('/schedules')
@login_required
def schedules():
    year_filter = (request.args.get('year') or '').strip()
    semester_filter = (request.args.get('semester') or '').strip()
    major_filter = (request.args.get('major') or '').strip()
    program_filter = (request.args.get('program') or '').strip()

    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')
    user_first_name = (session.get('first_name') or '').strip()
    user_last_name = (session.get('last_name') or '').strip()

    try:
        sched_cols = (
            'schedule_id, prof_course_id, section, semester, major, program, day, class_start, class_end, session_type, room_id, '
            'prof_course(prof_course_id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
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

        # Default to active semester from session or the latest confirmed semester if not specified
        if not semester_filter and semester_options:
            active_sem = session.get('active_semester', '')
            if active_sem in semester_options:
                semester_filter = active_sem
            else:
                semester_filter = semester_options[-1]

        def _matches(r):
            if year_filter and _year_of_section(r.get('section')) != year_filter:
                return False
            if semester_filter and semester_filter.lower() != 'all' and r.get('semester') != semester_filter:
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
                r_pid = (r.get('prof_course') or {}).get('prof_id') or r.get('prof_id')
                if r_pid == prof_id and _matches(r):
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
                pc = _rel(r, 'prof_course') or {}
                c = _rel(pc, 'course') or _rel(r, 'course') or {}
                rm = _rel(r, 'room') or {}
                p = _rel(pc, 'professor') or _rel(r, 'professor') or {}
                fname = p.get('first_name') or ''
                lname = p.get('last_name') or ''
                prof_name = f"{fname} {lname}".strip() or 'TBA'
                start_fmt = _format_time(r.get('class_start'))
                end_fmt = _format_time(r.get('class_end'))
                sections_by_key[key]['entries'].append({
                    'id': r.get('schedule_id'),
                    'schedule_id': r.get('schedule_id'),
                    'prof_course_id': r.get('prof_course_id'),
                    'course_id': pc.get('course_id') or r.get('course_id'),
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
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    preview_pool = _get_preview_for_user()
    has_preview = bool(preview_pool)
    is_preview = (mode == 'preview') and has_preview

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            if str(p_entry.get('section') or '').strip() != str(section_name).strip():
                continue
            sem = str(p_entry.get('semester') or '')
            maj = str(p_entry.get('major') or '')
            prog = str(p_entry.get('program') or '')

            if user_role == 'Viewer' and program and prog and prog != program:
                continue

            if semester_filter and sem != semester_filter:
                continue
            if major_filter and maj != major_filter:
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            prof_name = p_entry.get('professor_name') or 'TBA'

            entries.append({
                'schedule_id': p_entry.get('id'),
                'prof_course_id': p_entry.get('prof_course_id'),
                'course_id': p_entry.get('course_id'),
                'prof_id': p_entry.get('prof_id'),
                'day': p_entry.get('day'),
                'class_start': st,
                'class_end': et,
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'semester': sem,
                'major': maj,
                'course_name': p_entry.get('course_name') or 'TBA',
                'room_name': p_entry.get('room_name') or 'TBA',
                'first_name': '',
                'last_name': '',
                'professor': prof_name,
                'professor_name': prof_name,
                'section': section_name,
                'year_level': _year_of_section(section_name),
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'timeslot_display': f"{p_entry.get('day')} | {st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, day, class_start, class_end, session_type, semester, major, prof_course_id, room_id, '
            'prof_course(prof_course_id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        ).eq('section', section_name)

        if user_role == 'Viewer' and program:
            query = query.or_(f'program.eq.{program},program.is.null,program.eq.')

        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.or_(f'major.eq.{major_filter},major.is.null')

        rows = query.execute().data or []

        for row in rows:
            pc = _rel(row, 'prof_course') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            r = _rel(row, 'room') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            first_name = p.get('first_name') or ''
            last_name = p.get('last_name') or ''
            prof_name = f"{first_name} {last_name}".strip() or 'TBA'
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entry = {
                'schedule_id': row['schedule_id'],
                'prof_course_id': row.get('prof_course_id'),
                'course_id': pc.get('course_id') or row.get('course_id'),
                'prof_id': pc.get('prof_id') or row.get('prof_id'),
                'day': row['day'],
                'class_start': st_raw,
                'class_end': et_raw,
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'session_type': row['session_type'],
                'semester': row['semester'],
                'major': row['major'],
                'course_name': c.get('course_name') or 'TBA',
                'room_name': r.get('room_name') or 'TBA',
                'room_id': row.get('room_id'),
                'first_name': first_name,
                'last_name': last_name,
                'professor': prof_name,
                'professor_name': prof_name,
                'section': section_name,
                'year_level': _year_of_section(section_name),
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'timeslot_display': f"{row['day']} | {st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
            }
            entries.append(entry)

    sort_day = request.args.get('sort_day', 'asc').lower()
    day_order = _DAY_ORDER if sort_day != 'desc' else {d: 6 - i for i, d in enumerate(_DAY_ORDER)}
    entries.sort(key=lambda e: (day_order.get(e.get('day') or '', 99), str(e.get('start_time_raw') or '')))

    section = {
        'section': section_name,
        'section_name': section_name,
        'year_level': _year_of_section(section_name),
        'semester': semester_filter or (entries[0].get('semester', '') if entries else ''),
        'major': major_filter or (entries[0].get('major') if entries else None)
    }

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    availability = _calculate_section_availability(entries, timeslots=timeslots, section=section)

    dept = _get_department()
    r_query = supabase.table('room').select('room_id, room_name, room_type')
    if dept:
        rooms = (r_query.eq('department', dept).execute().data) or []
        if not rooms:
            rooms = (supabase.table('room').select('room_id, room_name, room_type').execute().data) or []
    else:
        rooms = (r_query.execute().data) or []

    pc_query = supabase.table('prof_course').select(
        'prof_course_id, course_id, prof_id, '
        'course(course_id, course_name, program), '
        'professor(prof_id, first_name, last_name, department)'
    )
    if dept:
        pc_rows = (pc_query.eq('professor.department', dept).execute().data) or []
        if not pc_rows:
            pc_rows = (supabase.table('prof_course').select(
                'prof_course_id, course_id, prof_id, '
                'course(course_id, course_name, program), '
                'professor(prof_id, first_name, last_name, department)'
            ).execute().data) or []
    else:
        pc_rows = (pc_query.execute().data) or []

    all_prof_courses = []
    for pc_item in pc_rows:
        pcid = pc_item.get('prof_course_id')
        p_obj = _rel(pc_item, 'professor') or {}
        c_obj = _rel(pc_item, 'course') or {}
        p_name = f"{p_obj.get('first_name') or ''} {p_obj.get('last_name') or ''}".strip() or 'TBA'
        c_name = c_obj.get('course_name') or f"Course #{pc_item.get('course_id')}"
        all_prof_courses.append({
            'prof_course_id': pcid,
            'prof_id': pc_item.get('prof_id'),
            'course_id': pc_item.get('course_id'),
            'label': f"{p_name} - {c_name}",
            'prof_name': p_name,
            'course_name': c_name,
        })
    all_prof_courses.sort(key=lambda x: x['label'])

    return render_template(
        'generated_schedule.html',
        active_page='schedules',
        section=section,
        entries=entries,
        sections_with_entries=[{'section': section, 'entries': entries}],
        availability=availability,
        is_preview=is_preview,
        has_preview=has_preview,
        year_filter=section.get('year_level'),
        semester_filter=semester_filter,
        major_filter=major_filter,
        sort_day=sort_day,
        rooms=rooms,
        all_prof_courses=all_prof_courses
    )


@app.route('/api/section_availability/<section_name>', methods=['GET'])
@login_required
def api_section_availability(section_name):
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    preview_pool = _get_preview_for_user()
    is_preview = (mode == 'preview') and bool(preview_pool)

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            if str(p_entry.get('section') or '').strip() == str(section_name).strip():
                st = p_entry.get('start')
                et = p_entry.get('end')
                st_fmt = _format_time(st) or str(st or '')
                et_fmt = _format_time(et) or str(et or '')
                entries.append({
                    'schedule_id': p_entry.get('id'),
                    'course_name': p_entry.get('course_name') or 'TBA',
                    'room_name': p_entry.get('room_name') or 'TBA',
                    'day': p_entry.get('day'),
                    'start_time': st_fmt,
                    'end_time': et_fmt,
                    'start_time_raw': st,
                    'end_time_raw': et,
                    'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                    'section': section_name,
                    'session_type': p_entry.get('session_type') or 'Lecture',
                    'professor': p_entry.get('professor_name') or 'TBA',
                })
    else:
        rows = (supabase.table('schedule').select(
            'schedule_id, day, class_start, class_end, session_type, prof_course(course(course_name), professor(first_name, last_name)), room(room_name)'
        ).eq('section', section_name).execute().data) or []
        for row in rows:
            pc = _rel(row, 'prof_course') or {}
            c = _rel(pc, 'course') or {}
            r = _rel(row, 'room') or {}
            p = _rel(pc, 'professor') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'course_name': c.get('course_name') or 'TBA',
                'room_name': r.get('room_name') or 'TBA',
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else 'TBA',
                'section': section_name,
                'session_type': row.get('session_type') or 'Lecture',
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or 'TBA',
            })

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    avail = _calculate_section_availability(entries, timeslots=timeslots, section=section_name)
    return jsonify({'success': True, 'availability': avail})



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

    custom_section = (payload.get('section') or '').strip()
    if not year_level and not custom_section:
        return jsonify({'success': False, 'message': 'Please select a year level or enter a section name.'}), 400

    new_section_name = custom_section or _build_section_name_for_year(section_name, year_level)

    try:
        query = supabase.table('schedule').update({
            'section': new_section_name,
            'semester': semester,
            'major': major,
        }).eq('section', section_name)
        if program and session.get('role') == 'Viewer':
            query = query.or_(f'program.eq.{program},program.is.null,program.eq.')
        query.execute()
        log_activity('edit', 'schedule', f'Updated section {section_name} to {new_section_name}')
        flash('Updated successfully', 'success')
    except Exception as err:
        logging.exception(f"Error in edit_schedule: {err}")
        return jsonify({'success': False, 'message': f'Something went wrong: {str(err)}'}), 500

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
    prof_course_id = payload.get('prof_course_id')
    day = payload.get('day')
    start = payload.get('start')
    end = payload.get('end')
    room_id = payload.get('room_id')
    program = session.get('program', '')
    user_role = session.get('role', 'Viewer')

    if not course_name and not professor_name and not room_name and not timeslot and not session_type and not prof_course_id and not day and not start and not end and not room_id:
        return jsonify({'success': False, 'message': 'Please complete at least one field.'}), 400

    try:
        query = supabase.table('schedule').select(
            'schedule_id, prof_course_id, room_id, day, class_start, class_end, session_type, section, semester, major, program, '
            'prof_course(prof_course_id, prof_id, course_id, course(course_name), professor(first_name, last_name))'
        ).eq('schedule_id', schedule_id)
        if user_role == 'Viewer' and program:
            query = query.or_(f'program.eq.{program},program.is.null,program.eq.')

        res = query.execute()
        existing = _first(res.data or [])

        if not existing:
            return jsonify({'success': False, 'message': 'Schedule entry not found.'}), 404

        existing_pc = _rel(existing, 'prof_course') or {}
        existing_course_id = existing_pc.get('course_id')
        existing_prof_id = existing_pc.get('prof_id')

        target_prof_course_id = None

        if prof_course_id:
            try:
                target_prof_course_id = int(prof_course_id)
            except (ValueError, TypeError):
                return jsonify({'success': False, 'message': 'Invalid professor-course selection.'}), 400
        elif course_name or professor_name:
            target_course_id = existing_course_id
            if course_name:
                c_query = supabase.table('course').select('course_id').eq('course_name', course_name)
                if program:
                    c_res = c_query.eq('program', program).limit(1).execute()
                    c_row = _first(c_res.data or [])
                    if not c_row:
                        c_res = supabase.table('course').select('course_id').eq('course_name', course_name).limit(1).execute()
                        c_row = _first(c_res.data or [])
                else:
                    c_res = c_query.limit(1).execute()
                    c_row = _first(c_res.data or [])
                if c_row:
                    target_course_id = c_row['course_id']

            target_prof_id = existing_prof_id
            if professor_name:
                if professor_name.upper() in ('TBA', 'NONE', 'N/A'):
                    target_prof_id = None
                else:
                    dept = _get_department()
                    p_query = supabase.table('professor').select('prof_id, first_name, last_name')
                    if dept:
                        p_res = p_query.eq('department', dept).execute()
                    else:
                        p_res = p_query.execute()
                    for p in (p_res.data or []):
                        full = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
                        if full.lower() == professor_name.lower():
                            target_prof_id = p['prof_id']
                            break

            if target_course_id and target_prof_id:
                pc_match = supabase.table('prof_course').select('prof_course_id').eq('prof_id', target_prof_id).eq('course_id', target_course_id).limit(1).execute()
                pc_row = _first(pc_match.data or [])
                if pc_row:
                    target_prof_course_id = pc_row['prof_course_id']
                else:
                    new_pc = supabase.table('prof_course').insert({'prof_id': target_prof_id, 'course_id': target_course_id}).execute()
                    if new_pc.data:
                        target_prof_course_id = new_pc.data[0]['prof_course_id']
            elif not target_prof_id:
                target_prof_course_id = None
            else:
                target_prof_course_id = existing.get('prof_course_id')
        else:
            target_prof_course_id = existing.get('prof_course_id')

        target_room_id = existing.get('room_id')
        if room_id:
            try:
                target_room_id = int(room_id)
            except (ValueError, TypeError):
                pass
        elif room_name:
            if room_name.upper() in ('TBA', 'NONE', 'N/A'):
                target_room_id = None
            else:
                dept = _get_department()
                r_query = supabase.table('room').select('room_id, room_name, room_type')
                if dept:
                    r_res = r_query.eq('room_name', room_name).eq('department', dept).limit(1).execute()
                    r_row = _first(r_res.data or [])
                    if not r_row:
                        r_res = supabase.table('room').select('room_id, room_name, room_type').eq('room_name', room_name).limit(1).execute()
                        r_row = _first(r_res.data or [])
                else:
                    r_res = r_query.eq('room_name', room_name).limit(1).execute()
                    r_row = _first(r_res.data or [])
                if r_row:
                    target_room_id = r_row['room_id']

        target_day = day or existing.get('day')
        target_start = start or existing.get('class_start')
        target_end = end or existing.get('class_end')

        if timeslot and '|' in timeslot:
            parts = timeslot.split('|', 1)
            d_part = parts[0].strip()
            t_part = parts[1].strip()
            if d_part in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
                target_day = d_part
            if '-' in t_part:
                sp, ep = t_part.split('-', 1)
                st = _parse_time(sp.strip())
                et = _parse_time(ep.strip())
                if st: target_start = str(st)
                if et: target_end = str(et)

        # Lunch Break Conflict check: prevent scheduling during system lunch break
        if target_start and target_end:
            st_td = _parse_time(target_start)
            et_td = _parse_time(target_end)
            if st_td and et_td:
                try:
                    ts_res = supabase.table('timeslot').select('lunch_time').execute()
                    ts_rows = ts_res.data or []
                except Exception:
                    ts_rows = []
                conf_lunch_td = None
                for tr in ts_rows:
                    parsed_lt = _parse_time(tr.get('lunch_time'))
                    if parsed_lt is not None:
                        conf_lunch_td = parsed_lt
                        break
                if conf_lunch_td is None:
                    conf_lunch_td = timedelta(hours=12)

                if _is_blocked_by_lunch(st_td, et_td, conf_lunch_td):
                    lunch_str = f"{_format_time(conf_lunch_td)} - {_format_time(conf_lunch_td + timedelta(hours=1))}"
                    return jsonify({
                        'success': False,
                        'message': f"Lunch break conflict: Classes cannot be scheduled during the designated lunch break ({lunch_str})."
                    }), 400

        update_payload = {
            'prof_course_id': target_prof_course_id,
            'room_id': target_room_id,
            'session_type': session_type or existing.get('session_type') or 'Lecture',
        }
        if target_day:
            update_payload['day'] = target_day
        if target_start:
            update_payload['class_start'] = target_start
        if target_end:
            update_payload['class_end'] = target_end

        supabase.table('schedule').update(update_payload).eq('schedule_id', schedule_id).execute()
        flash('Edited successfully', 'success')
        return jsonify({'success': True, 'message': 'Schedule entry updated successfully.'})

    except Exception as err:
        logging.exception(f"Error in edit_schedule_entry: {err}")
        return jsonify({'success': False, 'message': f'Error updating schedule: {str(err)}'}), 500

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
            pc_ids = [r['prof_course_id'] for r in (supabase.table('prof_course').select('prof_course_id').eq('course_id', item_id).execute().data or [])]
            if pc_ids:
                try:
                    supabase.table('schedule').delete().in_('prof_course_id', pc_ids).execute()
                except Exception:
                    pass
            try:
                supabase.table('schedule').delete().eq('course_id', item_id).execute()
            except Exception:
                pass
            supabase.table('prof_course').delete().eq('course_id', item_id).execute()
            supabase.table('course').delete().eq('course_id', item_id).execute()
        elif item_type == 'professor':
            pc_ids = [r['prof_course_id'] for r in (supabase.table('prof_course').select('prof_course_id').eq('prof_id', item_id).execute().data or [])]
            if pc_ids:
                try:
                    supabase.table('schedule').delete().in_('prof_course_id', pc_ids).execute()
                except Exception:
                    pass
            try:
                supabase.table('schedule').delete().eq('prof_id', item_id).execute()
            except Exception:
                pass
            supabase.table('prof_course').delete().eq('prof_id', item_id).execute()
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
def irregular_students():
    abort(404)
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
def delete_irregular_student(student_id):
    abort(404)
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
def manage_irregular_student_schedule(student_id):
    abort(404)
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
        'schedule_id, section, day, class_start, class_end, room_id, prof_course_id, session_type, '
        'prof_course(prof_course_id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
        'course(course_name), room(room_name), professor(first_name, last_name)'
    ).eq('program', student['program'])
    if semester_filter:
        sched_query = sched_query.eq('semester', semester_filter)
    sched_rows = sched_query.execute().data or []

    all_schedule_entries = []
    for row in sched_rows:
        pc = _rel(row, 'prof_course') or {}
        c = _rel(pc, 'course') or _rel(row, 'course') or {}
        r = _rel(row, 'room') or {}
        p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''
        all_schedule_entries.append({
            'schedule_id': row.get('schedule_id'),
            'prof_course_id': row.get('prof_course_id'),
            'course_id': pc.get('course_id') or row.get('course_id'),
            'section': row.get('section'),
            'day': row.get('day'),
            'class_start': _format_time(row.get('class_start')) or str(row.get('class_start') or ''),
            'class_end': _format_time(row.get('class_end')) or str(row.get('class_end') or ''),
            'class_start_raw': row.get('class_start'),
            'class_end_raw': row.get('class_end'),
            'room_id': row.get('room_id'),
            'room': r.get('room_name'),
            'prof_id': pc.get('prof_id') or row.get('prof_id'),
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
            course_sections[c_id][sec] = []
        course_sections[c_id][sec].append(entry)

    # Current assignments for this student
    assigned_rows = (supabase.table('irregular_student_schedule').select(
        'id, student_id, schedule_id, course_id, section, created_at, '
        'schedule(day, class_start, class_end, session_type, prof_course_id, prof_course(course_id, prof_id, course(course_name), professor(first_name, last_name)), course(course_name), room(room_name), professor(first_name, last_name))'
    ).eq('student_id', student_id).execute().data) or []

    assigned_by_course = {}
    total_assigned_units = 0
    assigned_course_ids = set()

    for row in assigned_rows:
        cid = row.get('course_id')
        assigned_course_ids.add(cid)
        sch = _rel(row, 'schedule') or {}
        pc = _rel(sch, 'prof_course') or {}
        c = _rel(pc, 'course') or _rel(sch, 'course') or {}
        rm = _rel(sch, 'room') or {}
        p = _rel(pc, 'professor') or _rel(sch, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''

        entry_data = {
            'assignment_id': row.get('id'),
            'schedule_id': row.get('schedule_id'),
            'course_id': cid,
            'section': row.get('section'),
            'day': sch.get('day'),
            'class_start': _format_time(sch.get('class_start')) or str(sch.get('class_start') or ''),
            'class_end': _format_time(sch.get('class_end')) or str(sch.get('class_end') or ''),
            'room': rm.get('room_name'),
            'professor': f"{pf} {pl}".strip() or None,
            'course_name': c.get('course_name'),
            'session_type': sch.get('session_type'),
        }

        if cid not in assigned_by_course:
            assigned_by_course[cid] = {
                'section': row.get('section'),
                'entries': [],
            }
        assigned_by_course[cid]['entries'].append(entry_data)

    # Sum units for distinct assigned courses
    for c in program_courses:
        if c['course_id'] in assigned_course_ids:
            try:
                total_assigned_units += float(c.get('units') or 0)
            except (ValueError, TypeError):
                pass

    assigned_entries = []
    for cid, grp in assigned_by_course.items():
        assigned_entries.extend(grp['entries'])

    return render_template(
        'manage_irregular_schedule.html',
        active_page='irregular_students',
        student=student,
        courses_by_year=courses_by_year,
        course_sections=course_sections,
        assigned_by_course=assigned_by_course,
        assigned_entries=assigned_entries,
        assigned_course_ids=list(assigned_course_ids),
        total_assigned_units=total_assigned_units,
        semester_filter=semester_filter,
        year_filter=year_filter,
    )


@app.route('/irregular_students/<int:student_id>/view_schedule')
def view_irregular_student_schedule(student_id):
    abort(404)
    student = _first((supabase.table('irregular_students').select('*').eq('student_id', student_id).execute().data) or [])
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('irregular_students'))

    assigned_rows = (supabase.table('irregular_student_schedule').select(
        'id, student_id, schedule_id, course_id, section, '
        'schedule(day, class_start, class_end, session_type, prof_course_id, prof_course(course_id, prof_id, course(course_name), professor(first_name, last_name)), course(course_name), room(room_name), professor(first_name, last_name))'
    ).eq('student_id', student_id).execute().data) or []

    entries = []
    for row in assigned_rows:
        sch = _rel(row, 'schedule') or {}
        pc = _rel(sch, 'prof_course') or {}
        c = _rel(pc, 'course') or _rel(sch, 'course') or {}
        rm = _rel(sch, 'room') or {}
        p = _rel(pc, 'professor') or _rel(sch, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''
        s_fmt = _format_time(sch.get('class_start')) or str(sch.get('class_start') or '')
        e_fmt = _format_time(sch.get('class_end')) or str(sch.get('class_end') or '')
        entries.append({
            'course_name': c.get('course_name') or f"Course #{row.get('course_id')}",
            'section': row.get('section'),
            'day': sch.get('day') or '',
            'start_time': s_fmt,
            'end_time': e_fmt,
            'time_range': f"{sch.get('day')} | {s_fmt} - {e_fmt}" if sch.get('day') and s_fmt else 'TBA',
            'room': rm.get('room_name') or 'TBA',
            'professor': f"{pf} {pl}".strip() or 'TBA',
            'session_type': sch.get('session_type') or 'Lecture',
        })

    entries.sort(key=lambda e: (_DAY_ORDER.get(e.get('day') or '', 99), e.get('start_time') or ''))

    return render_template(
        'view_irregular_schedule.html',
        active_page='irregular_students',
        student=student,
        entries=entries,
    )


@app.route('/irregular_students/<int:student_id>/assign_section', methods=['POST'])
def assign_irregular_section(student_id):
    abort(404)
    data = request.get_json(silent=True) or request.form
    course_id = data.get('course_id')
    section = data.get('section')

    if not course_id or not section:
        return jsonify({'success': False, 'message': 'Course and section are required.'}), 400

    pc_res = supabase.table('prof_course').select('prof_course_id').eq('course_id', course_id).execute()
    c_pc_ids = [item['prof_course_id'] for item in (pc_res.data or []) if item.get('prof_course_id')]
    if c_pc_ids:
        res = supabase.table('schedule').select('*, prof_course(course_id, course(course_name))').in_('prof_course_id', c_pc_ids).eq('section', section).execute()
    else:
        res = None
    new_entries = []
    for row in (res.data or []):
        pc = _rel(row, 'prof_course') or {}
        crs = _rel(pc, 'course') or _rel(row, 'course') or {}
        row['course_name'] = crs.get('course_name')
        row['course_id'] = pc.get('course_id') or row.get('course_id') or course_id
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
def unassign_irregular_section(student_id, course_id):
    abort(404)
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
            elif standard_semester == '1st Semester' and y == 4:
                raw_db = (request.form.get('sections_major[4][database]') or
                          request.form.get('sections[4][database]') or
                          request.form.get('sections_4y_database') or
                          request.form.get('sections_4_database') or
                          request.form.get('database_sections_4'))
                raw_web = (request.form.get('sections_major[4][web]') or
                           request.form.get('sections[4][web]') or
                           request.form.get('sections_4y_web') or
                           request.form.get('sections_4_web') or
                           request.form.get('web_sections_4'))
                raw_net = (request.form.get('sections_major[4][networking]') or
                           request.form.get('sections[4][networking]') or
                           request.form.get('sections_4y_networking') or
                           request.form.get('sections_4_networking') or
                           request.form.get('networking_sections_4'))

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
            pc_res = supabase.table('prof_course').select('prof_course_id, course_id, prof_id, professor(first_name, last_name, max_hours, department)').eq('professor.department', department).execute()
            all_profs_res = supabase.table('professor').select('prof_id, first_name, last_name, max_hours, department').eq('department', department).execute()
        else:
            pc_res = supabase.table('prof_course').select('prof_course_id, course_id, prof_id, professor(first_name, last_name, max_hours, department)').execute()
            all_profs_res = supabase.table('professor').select('prof_id, first_name, last_name, max_hours, department').execute()

        pc_data = pc_res.data or []
        prof_course_map = {}
        professors_by_course = {}
        for row in pc_data:
            pcid = row.get('prof_course_id')
            cid = row.get('course_id')
            pid = row.get('prof_id')
            if pid and cid and pcid:
                prof_course_map[(pid, cid)] = pcid
            p = _rel(row, 'professor')
            if p:
                professors_by_course.setdefault(cid, []).append({
                    'prof_course_id': pcid,
                    'course_id': cid,
                    'prof_id': pid,
                    'first_name': p.get('first_name'),
                    'last_name': p.get('last_name'),
                    'max_hours': int(p.get('max_hours') or 40),
                })

        all_prof_data = all_profs_res.data or []
        all_professors_pool = []
        for p in all_prof_data:
            all_professors_pool.append({
                'prof_id': p.get('prof_id'),
                'first_name': p.get('first_name'),
                'last_name': p.get('last_name'),
                'max_hours': int(p.get('max_hours') or 40),
                'department': p.get('department'),
            })

        # Fetch rooms
        if is_viewer and department:
            rooms_res = supabase.table('room').select('*').eq('department', department).execute()
        else:
            rooms_res = supabase.table('room').select('*').execute()
        all_rooms = rooms_res.data or []
        lecture_rooms = [r for r in all_rooms if _is_lecture_room_type(r.get('room_type'))]
        lab_rooms = [r for r in all_rooms if _is_lab_room_type(r.get('room_type'))]
        logging.info(f"[ROOM POOL] Total: {len(all_rooms)} | Lecture Rooms: {len(lecture_rooms)} | Laboratory Rooms: {len(lab_rooms)}")

        # Timeslots & candidate slots
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
        timeslots.sort(key=lambda t: str(t.get('start_time') or ''))
        candidate_slots = _build_candidate_slots(timeslots)

        day_order = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6}
        slot_groups = {}
        for slot in candidate_slots:
            slot_groups.setdefault(slot['day'], []).append(slot)

        # Global conflict tracking and room utilization tracking across all sections in batch
        section_bookings = {(sec['section'], sec.get('major')): [] for sec in all_sections}
        room_bookings = {}
        room_usage = {r['room_id']: 0 for r in all_rooms if r.get('room_id') is not None}
        room_last_used = {r['room_id']: 0 for r in all_rooms if r.get('room_id') is not None}
        room_order = {r['room_id']: idx for idx, r in enumerate(all_rooms) if r.get('room_id') is not None}
        assignment_step = 0
        professor_bookings = {}
        professor_hours = {}
        prof_day_hours = {}
        prof_course_count = {}
        prof_section_count = {}
        preview_entries = []

        # Load existing bookings from DB to avoid collision across different programs in the SAME semester
        existing_rows = (supabase.table('schedule').select('section, room_id, day, class_start, class_end, prof_course_id, prof_course(prof_id), semester, program, major').execute().data) or []
        for existing in existing_rows:
            # ONLY consider rows for the EXACT same semester that belong to a DIFFERENT program
            # (different semesters do not run simultaneously, so they must NOT block rooms or professors)
            if existing.get('semester') != standard_semester:
                continue
            if not program or existing.get('program') == program or (not existing.get('program') and not program):
                continue
            sec_n = existing.get('section')
            sec_m = existing.get('major')
            rid = existing.get('room_id')
            p_pc = _rel(existing, 'prof_course') or {}
            p_id = p_pc.get('prof_id') or existing.get('prof_id')
            d = existing.get('day')
            st = existing.get('class_start')
            et = existing.get('class_end')
            if sec_n:
                section_bookings.setdefault((sec_n, sec_m), []).append((d, st, et))
            if rid is not None:
                room_bookings.setdefault(rid, []).append((d, st, et))
            if p_id is not None:
                professor_bookings.setdefault(p_id, []).append((d, st, et))
                try:
                    dur_h = max(0.0, (_to_seconds(et) - _to_seconds(st)) / 3600.0)
                except Exception:
                    dur_h = 1.0
                professor_hours[p_id] = professor_hours.get(p_id, 0.0) + dur_h
                if d:
                    prof_day_hours[(p_id, d)] = prof_day_hours.get((p_id, d), 0.0) + dur_h

        total_sessions_required = 0
        total_sessions_scheduled = 0
        prof_tba_count = 0
        room_tba_count = 0

        # Workload-aware scoring for candidate professors
        def _score_candidate_professor(prof, course_id, duration, day, primary_prof_ids, tolerance=2.0, allow_overload=False):
            pk = prof['prof_id']
            curr_h = professor_hours.get(pk, 0.0)
            max_h = prof.get('max_hours') or 40
            day_h = prof_day_hours.get((pk, day), 0.0)
            course_sec = prof_course_count.get((pk, course_id), 0)
            is_primary = 1 if pk in primary_prof_ids else 0
            exceeds_cap = 1 if (curr_h + duration > max_h) else 0
            overload_amount = max(0.0, (curr_h + duration) - max_h)
            tier = int(curr_h // tolerance)
            utilization = (curr_h / max_h) if max_h > 0 else 1.0
            sec_count = prof_section_count.get(pk, 0)

            return (
                0 if is_primary else 1,                   # 1. Primary prof_course qualification first
                exceeds_cap,                              # 2. Within max_hours cap (0) before exceeding (1)
                overload_amount if allow_overload else 0, # 3. Minimize overload if fallback allowed
                tier,                                     # 4. Workload tier (±2h tolerance grouping)
                course_sec,                               # 5. Course rotation (avoid repeatedly assigning same prof)
                day_h,                                    # 6. Daily spreading (avoid clustering on one day)
                curr_h,                                   # 7. Exact current hours
                utilization,                              # 8. Capacity utilization ratio
                sec_count,                                # 9. Total sections count
                pk                                        # 10. Deterministic tie-break
            )

        # Helper to schedule a single unpaired session (or half of a split paired session)
        def _schedule_single_session(session_type, duration, course, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
            nonlocal total_sessions_scheduled, prof_tba_count, room_tba_count, assignment_step
            if duration <= 0:
                return True

            course_id = course['course_id']
            primary_profs = professors_by_course.get(course_id, [])
            primary_prof_ids = {p['prof_id'] for p in primary_profs}
            other_profs = [p for p in all_professors_pool if p['prof_id'] not in primary_prof_ids]

            # STRICT room filtering: Lecture courses -> ONLY lecture rooms; Lab courses -> ONLY lab rooms
            cand_rooms = lecture_rooms if session_type == 'Lecture' else lab_rooms
            logging.debug(f"[ROOM_FILTER] Course '{course.get('course_name')}' ({session_type}) filtered to {len(cand_rooms)} matching rooms.")

            # Passes: from most constrained & preferred to broadest fallback (relax faculty & rules, NEVER room type)
            passes = [
                {'strict_rules': True,  'prof_pool': 'primary'},
                {'strict_rules': True,  'prof_pool': 'all'},
                {'strict_rules': False, 'prof_pool': 'primary'},
                {'strict_rules': False, 'prof_pool': 'all'},
            ]

            for p_config in passes:
                prof_pool = primary_profs if p_config['prof_pool'] == 'primary' else (primary_profs + other_profs)

                all_days = sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99))
                scored_days = []
                for day in all_days:
                    day_slots = slot_groups.get(day, [])
                    if len(day_slots) < duration:
                        continue
                    test_slot = day_slots[0] if day_slots else None
                    slot_is_late = _is_late_slot(test_slot) if test_slot else False
                    s = _score_day_for_section(
                        day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used,
                        strict=p_config['strict_rules']
                    )
                    if s >= 0:
                        if primary_profs:
                            min_prof_day_h = min(prof_day_hours.get((p['prof_id'], day), 0.0) for p in primary_profs)
                            s -= int(min_prof_day_h * 15)
                        scored_days.append((s, day))
                scored_days.sort(key=lambda x: x[0], reverse=True)

                late_threshold = timedelta(hours=17)
                for _, day in scored_days:
                    day_slots = slot_groups[day]
                    if len(day_slots) < duration:
                        continue

                    for start_index in range(0, len(day_slots) - duration + 1):
                        block_slots = day_slots[start_index:start_index + duration]
                        if not _is_contiguous_block(block_slots):
                            continue

                        slot_is_late = _is_late_slot(block_slots[0], late_threshold)
                        if p_config['strict_rules'] and slot_is_late and len(late_days) >= _get_year_rules(yr)['max_late_days'] and day not in late_days:
                            continue

                        block_start = block_slots[0]['start_time']
                        block_end = block_slots[-1]['end_time']

                        if _has_conflict(day, block_start, block_end, section_bookings[sec_key]):
                            continue

                        eligible_profs = [
                            p for p in prof_pool
                            if professor_hours.get(p['prof_id'], 0.0) + duration <= (p.get('max_hours') or 40)
                            and not _has_conflict(day, block_start, block_end, professor_bookings.get(p['prof_id'], []))
                        ]
                        if not eligible_profs:
                            continue

                        assigned_room = _select_least_used_room(
                            cand_rooms, day, block_start, block_end,
                            room_bookings, room_usage, room_last_used, room_order
                        )

                        if not assigned_room:
                            continue

                        eligible_profs.sort(key=lambda p: _score_candidate_professor(
                            p, course_id, duration, day, primary_prof_ids, tolerance=2.0, allow_overload=False
                        ))
                        assigned_prof = eligible_profs[0]

                        # ASSIGNMENT SUCCESS
                        pk = assigned_prof['prof_id']
                        rk = assigned_room['room_id']
                        prof_full_name = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip()
                        assigned_prof_course_id = assigned_prof.get('prof_course_id') or prof_course_map.get((pk, course_id))

                        logging.debug(
                            f"[ROOM_ASSIGNED] Course: {course.get('course_name')} | Session: {session_type} | "
                            f"Room: {assigned_room.get('room_name')} ({assigned_room.get('room_type')})"
                        )

                        preview_entries.append({
                            'prof_course_id': assigned_prof_course_id,
                            'course_id': course_id,
                            'course_name': course.get('course_name'),
                            'prof_id': pk,
                            'professor_name': prof_full_name,
                            'section': section_name,
                            'room_id': rk,
                            'room_name': assigned_room.get('room_name'),
                            'day': day,
                            'start': block_start,
                            'end': block_end,
                            'session_type': session_type,
                            'semester': standard_semester,
                            'major': sec_major or course.get('major'),
                            'program': course.get('program') or program or session.get('program', ''),
                        })

                        section_bookings[sec_key].append((day, block_start, block_end))
                        room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                        assignment_step += 1
                        room_usage[rk] = room_usage.get(rk, 0) + 1
                        room_last_used[rk] = assignment_step
                        professor_bookings.setdefault(pk, []).append((day, block_start, block_end))
                        professor_hours[pk] = professor_hours.get(pk, 0.0) + duration
                        prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + duration
                        prof_course_count[(pk, course_id)] = prof_course_count.get((pk, course_id), 0) + 1
                        prof_section_count[pk] = prof_section_count.get(pk, 0) + 1

                        courses_per_day[day] = courses_per_day.get(day, 0) + 1
                        days_tried[day] = days_tried.get(day, 0) + 1
                        if slot_is_late:
                            late_days.add(day)

                        total_sessions_scheduled += 1
                        return True

            # Full Grid Relaxation (Any remaining day/time) - STRICT ROOM TYPE PRESERVED
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

                    conflict_free_profs = [
                        p for p in (primary_profs + other_profs)
                        if not _has_conflict(day, block_start, block_end, professor_bookings.get(p['prof_id'], []))
                    ]
                    assigned_prof = None
                    if conflict_free_profs:
                        under_cap_profs = [
                            p for p in conflict_free_profs
                            if professor_hours.get(p['prof_id'], 0.0) + duration <= (p.get('max_hours') or 40)
                        ]
                        if under_cap_profs:
                            under_cap_profs.sort(key=lambda p: _score_candidate_professor(
                                p, course_id, duration, day, primary_prof_ids, tolerance=2.0, allow_overload=False
                            ))
                            assigned_prof = under_cap_profs[0]
                        else:
                            # All available reach limit: fallback to least-overloaded professor
                            conflict_free_profs.sort(key=lambda p: _score_candidate_professor(
                                p, course_id, duration, day, primary_prof_ids, tolerance=2.0, allow_overload=True
                            ))
                            assigned_prof = conflict_free_profs[0]

                    assigned_room = _select_least_used_room(
                        cand_rooms, day, block_start, block_end,
                        room_bookings, room_usage, room_last_used, room_order
                    )

                    pk = assigned_prof['prof_id'] if assigned_prof else None
                    prof_name = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip() if assigned_prof else None
                    rk = assigned_room['room_id'] if assigned_room else None
                    room_name = assigned_room.get('room_name') if assigned_room else None

                    assigned_prof_course_id = (assigned_prof.get('prof_course_id') or prof_course_map.get((pk, course_id))) if pk else None
                    if pk and not assigned_prof_course_id:
                        # Fallback to TBA if no valid prof_course mapping exists for this prof-course pair
                        pk = None
                        prof_name = None

                    if not pk:
                        prof_tba_count += 1
                        logging.warning(f"[SCHEDULER PROF FAIL] No faculty available for {section_name} - {course.get('course_name')} {session_type} on {day} {block_start}-{block_end}")
                    if not rk:
                        room_tba_count += 1
                        logging.warning(f"[SCHEDULER ROOM FAIL] No matching {session_type} room available for {section_name} - {course.get('course_name')} on {day} {block_start}-{block_end}. Evaluated {len(cand_rooms)} {session_type} rooms.")

                    preview_entries.append({
                        'prof_course_id': assigned_prof_course_id,
                        'course_id': course_id,
                        'course_name': course.get('course_name'),
                        'prof_id': pk,
                        'professor_name': prof_name,
                        'section': section_name,
                        'room_id': rk,
                        'room_name': room_name,
                        'day': day,
                        'start': block_start,
                        'end': block_end,
                        'session_type': session_type,
                        'semester': standard_semester,
                        'major': sec_major or course.get('major'),
                        'program': course.get('program') or program or session.get('program', ''),
                    })

                    section_bookings[sec_key].append((day, block_start, block_end))
                    if rk:
                        room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                        assignment_step += 1
                        room_usage[rk] = room_usage.get(rk, 0) + 1
                        room_last_used[rk] = assignment_step
                    if pk:
                        professor_bookings.setdefault(pk, []).append((day, block_start, block_end))
                        professor_hours[pk] = professor_hours.get(pk, 0.0) + duration
                        prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + duration
                        prof_course_count[(pk, course_id)] = prof_course_count.get((pk, course_id), 0) + 1
                        prof_section_count[pk] = prof_section_count.get(pk, 0) + 1

                    courses_per_day[day] = courses_per_day.get(day, 0) + 1
                    days_tried[day] = days_tried.get(day, 0) + 1
                    total_sessions_scheduled += 1
                    return True

            return False

        # Helper to schedule a paired block (Lecture + Lab)
        def _schedule_paired_block(lec_dur, lab_dur, course, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
            nonlocal total_sessions_scheduled, prof_tba_count, room_tba_count, assignment_step
            total_dur = lec_dur + lab_dur
            course_id = course['course_id']
            primary_profs = professors_by_course.get(course_id, [])
            primary_prof_ids = {p['prof_id'] for p in primary_profs}
            other_profs = [p for p in all_professors_pool if p['prof_id'] not in primary_prof_ids]

            passes = [
                {'strict_rules': True,  'prof_pool': 'primary'},
                {'strict_rules': True,  'prof_pool': 'all'},
                {'strict_rules': False, 'prof_pool': 'primary'},
                {'strict_rules': False, 'prof_pool': 'all'},
            ]

            for p_config in passes:
                prof_pool = primary_profs if p_config['prof_pool'] == 'primary' else (primary_profs + other_profs)

                all_days = sorted(slot_groups.keys(), key=lambda d: day_order.get(d, 99))
                scored_days = []
                for day in all_days:
                    day_slots = slot_groups.get(day, [])
                    if len(day_slots) < total_dur:
                        continue
                    test_slot = day_slots[0] if day_slots else None
                    slot_is_late = _is_late_slot(test_slot) if test_slot else False
                    s = _score_day_for_section(
                        day, yr, courses_per_day, late_days, slot_is_late, days_tried, two_course_day_used,
                        strict=p_config['strict_rules']
                    )
                    if s >= 0:
                        if primary_profs:
                            min_prof_day_h = min(prof_day_hours.get((p['prof_id'], day), 0.0) for p in primary_profs)
                            s -= int(min_prof_day_h * 15)
                        scored_days.append((s, day))
                scored_days.sort(key=lambda x: x[0], reverse=True)

                late_threshold = timedelta(hours=17)
                for _, day in scored_days:
                    day_slots = slot_groups[day]
                    if len(day_slots) < total_dur:
                        continue

                    for start_index in range(0, len(day_slots) - total_dur + 1):
                        full_block = day_slots[start_index:start_index + total_dur]
                        if not _is_contiguous_block(full_block):
                            continue

                        slot_is_late = _is_late_slot(full_block[0], late_threshold)
                        if p_config['strict_rules'] and slot_is_late and len(late_days) >= _get_year_rules(yr)['max_late_days'] and day not in late_days:
                            continue

                        lec_start = full_block[0]['start_time']
                        lec_end = full_block[lec_dur - 1]['end_time'] if lec_dur > 0 else lec_start
                        lab_start = full_block[lec_dur]['start_time'] if lab_dur > 0 else lec_end
                        lab_end = full_block[-1]['end_time']

                        if _has_conflict(day, lec_start, lab_end, section_bookings[sec_key]):
                            continue

                        eligible_profs = [
                            p for p in prof_pool
                            if professor_hours.get(p['prof_id'], 0.0) + total_dur <= (p.get('max_hours') or 40)
                            and not _has_conflict(day, lec_start, lab_end, professor_bookings.get(p['prof_id'], []))
                        ]
                        if not eligible_profs:
                            continue

                        # STRICT Lecture room selection
                        assigned_lec_room = _select_least_used_room(
                            lecture_rooms, day, lec_start, lec_end,
                            room_bookings, room_usage, room_last_used, room_order
                        )
                        if not assigned_lec_room:
                            continue

                        # STRICT Laboratory room selection
                        assigned_lab_room = _select_least_used_room(
                            lab_rooms, day, lab_start, lab_end,
                            room_bookings, room_usage, room_last_used, room_order
                        )
                        if not assigned_lab_room:
                            continue

                        eligible_profs.sort(key=lambda p: _score_candidate_professor(
                            p, course_id, total_dur, day, primary_prof_ids, tolerance=2.0, allow_overload=False
                        ))
                        assigned_prof = eligible_profs[0]

                        # SUCCESSFUL PAIRED ASSIGNMENT
                        pk = assigned_prof['prof_id']
                        prof_name = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip()
                        assigned_prof_course_id = assigned_prof.get('prof_course_id') or prof_course_map.get((pk, course_id))
                        lec_rk = assigned_lec_room['room_id']
                        lab_rk = assigned_lab_room['room_id']

                        preview_entries.append({
                            'prof_course_id': assigned_prof_course_id,
                            'course_id': course_id,
                            'course_name': course.get('course_name'),
                            'prof_id': pk,
                            'professor_name': prof_name,
                            'section': section_name,
                            'room_id': lec_rk,
                            'room_name': assigned_lec_room.get('room_name'),
                            'day': day,
                            'start': lec_start,
                            'end': lec_end,
                            'session_type': 'Lecture',
                            'semester': standard_semester,
                            'major': sec_major or course.get('major'),
                            'program': course.get('program') or program or session.get('program', ''),
                        })
                        section_bookings[sec_key].append((day, lec_start, lec_end))
                        room_bookings.setdefault(lec_rk, []).append((day, lec_start, lec_end))
                        assignment_step += 1
                        room_usage[lec_rk] = room_usage.get(lec_rk, 0) + 1
                        room_last_used[lec_rk] = assignment_step
                        professor_bookings.setdefault(pk, []).append((day, lec_start, lec_end))

                        preview_entries.append({
                            'prof_course_id': assigned_prof_course_id,
                            'course_id': course_id,
                            'course_name': course.get('course_name'),
                            'prof_id': pk,
                            'professor_name': prof_name,
                            'section': section_name,
                            'room_id': lab_rk,
                            'room_name': assigned_lab_room.get('room_name'),
                            'day': day,
                            'start': lab_start,
                            'end': lab_end,
                            'session_type': 'Laboratory',
                            'semester': standard_semester,
                            'major': sec_major or course.get('major'),
                            'program': course.get('program') or program or session.get('program', ''),
                        })
                        section_bookings[sec_key].append((day, lab_start, lab_end))
                        room_bookings.setdefault(lab_rk, []).append((day, lab_start, lab_end))
                        assignment_step += 1
                        room_usage[lab_rk] = room_usage.get(lab_rk, 0) + 1
                        room_last_used[lab_rk] = assignment_step
                        professor_bookings.setdefault(pk, []).append((day, lab_start, lab_end))

                        professor_hours[pk] = professor_hours.get(pk, 0.0) + total_dur
                        prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + total_dur
                        prof_course_count[(pk, course_id)] = prof_course_count.get((pk, course_id), 0) + 1
                        prof_section_count[pk] = prof_section_count.get(pk, 0) + 1

                        courses_per_day[day] = courses_per_day.get(day, 0) + 1
                        days_tried[day] = days_tried.get(day, 0) + 1
                        if slot_is_late:
                            late_days.add(day)

                        total_sessions_scheduled += 2
                        return True

            # If contiguous 4-hour paired block could not be placed, split into separate Lecture and Laboratory sessions
            logging.info(f"[SCHEDULER] Splitting paired session for {section_name} - {course.get('course_name')} ({lec_dur}h Lecture, {lab_dur}h Lab) into independent slots.")
            ok_lec = _schedule_single_session('Lecture', lec_dur, course, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
            ok_lab = _schedule_single_session('Laboratory', lab_dur, course, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
            return ok_lec and ok_lab

        # Main assignment loop across all sections in the batch
        for section in all_sections:
            section_name = section['section']
            yr = int(section.get('year_level') or 1)
            sec_major = section.get('major')
            sec_key = (section_name, sec_major)
            section_courses = [
                c for c in courses_by_year.get(yr, [])
                if _major_matches(c.get('major'), sec_major)
            ]
            # Prioritize most constrained courses (fewest qualified faculty) first
            section_courses.sort(
                key=lambda c: (
                    len(professors_by_course.get(c.get('course_id'), [])),
                    c.get('course_id', 0)
                )
            )
            section_bookings.setdefault(sec_key, [])
            courses_per_day = {}
            two_course_day_used = False
            late_days = set()
            days_tried = {}

            for course in section_courses:
                subject_session_queue = _build_subject_session_queue(course)

                for session_item in subject_session_queue:
                    total_sessions_required += 1
                    if session_item.get('paired'):
                        _schedule_paired_block(
                            session_item['lec_duration'], session_item['lab_duration'],
                            course, section_name, yr, sec_major, sec_key,
                            courses_per_day, late_days, days_tried, two_course_day_used
                        )
                    else:
                        _schedule_single_session(
                            session_item['session_type'], session_item['duration'],
                            course, section_name, yr, sec_major, sec_key,
                            courses_per_day, late_days, days_tried, two_course_day_used
                        )

        # Audit & Utilization Metrics Logging
        total_faculty_capacity = sum(int(p.get('max_hours') or 40) for p in all_professors_pool)
        total_prof_hours_assigned = sum(professor_hours.values())
        prof_util_pct = (total_prof_hours_assigned / total_faculty_capacity * 100) if total_faculty_capacity > 0 else 0
        active_prof_ids = [pid for pid, h in professor_hours.items() if h > 0]
        active_hours = [professor_hours[pid] for pid in active_prof_ids]
        min_load = min(active_hours) if active_hours else 0
        max_load = max(active_hours) if active_hours else 0
        spread_load = max_load - min_load
        total_entries_count = len(preview_entries)
        logging.info(
            f"[SCHEDULER COMPLETE] Generated {total_entries_count} schedule entries across {len(all_sections)} sections. "
            f"Prof TBA: {prof_tba_count}, Room TBA: {room_tba_count}. "
            f"Faculty Load: {total_prof_hours_assigned}h / {total_faculty_capacity}h ({prof_util_pct:.1f}% capacity utilized). "
            f"Active Faculty: {len(active_prof_ids)}/{len(all_professors_pool)} | Min Load: {min_load}h | Max Load: {max_load}h | Spread: {spread_load}h."
        )

        # Strict validation of generated entries before storing preview
        all_rooms_map = {int(r['room_id']): r for r in all_rooms if r.get('room_id')}

        # Batch room distribution metrics logging
        if room_usage:
            max_room_usage = max(room_usage.values())
            min_room_usage = min(room_usage.values())
            distribution_difference = max_room_usage - min_room_usage
            room_usage_log = ", ".join(f"{all_rooms_map.get(rid, {}).get('room_name', f'Room {rid}')}: {count}" for rid, count in room_usage.items())
            logging.info(
                f"[ROOM DISTRIBUTION] Batch room utilization: {room_usage_log} | "
                f"Max: {max_room_usage} | Min: {min_room_usage} | Difference: {distribution_difference}"
            )
        is_valid, validation_errors = _validate_schedule_room_types(preview_entries, all_rooms_map)
        if not is_valid:
            for err in validation_errors:
                logging.error(f"[GENERATION ROOM VALIDATION ERROR] {err}")
            raise ValueError(f"Generated schedule failed room-type validation: {validation_errors[0]}")

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

    target_entry = None
    for entry in preview:
        if entry.get('id') == entry_id:
            target_entry = entry
            break

    if not target_entry:
        return jsonify({'error': 'Preview entry not found.'}), 404

    try:
        prof_course_id = data.get('prof_course_id')
        if not prof_course_id:
            return jsonify({'error': 'Professor & Course selection is required.'}), 400

        try:
            prof_course_id_int = int(prof_course_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid professor-course selection.'}), 400

        pc_res = supabase.table('prof_course').select(
            'prof_course_id, course_id, prof_id, course(course_id, course_name), professor(prof_id, first_name, last_name)'
        ).eq('prof_course_id', prof_course_id_int).execute()
        pc_row = _first(pc_res.data or [])
        if not pc_row:
            return jsonify({'error': 'Selected professor-course pairing is invalid.'}), 400

        course_id_int = pc_row.get('course_id')
        prof_id_int = pc_row.get('prof_id')
        c = _rel(pc_row, 'course') or {}
        course_name = c.get('course_name') or f"Course #{course_id_int}"
        p = _rel(pc_row, 'professor') or {}
        prof_name = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or 'TBA'

        section = (data.get('section') or target_entry.get('section') or '').strip()
        if not section:
            return jsonify({'error': 'Section is required.'}), 400

        day = data.get('day') or target_entry.get('day') or 'Monday'
        start_input = data.get('start') or target_entry.get('start')
        end_input = data.get('end') or target_entry.get('end')

        timeslot_input = data.get('timeslot')
        if timeslot_input:
            t_part = timeslot_input
            if '|' in timeslot_input:
                d_part, t_part = timeslot_input.split('|', 1)
                d_part = d_part.strip()
                if d_part in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
                    day = d_part
            if '-' in t_part:
                sp, ep = t_part.split('-', 1)
                start_input = sp.strip()
                end_input = ep.strip()

        if day not in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']:
            return jsonify({'error': 'Invalid day selected.'}), 400

        start_time = _parse_time(start_input)
        end_time = _parse_time(end_input)
        if start_time is None or end_time is None or end_time <= start_time:
            return jsonify({'error': 'Invalid start or end time.'}), 400

        formatted_start = _format_time(start_time)
        formatted_end = _format_time(end_time)
        formatted_timerange = f"{day} | {formatted_start} - {formatted_end}"

        room_id = data.get('room_id')
        room_name = 'TBA'
        room_id_int = None
        if room_id not in (None, '', '0', 0):
            try:
                room_id_int = int(room_id)
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid room selection.'}), 400

            room_res = supabase.table('room').select('room_id, room_name, room_type').eq('room_id', room_id_int).execute()
            row = _first(room_res.data or [])
            if not row:
                return jsonify({'error': 'Selected room is invalid.'}), 400

            target_session_type = target_entry.get('session_type') or 'Lecture'
            if not _room_matches_session(row, target_session_type):
                return jsonify({
                    'error': f"Room type mismatch: Cannot assign '{row.get('room_name')}' ({row.get('room_type')}) to a {target_session_type} class."
                }), 400

            room_name = row.get('room_name')

        # 0. Lunch Break Conflict: ensure class does not overlap system lunch break
        try:
            ts_res = supabase.table('timeslot').select('lunch_time').execute()
            ts_rows = ts_res.data or []
        except Exception:
            ts_rows = []
        conf_lunch_td = None
        for tr in ts_rows:
            parsed_lt = _parse_time(tr.get('lunch_time'))
            if parsed_lt is not None:
                conf_lunch_td = parsed_lt
                break
        if conf_lunch_td is None:
            conf_lunch_td = timedelta(hours=12)

        if _is_blocked_by_lunch(start_time, end_time, conf_lunch_td):
            lunch_str = f"{_format_time(conf_lunch_td)} - {_format_time(conf_lunch_td + timedelta(hours=1))}"
            return jsonify({
                'error': f"Lunch break conflict: Classes cannot be scheduled during the designated lunch break ({lunch_str})."
            }), 400

        # Conflict Detection: check against all other entries in the active preview
        for other in preview:
            if other.get('id') == entry_id:
                continue
            other_day = other.get('day')
            if other_day != day:
                continue

            other_start = _parse_time(other.get('start'))
            other_end = _parse_time(other.get('end'))
            if other_start is None or other_end is None:
                continue

            # 1. Section conflict: same section cannot have overlapping classes
            if (other.get('section') or '').strip() == section:
                if _has_conflict(day, start_time, end_time, [(other_day, other_start, other_end)]):
                    other_timeslot = other.get('time_range') or f"{other.get('start')} - {other.get('end')}"
                    return jsonify({
                        'error': f"Section conflict: Section {section} already has {other.get('course_name') or 'a class'} scheduled on {day} ({other_timeslot})."
                    }), 400

            # 2. Room conflict: same room cannot have overlapping bookings
            if room_id_int is not None and other.get('room_id') == room_id_int:
                if _has_conflict(day, start_time, end_time, [(other_day, other_start, other_end)]):
                    other_timeslot = other.get('time_range') or f"{other.get('start')} - {other.get('end')}"
                    return jsonify({
                        'error': f"Room conflict: Room {room_name} is already booked on {day} ({other_timeslot}) by Section {other.get('section')}."
                    }), 400

            # 3. Professor conflict: same professor cannot teach multiple classes at once
            if prof_id_int is not None:
                other_prof_id = other.get('prof_id')
                if other_prof_id is None and other.get('prof_course_id') == prof_course_id_int:
                    other_prof_id = prof_id_int

                if other_prof_id is not None and other_prof_id == prof_id_int:
                    if _has_conflict(day, start_time, end_time, [(other_day, other_start, other_end)]):
                        other_timeslot = other.get('time_range') or f"{other.get('start')} - {other.get('end')}"
                        return jsonify({
                            'error': f"Professor conflict: {prof_name} is already scheduled on {day} ({other_timeslot}) for Section {other.get('section')}."
                        }), 400

        # Apply updates to target preview entry
        target_entry['prof_course_id'] = prof_course_id_int
        target_entry['course_id'] = course_id_int
        target_entry['course_name'] = course_name
        target_entry['prof_id'] = prof_id_int
        target_entry['professor_name'] = prof_name
        target_entry['section'] = section
        target_entry['room_id'] = room_id_int
        target_entry['room_name'] = room_name
        target_entry['day'] = day
        target_entry['start'] = formatted_start
        target_entry['end'] = formatted_end
        target_entry['time_range'] = formatted_timerange

        _set_preview_for_user(preview)

        return jsonify({
            'success': True,
            'message': 'Schedule entry updated successfully.',
            'entry': {
                'id': entry_id,
                'prof_course_id': prof_course_id_int,
                'course_name': course_name,
                'prof_id': prof_id_int,
                'professor_name': prof_name,
                'section': section,
                'room_id': room_id_int,
                'room_name': room_name,
                'day': day,
                'start': formatted_start,
                'end': formatted_end,
                'time_range': formatted_timerange,
                'session_type': target_entry.get('session_type') or 'Lecture',
            }
        })
    except Exception as err:
        logging.exception(f"Error in edit_preview_entry: {err}")
        return jsonify({'error': str(err)}), 500


@app.route('/api/preview_entries', methods=['GET'])
@login_required
def api_preview_entries():
    preview = _get_preview_for_user()
    return jsonify({'success': True, 'entries': preview or []})


@app.route('/confirm_preview', methods=['POST'])
@login_required
def confirm_preview():
    preview = _get_preview_for_user()
    if not preview:
        logging.warning("[confirm_preview] No preview schedule found in server store or session.")
        flash('No preview schedule found to confirm.', 'warning')
        return redirect(url_for('generate_schedule'))

    try:
        # Pre-confirmation strict room type validation check
        rooms_res = supabase.table('room').select('room_id, room_name, room_type').execute()
        all_rooms_map = {int(r['room_id']): r for r in (rooms_res.data or []) if r.get('room_id')}
        is_valid, validation_errors = _validate_schedule_room_types(preview, all_rooms_map)
        if not is_valid:
            for err in validation_errors:
                logging.error(f"[CONFIRM ROOM VALIDATION ERROR] {err}")
            flash(f"Cannot save schedule: Room type validation failed: {validation_errors[0]}", 'error')
            return redirect(url_for('generate_schedule'))

        # Determine the semester, program, and sections from preview
        sem_val = (preview[0].get('semester') or '').strip()
        prog_val = (preview[0].get('program') or session.get('program') or '').strip()
        sections_in_preview = list({e.get('section') for e in preview if e.get('section')})

        # Prepare payload rows
        rows = []
        for entry in preview:
            entry_sem = (entry.get('semester') or sem_val).strip()
            entry_prog = entry.get('program') or prog_val or session.get('program', '')
            prof_course_id = entry.get('prof_course_id')
            prof_id = entry.get('prof_id')
            course_id = entry.get('course_id')
            room_id = entry.get('room_id')

            if not prof_course_id and prof_id and course_id:
                pc_chk = supabase.table('prof_course').select('prof_course_id').eq('prof_id', int(prof_id)).eq('course_id', int(course_id)).execute()
                pc_chk_row = _first(pc_chk.data or [])
                if pc_chk_row:
                    prof_course_id = pc_chk_row.get('prof_course_id')

            rows.append({
                'prof_course_id': int(prof_course_id) if prof_course_id not in (None, '', 0, '0') else None,
                'room_id': int(room_id) if room_id not in (None, '', 0, '0') else None,
                'day': entry.get('day') or 'Monday',
                'class_start': _to_time_string(entry.get('start')),
                'class_end': _to_time_string(entry.get('end')),
                'session_type': entry.get('session_type') or 'Lecture',
                'section': entry.get('section'),
                'semester': entry_sem,
                'major': entry.get('major'),
                'program': entry_prog,
            })

        if not rows:
            logging.error(f"[confirm_preview] Constructed rows payload is empty from preview of length {len(preview)}.")
            flash('Failed to confirm schedule: No valid class entries found in preview.', 'error')
            return redirect(url_for('generate_schedule'))

        archived_by = session.get('username') or session.get('first_name') or 'Scheduler'
        if session.get('last_name'):
            archived_by = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()

        logging.info(f"[confirm_preview] Confirming {len(rows)} entries for semester='{sem_val}', program='{prog_val}', user='{archived_by}'")

        # Atomic PostgreSQL Transaction via confirm_schedule_transaction RPC (Archive -> Delete -> Insert)
        rpc_success = False
        rpc_details = {}
        if hasattr(supabase, 'rpc'):
            try:
                rpc_res = supabase.rpc('confirm_schedule_transaction', {
                    'p_semester': sem_val,
                    'p_program': prog_val,
                    'p_rows': rows,
                    'p_clear_scope': True,
                    'p_archived_by': archived_by
                }).execute()
                if rpc_res and getattr(rpc_res, 'data', None):
                    data = rpc_res.data
                    if isinstance(data, dict) and data.get('success'):
                        rpc_success = True
                        rpc_details = data
                        logging.info(f"[confirm_preview] RPC transaction completed successfully: {data}")
                    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and data[0].get('success'):
                        rpc_success = True
                        rpc_details = data[0]
                        logging.info(f"[confirm_preview] RPC transaction completed successfully: {data[0]}")
            except Exception as rpc_err:
                logging.warning(f"[confirm_preview] confirm_schedule_transaction RPC execution fallback: {rpc_err}")
                rpc_success = False

        if not rpc_success:
            # Fallback: Archive existing active records for this program, delete them, and insert new records
            try:
                old_query = supabase.table('schedule').select('*')
                if prog_val and str(prog_val).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                    old_query = old_query.or_(f'program.eq.{prog_val},program.is.null,program.eq.')
                old_rows = old_query.execute().data or []
                if old_rows:
                    batch_id = str(uuid.uuid4())
                    archive_payload = []
                    for r in old_rows:
                        archive_payload.append({
                            'batch_id': batch_id,
                            'original_schedule_id': r.get('schedule_id'),
                            'prof_course_id': r.get('prof_course_id'),
                            'room_id': r.get('room_id'),
                            'day': r.get('day'),
                            'class_start': _to_time_string(r.get('class_start')),
                            'class_end': _to_time_string(r.get('class_end')),
                            'session_type': r.get('session_type') or 'Lecture',
                            'section': r.get('section'),
                            'semester': r.get('semester'),
                            'major': r.get('major'),
                            'program': r.get('program'),
                            'archived_by': archived_by,
                            'archive_reason': f'Replaced on schedule confirmation of {sem_val}'
                        })
                    supabase.table('schedule_archive').insert(archive_payload).execute()
            except Exception as arc_err:
                logging.warning(f"[confirm_preview] Fallback archive insert warning: {arc_err}")

            del_query = supabase.table('schedule').delete()
            if prog_val and str(prog_val).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                del_query = del_query.or_(f'program.eq.{prog_val},program.is.null,program.eq.')
            else:
                del_query = del_query.neq('schedule_id', -1)
            del_query.execute()

            # Batch insert into schedule table (in chunks to avoid payload limits)
            chunk_size = 50
            for i in range(0, len(rows), chunk_size):
                chunk = rows[i:i + chunk_size]
                supabase.table('schedule').insert(chunk).execute()

        # Update session generated_sections and active semester so state is consistent
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
        session['active_semester'] = sem_val

        archived_count = rpc_details.get('archived_count', 0)
        archived_note = f" (Previous {archived_count} entries moved to archive)" if archived_count else ""
        log_activity('confirm', 'schedule', f'Generated and saved {len(rows)} entries for {len(sections_in_preview)} sections ({sem_val}){archived_note}')
        flash(f'{sem_val} schedule saved successfully!{archived_note} ({len(rows)} class entries confirmed)', 'success')
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
# SCHEDULE ARCHIVE & RESTORATION
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/schedule_archive')
@login_required
def schedule_archive():
    semester_filter = (request.args.get('semester') or '').strip()
    program_filter = (request.args.get('program') or '').strip()
    user_role = session.get('role', 'Viewer')
    user_program = session.get('program', '')

    effective_program = user_program if (user_role == 'Viewer' and user_program) else program_filter

    batches_list = []
    try:
        # 1. Primary: Database RPC aggregation (unlimited batches, grouped by batch_id directly in Postgres)
        rpc_success = False
        if hasattr(supabase, 'rpc'):
            try:
                res = supabase.rpc('get_schedule_archive_batches', {
                    'p_program': effective_program or None,
                    'p_semester': semester_filter or None
                }).execute()
                if res and isinstance(res.data, list):
                    rpc_success = True
                    for r in res.data:
                        bid = str(r.get('batch_id') or '')
                        if not bid:
                            continue
                        arch_at_raw = r.get('archived_at')
                        arch_at_fmt = str(arch_at_raw)
                        if arch_at_raw:
                            try:
                                dt = datetime.fromisoformat(str(arch_at_raw).replace('Z', '+00:00'))
                                arch_at_fmt = dt.strftime('%b %d, %Y - %I:%M %p')
                            except Exception:
                                arch_at_fmt = str(arch_at_raw)

                        secs = sorted(list(r.get('sections') or []))
                        batches_list.append({
                            'batch_id': bid,
                            'semester': r.get('semester') or '1st Semester',
                            'program': r.get('program') or '',
                            'archived_at_raw': arch_at_raw,
                            'archived_at_fmt': arch_at_fmt,
                            'archived_by': r.get('archived_by') or 'Scheduler',
                            'archive_reason': r.get('archive_reason') or 'Replaced on confirmation',
                            'entry_count': int(r.get('entry_count') or 0),
                            'section_count': int(r.get('section_count') or len(secs)),
                            'sections_preview': ', '.join(secs[:6]) + ('...' if len(secs) > 6 else ''),
                        })
            except Exception as rpc_err:
                logging.warning(f"get_schedule_archive_batches RPC failed ({rpc_err}), using paginated table query.")
                rpc_success = False

        if not rpc_success:
            # 2. Fallback: Paginated table query across all chunks to guarantee unlimited batches without 1000-row clipping
            all_rows = []
            page_size = 1000
            start = 0
            while True:
                query = supabase.table('schedule_archive').select('batch_id, semester, program, section, major, archived_at, archived_by, archive_reason')
                if effective_program:
                    query = query.eq('program', effective_program)
                if semester_filter:
                    query = query.eq('semester', semester_filter)
                chunk_res = query.order('archived_at', desc=True).range(start, start + page_size - 1).execute()
                chunk_data = chunk_res.data or []
                all_rows.extend(chunk_data)
                if len(chunk_data) < page_size:
                    break
                start += page_size

            batches_map = {}
            for r in all_rows:
                bid = str(r.get('batch_id') or '')
                if not bid:
                    continue
                if bid not in batches_map:
                    arch_at_raw = r.get('archived_at')
                    arch_at_fmt = str(arch_at_raw)
                    if arch_at_raw:
                        try:
                            dt = datetime.fromisoformat(str(arch_at_raw).replace('Z', '+00:00'))
                            arch_at_fmt = dt.strftime('%b %d, %Y - %I:%M %p')
                        except Exception:
                            arch_at_fmt = str(arch_at_raw)

                    batches_map[bid] = {
                        'batch_id': bid,
                        'semester': r.get('semester') or '1st Semester',
                        'program': r.get('program') or '',
                        'archived_at_raw': arch_at_raw,
                        'archived_at_fmt': arch_at_fmt,
                        'archived_by': r.get('archived_by') or 'Scheduler',
                        'archive_reason': r.get('archive_reason') or 'Replaced on confirmation',
                        'entry_count': 0,
                        'sections_set': set(),
                    }
                batches_map[bid]['entry_count'] += 1
                if r.get('section'):
                    batches_map[bid]['sections_set'].add(r.get('section'))

            for b in batches_map.values():
                sorted_secs = sorted(list(b['sections_set']))
                b['section_count'] = len(sorted_secs)
                b['sections_preview'] = ', '.join(sorted_secs[:6]) + ('...' if len(sorted_secs) > 6 else '')
                batches_list.append(b)

            batches_list.sort(key=lambda x: str(x.get('archived_at_raw') or ''), reverse=True)

        semester_options = ['1st Semester', '2nd Semester']
        program_options = ['BSIT']
        if user_program and user_program not in program_options:
            program_options.append(user_program)

    except Exception as err:
        logging.exception(f"Error retrieving schedule archive: {err}")
        batches_list = []
        semester_options = ['1st Semester', '2nd Semester']
        program_options = ['BSIT']

    return render_template(
        'schedule_archive.html',
        active_page='schedule_archive',
        archive_batches=batches_list,
        semester_options=semester_options,
        program_options=program_options,
        semester_filter=semester_filter,
        program_filter=program_filter,
    )


@app.route('/schedule_archive/<batch_id>')
@login_required
def view_schedule_archive_batch(batch_id):
    try:
        res = supabase.table('schedule_archive').select('*, course(course_name), professor(first_name, last_name), room(room_name)').eq('batch_id', batch_id).execute()
        rows = res.data or []
        if not rows:
            flash('Archived schedule version not found.', 'warning')
            return redirect(url_for('schedule_archive'))

        first_row = rows[0]
        arch_at_raw = first_row.get('archived_at')
        arch_at_fmt = str(arch_at_raw)
        if arch_at_raw:
            try:
                dt = datetime.fromisoformat(str(arch_at_raw).replace('Z', '+00:00'))
                arch_at_fmt = dt.strftime('%b %d, %Y - %I:%M %p')
            except Exception:
                arch_at_fmt = str(arch_at_raw)

        batch_info = {
            'batch_id': batch_id,
            'semester': first_row.get('semester') or '1st Semester',
            'program': first_row.get('program') or '',
            'archived_at_fmt': arch_at_fmt,
            'archived_by': first_row.get('archived_by') or 'Scheduler',
            'archive_reason': first_row.get('archive_reason') or '',
        }

        sections_by_key = {}
        sections = []
        seen = set()

        for r in rows:
            sec = r.get('section')
            if not sec:
                continue
            sem = r.get('semester', '')
            maj = r.get('major')
            key = (sec, sem, maj)

            if key not in seen:
                seen.add(key)
                sections.append({
                    'section': sec,
                    'section_name': sec,
                    'semester': sem,
                    'major': maj,
                    'year_level': _year_of_section(sec),
                })

            if key not in sections_by_key:
                sections_by_key[key] = {
                    'section': {
                        'section': sec,
                        'section_name': sec,
                        'semester': sem,
                        'major': maj,
                        'year_level': _year_of_section(sec),
                    },
                    'entries': []
                }

            pc = _rel(r, 'prof_course') or {}
            c = _rel(pc, 'course') or _rel(r, 'course') or {}
            p = _rel(pc, 'professor') or _rel(r, 'professor') or {}
            rm = _rel(r, 'room') or {}
            pname = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or 'TBA'
            s_fmt = _format_time(r.get('class_start'))
            e_fmt = _format_time(r.get('class_end'))

            sections_by_key[key]['entries'].append({
                'prof_course_id': r.get('prof_course_id'),
                'course_id': pc.get('course_id') or r.get('course_id'),
                'course_name': c.get('course_name') or 'TBA',
                'professor_name': pname,
                'room_name': rm.get('room_name') or 'TBA',
                'day': r.get('day') or '',
                'start': s_fmt,
                'end': e_fmt,
                'time_range': f"{r.get('day')} | {s_fmt} - {e_fmt}" if r.get('day') and s_fmt else 'TBA',
                'session_type': r.get('session_type') or 'Lecture',
                'section': sec,
                'semester': sem,
                'major': maj,
            })

        sections.sort(key=lambda s: str(s.get('section') or ''))
        sections_with_entries = list(sections_by_key.values())
        for item in sections_with_entries:
            item['entries'].sort(key=lambda e: (_DAY_ORDER.get(e.get('day') or '', 99), str(e.get('start') or '')))

        year_groups = _group_preview_sections(sections_with_entries)

        return render_template(
            'schedule_archive_detail.html',
            active_page='schedule_archive',
            batch_info=batch_info,
            entries=rows,
            sections=sections,
            year_groups=year_groups,
        )
    except Exception as err:
        logging.exception(f"Error loading archived schedule batch {batch_id}: {err}")
        flash(f"Error loading archived schedule: {err}", "error")
        return redirect(url_for('schedule_archive'))


@app.route('/restore_schedule_archive/<batch_id>', methods=['POST'])
@login_required
def restore_schedule_archive(batch_id):
    user_role = session.get('role', 'Viewer')
    if user_role not in ['admin', 'Scheduler', 'scheduler', 'Admin']:
        flash('You do not have permission to restore archived schedules.', 'danger')
        return redirect(url_for('schedule_archive'))

    restored_by = session.get('username') or session.get('first_name') or 'Scheduler'
    if session.get('last_name'):
        restored_by = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()

    try:
        rpc_success = False
        res_data = None
        if hasattr(supabase, 'rpc'):
            try:
                rpc_res = supabase.rpc('restore_archived_schedule_batch', {
                    'p_batch_id': batch_id,
                    'p_restored_by': restored_by
                }).execute()
                if rpc_res and getattr(rpc_res, 'data', None):
                    res_data = rpc_res.data
                    rpc_success = True
            except Exception as rpc_err:
                logging.warning(f"restore_archived_schedule_batch RPC fallback: {rpc_err}")
                rpc_success = False

        if not rpc_success:
            # Fallback restore logic:
            arch_rows = (supabase.table('schedule_archive').select('*').eq('batch_id', batch_id).execute().data) or []
            if not arch_rows:
                flash('Archived batch not found.', 'warning')
                return redirect(url_for('schedule_archive'))

            target_sem = arch_rows[0].get('semester') or '1st Semester'
            target_prog = arch_rows[0].get('program') or ''

            curr_query = supabase.table('schedule').select('*').eq('semester', target_sem)
            if target_prog and str(target_prog).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                curr_query = curr_query.or_(f'program.eq.{target_prog},program.is.null,program.eq.')
            curr_rows = curr_query.execute().data or []

            if curr_rows:
                new_batch_id = str(uuid.uuid4())
                archive_current = []
                for cr in curr_rows:
                    archive_current.append({
                        'batch_id': new_batch_id,
                        'original_schedule_id': cr.get('schedule_id'),
                        'prof_course_id': cr.get('prof_course_id'),
                        'room_id': cr.get('room_id'),
                        'day': cr.get('day'),
                        'class_start': _to_time_string(cr.get('class_start')),
                        'class_end': _to_time_string(cr.get('class_end')),
                        'session_type': cr.get('session_type') or 'Lecture',
                        'section': cr.get('section'),
                        'semester': cr.get('semester'),
                        'major': cr.get('major'),
                        'program': cr.get('program'),
                        'archived_by': restored_by,
                        'archive_reason': f'Archived prior to restoring batch {batch_id}'
                    })
                supabase.table('schedule_archive').insert(archive_current).execute()

            del_q = supabase.table('schedule').delete().eq('semester', target_sem)
            if target_prog and str(target_prog).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                del_q = del_q.or_(f'program.eq.{target_prog},program.is.null,program.eq.')
            del_q.execute()

            restore_payload = []
            for ar in arch_rows:
                pcid = ar.get('prof_course_id')
                if not pcid and ar.get('prof_id') and ar.get('course_id'):
                    chk = supabase.table('prof_course').select('prof_course_id').eq('prof_id', ar.get('prof_id')).eq('course_id', ar.get('course_id')).execute()
                    row_chk = _first(chk.data or [])
                    if row_chk:
                        pcid = row_chk.get('prof_course_id')

                restore_payload.append({
                    'prof_course_id': pcid,
                    'room_id': ar.get('room_id'),
                    'day': ar.get('day'),
                    'class_start': _to_time_string(ar.get('class_start')),
                    'class_end': _to_time_string(ar.get('class_end')),
                    'session_type': ar.get('session_type') or 'Lecture',
                    'section': ar.get('section'),
                    'semester': ar.get('semester') or target_sem,
                    'major': ar.get('major'),
                    'program': ar.get('program') or target_prog,
                })

            chunk_size = 50
            for i in range(0, len(restore_payload), chunk_size):
                chunk = restore_payload[i:i + chunk_size]
                supabase.table('schedule').insert(chunk).execute()

            res_data = {'semester': target_sem, 'restored_count': len(restore_payload)}

        target_semester = (res_data or {}).get('semester') or '1st Semester'
        count = (res_data or {}).get('restored_count') or 'all'

        log_activity('restore', 'schedule', f'Restored schedule batch {batch_id} ({target_semester})')
        flash(f'Archived schedule version restored successfully! ({count} classes reactivated)', 'success')
        return redirect(url_for('schedules', semester=target_semester))

    except Exception as err:
        logging.exception(f"Error restoring schedule archive {batch_id}: {err}")
        flash(f"Error restoring schedule version: {err}", "error")
        return redirect(url_for('schedule_archive'))


@app.route('/delete_schedule_archive/<batch_id>', methods=['POST'])
@login_required
def delete_schedule_archive(batch_id):
    user_role = session.get('role', 'Viewer')
    if user_role.lower() != 'admin':
        flash('Only administrators can delete archived schedule records.', 'danger')
        return redirect(url_for('schedule_archive'))

    try:
        supabase.table('schedule_archive').delete().eq('batch_id', batch_id).execute()
        log_activity('delete', 'schedule_archive', f'Deleted archived schedule batch {batch_id}')
        flash('Archived schedule version deleted successfully.', 'success')
    except Exception as err:
        logging.exception(f"Error deleting archived schedule batch {batch_id}: {err}")
        flash(f"Error deleting archive: {err}", "error")

    return redirect(url_for('schedule_archive'))


# ──────────────────────────────────────────────────────────────────────────────
# BACKUP & RESTORE (JSON export/import via Supabase / PostgreSQL)
# ──────────────────────────────────────────────────────────────────────────────

BACKUP_TABLES_INSERT_ORDER = [
    'program_department',
    'users',
    'professor',
    'room',
    'timeslot',
    'course',
    'prof_course',
    'schedule',
    'schedule_archive',
    'irregular_students',
    'irregular_student_schedule',
    'delete_requests',
    'scheduler_notifications',
    'activity_log',
]

BACKUP_TABLES_DELETE_ORDER = list(reversed(BACKUP_TABLES_INSERT_ORDER))

BACKUP_PKS = {
    'program_department': 'program_name',
    'users': 'id',
    'professor': 'prof_id',
    'room': 'room_id',
    'timeslot': 'timeslot_id',
    'course': 'course_id',
    'prof_course': 'prof_course_id',
    'schedule': 'schedule_id',
    'schedule_archive': 'archive_id',
    'irregular_students': 'student_id',
    'irregular_student_schedule': 'id',
    'delete_requests': 'id',
    'scheduler_notifications': 'id',
    'activity_log': 'id',
}

# Alias for backwards compatibility
BACKUP_TABLES = BACKUP_TABLES_INSERT_ORDER


def _fetch_all_table_data(table_name, page_size=1000):
    """Fetch all rows from a Supabase table using pagination to avoid row limits."""
    all_rows = []
    start = 0
    while True:
        res = supabase.table(table_name).select('*').range(start, start + page_size - 1).execute()
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def _execute_client_side_restore(data_dict, clear_existing=True):
    """Fallback client-side restore handling foreign keys in proper order with batching."""
    details = {}
    total_restored = 0

    # 1. Clear tables in reverse dependency order if requested
    if clear_existing:
        for table in BACKUP_TABLES_DELETE_ORDER:
            if table in data_dict:
                try:
                    pk = BACKUP_PKS.get(table)
                    if pk:
                        supabase.table(table).delete().neq(pk, -999999999 if pk != 'id' and pk != 'program_name' else '__none__').execute()
                except Exception as e:
                    logging.warning(f"Could not clear table {table} via client: {e}")

    # 2. Insert tables in forward dependency order with chunking
    for table in BACKUP_TABLES_INSERT_ORDER:
        rows = data_dict.get(table)
        if not rows or not isinstance(rows, list):
            continue

        pk = BACKUP_PKS.get(table)
        chunk_size = 500
        table_count = 0
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            if pk:
                supabase.table(table).upsert(chunk, on_conflict=pk).execute()
            else:
                supabase.table(table).insert(chunk).execute()
            table_count += len(chunk)

        details[table] = table_count
        total_restored += table_count

    return {'success': True, 'total_restored': total_restored, 'details': details}


@app.route('/backup', methods=['GET'])
@login_required
@role_required(['admin', 'scheduler'])
def backup_database():
    """Export tables to a structured JSON file and stream it as a download."""
    try:
        tables_param = request.args.get('tables')
        if tables_param:
            requested = [t.strip() for t in tables_param.split(',') if t.strip() in BACKUP_TABLES_INSERT_ORDER]
            tables_to_export = requested if requested else BACKUP_TABLES_INSERT_ORDER
        else:
            tables_to_export = BACKUP_TABLES_INSERT_ORDER

        table_data = {}
        total_records = 0
        table_counts = {}

        for table in tables_to_export:
            rows = _fetch_all_table_data(table)
            table_data[table] = rows
            table_counts[table] = len(rows)
            total_records += len(rows)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        is_partial = len(tables_to_export) < len(BACKUP_TABLES_INSERT_ORDER)
        filename = f"backup_{'partial_' if is_partial else ''}{timestamp}.json"

        # Structured JSON document with metadata
        dump = {
            '_metadata': {
                'version': '2.0',
                'system': 'ClassScheduling System',
                'exported_at': datetime.now().isoformat(),
                'exported_by': session.get('username') or session.get('email', 'admin'),
                'is_partial': is_partial,
                'tables_included': tables_to_export,
                'total_records': total_records,
                'table_counts': table_counts,
            },
            'data': table_data,
        }

        # Root access compatibility
        for k, v in table_data.items():
            dump[k] = v

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='w', encoding='utf-8')
        try:
            json.dump(dump, tmp, default=str, indent=2)
        finally:
            tmp.close()

        log_activity('backup', 'database', f"{filename} ({total_records} rows across {len(tables_to_export)} tables)")
        return send_file(
            tmp.name,
            as_attachment=True,
            download_name=filename,
            mimetype='application/json',
        )
    except Exception as exc:
        logging.exception('Unexpected backup error: %s', exc)
        flash(f'An unexpected error occurred during backup: {exc}', 'error')
        return redirect(request.referrer or url_for('home'))


@app.route('/restore', methods=['POST'])
@login_required
@role_required(['admin', 'scheduler'])
def restore_database():
    """Import a JSON backup, restoring database state with FK ordering and transaction safety."""
    uploaded_file = request.files.get('backup_file') or request.files.get('sql_file')

    if not uploaded_file or uploaded_file.filename == '':
        flash('No file selected for restore.', 'error')
        return redirect(request.referrer or url_for('home'))

    original_filename = secure_filename(uploaded_file.filename)
    if not original_filename.lower().endswith('.json'):
        flash('Invalid file type. Only .json backup files are accepted.', 'error')
        return redirect(request.referrer or url_for('home'))

    try:
        raw_content = uploaded_file.read().decode('utf-8')
        if not raw_content.strip():
            flash('The uploaded backup file is empty.', 'error')
            return redirect(request.referrer or url_for('home'))

        parsed_json = json.loads(raw_content)
        if not isinstance(parsed_json, dict):
            flash('Invalid backup format. Root structure must be a JSON object.', 'error')
            return redirect(request.referrer or url_for('home'))

        # Extract data payload whether nested under 'data' or directly in root
        if 'data' in parsed_json and isinstance(parsed_json['data'], dict):
            data_dict = parsed_json['data']
        else:
            data_dict = {k: v for k, v in parsed_json.items() if not k.startswith('_') and isinstance(v, list)}

        if not data_dict:
            flash('No valid table data found in the backup file.', 'error')
            return redirect(request.referrer or url_for('home'))

        clear_existing = request.form.get('clear_existing', 'true').lower() in ('true', '1', 'yes', 'on')

        # 1. Attempt atomic PostgreSQL restore via Supabase RPC stored procedure
        restore_result = None
        try:
            rpc_payload = {'data': data_dict}
            res = supabase.rpc('restore_database_json', {
                'payload': rpc_payload,
                'clear_existing': clear_existing
            }).execute()
            if res and hasattr(res, 'data') and res.data:
                restore_result = res.data
        except Exception as rpc_exc:
            logging.warning(f"RPC restore_database_json failed ({rpc_exc}), using client-side restore.")

        # 2. Fallback to client-side ordered restore if RPC was unavailable
        if not restore_result:
            try:
                restore_result = _execute_client_side_restore(data_dict, clear_existing=clear_existing)
            except Exception as client_exc:
                logging.exception('Client-side restore error: %s', client_exc)
                flash(f'Database restore failed: {client_exc}', 'error')
                return redirect(request.referrer or url_for('home'))

        total_restored = restore_result.get('total_restored', 0)
        table_summary = restore_result.get('details', {})
        details_str = ", ".join(f"{k}: {v}" for k, v in table_summary.items() if v)

        performer = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip() or session.get('username', 'unknown')
        logging.info('Database restore performed by %s from file %s (%s total records)', performer, original_filename, total_restored)
        log_activity('restore', 'database', f"{original_filename} ({total_restored} records: {details_str})")

        flash(f'Database restored successfully! Restored {total_restored} total records ({details_str}).', 'success')
        return redirect(request.referrer or url_for('home'))

    except json.JSONDecodeError as jde:
        logging.error('JSON parse error during restore: %s', jde)
        flash(f'Invalid JSON syntax in backup file: {jde}', 'error')
        return redirect(request.referrer or url_for('home'))
    except Exception as exc:
        logging.exception('Unexpected restore error: %s', exc)
        flash(f'An unexpected error occurred during restore: {exc}', 'error')
        return redirect(request.referrer or url_for('home'))


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1').lower() in ('1', 'true', 'yes')
    app.run(host=host, port=port, debug=debug)
