from flask import Flask, request, render_template, redirect, url_for, session, jsonify, flash, send_file, abort, has_request_context
from datetime import timedelta, datetime, timezone
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
import re
import io
from collections import Counter

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client, Client
from postgrest.exceptions import APIError
from pdf_export import generate_timetable_pdf
from excel_export import (
    generate_timetable_excel,
    EXCEL_THEMES,
    get_section_theme,
    set_section_theme,
    get_all_section_themes
)
import professor_load_importer

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

# Shared Constants
SEMESTER_CHOICES = ["1st Semester", "2nd Semester"]
SPECIALIZATION_CHOICES = ["Database Systems", "Web Systems", "Networking", "General"]
SPECIALIZED_TERMS = [(3, "2nd Semester"), (4, "1st Semester"), (4, "2nd Semester")]

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

def _ranking_constraints(professor):
    """Return normalized teaching-load limits from a professor's academic ranking."""
    ranking = _rel(professor, 'academic_ranking') or {}
    def number(name, fallback):
        try:
            value = float(ranking.get(name, fallback))
            return value if value > 0 or name.startswith('min_') else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        'academic_ranking_id': professor.get('academic_ranking_id'),
        'academic_ranking_name': ranking.get('name') or '',
        'min_units': number('min_units', 0),
        'max_units': number('max_units', 24),
        'min_hours': number('min_hours', 0),
        'max_hours': number('max_hours', 40),
    }

def _user_to_dict(user):
    """Convert a database row or user object into the dict shape the templates expect."""
    if isinstance(user, dict):
        email = user.get('email') or ''
        username = user.get('username') or (email.split('@')[0] if '@' in email else email)
        prog_name = user.get('program_name') or user.get('program') or ''
        if not prog_name and isinstance(user.get('program'), dict):
            prog_name = user['program'].get('program_name') or ''
        return {
            'id': user.get('id'),
            'email': email,
            'username': username,
            'first_name': user.get('first_name', '') or '',
            'last_name': user.get('last_name', '') or '',
            'program_id': user.get('program_id'),
            'program': prog_name,
            'role': user.get('role', 'Scheduler') or 'Scheduler',
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
        'program_id': metadata.get('program_id'),
        'program': metadata.get('program', ''),
        'role': metadata.get('role', 'Scheduler') or 'Scheduler',
        'profile_picture': metadata.get('profile_picture'),
    }


def _list_users():
    """Return all users from public.users as template-ready dicts."""
    try:
        res = supabase.table('users').select('*, program:program_id(id, program_name)').execute()
        raw_users = res.data or []
    except Exception:
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

from werkzeug.exceptions import HTTPException

@app.errorhandler(HTTPException)
def handle_import_http_exception(e):
    if request.path.startswith('/professor_load/import'):
        code = getattr(e, 'code', 500)
        desc = getattr(e, 'description', str(e))
        return jsonify({'ok': False, 'success': False, 'error': desc}), code
    return e

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def _normalize_role(role_val):
    if not role_val:
        return ''
    r = str(role_val).strip().lower().replace(' ', '_').replace('-', '_')
    if r in ('super_admin', 'superadmin', 'administrator', 'admin'):
        return 'admin'
    if r in ('scheduler', 'dean', 'chair', 'dean/chair', 'program_chair', 'department_head'):
        return 'scheduler'
    if r in ('viewer', 'instructor', 'guest'):
        return 'viewer'
    return r


def _is_ajax_or_api_request():
    """Detect whether this request expects a JSON or API response."""
    if request.is_json:
        return True
    path = request.path.lower()
    if path.startswith('/api/') or '/import/' in path or path.startswith('/admin/delete_requests/'):
        return True
    if path.startswith('/search_'):
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = (request.headers.get('Accept') or '').lower()
    if 'application/json' in accept:
        return True
    # If method is not GET/HEAD, frontend forms/fetch expect JSON or direct action response
    if request.method not in ('GET', 'HEAD'):
        return True
    # Specific write endpoints that might accept GET (like /delete_course/<id>)
    if any(path.startswith(prefix) for prefix in ('/delete_', '/restore_', '/archive_', '/edit_', '/add_')):
        return True
    return False


def _is_ajax_request():
    """Detect whether a specific endpoint call was triggered via AJAX/fetch expecting JSON."""
    return (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '').lower()
    )


def roles_required(*allowed_roles):
    """
    Role-Based Access Control decorator.
    Enforces authorization on routes according to the system permission matrix.
    If unauthorized:
      - API / AJAX / write request: returns JSON {'ok': False, 'error': 'Access denied'} with status 403.
      - HTML page navigation: renders templates/403.html with status 403.
    """
    flat_roles = []
    for r in allowed_roles:
        if isinstance(r, (list, tuple, set)):
            flat_roles.extend(r)
        else:
            flat_roles.append(r)
    normalized_allowed = {_normalize_role(r) for r in flat_roles}

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                if _is_ajax_or_api_request():
                    return jsonify({'ok': False, 'error': 'Authentication required'}), 401
                return redirect(url_for('login'))

            raw_role = session.get('role', '')
            user_role = _normalize_role(raw_role)

            if user_role not in normalized_allowed:
                logging.warning(
                    f"RBAC Access Denied: user_id={session.get('user_id')} role='{raw_role}' "
                    f"path='{request.path}' method='{request.method}' allowed={normalized_allowed}"
                )
                if _is_ajax_or_api_request():
                    return jsonify({'ok': False, 'error': 'Access denied'}), 403
                return render_template('403.html', active_page='403'), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def role_required(allowed_roles):
    """Backwards compatibility wrapper for role_required."""
    return roles_required(*allowed_roles)


def admin_required(f):
    return roles_required('admin')(f)


def super_admin_required(f):
    return roles_required('admin')(f)


def scheduler_required(f):
    return roles_required('scheduler')(f)


def scheduler_only_required(f):
    return roles_required('scheduler')(f)


def dean_required(f):
    return roles_required('scheduler')(f)


def _get_programs():
    """Return all programs from public.program.

    Result is cached on Flask's request context (g) so at most one DB query
    is made per HTTP request, even if called many times.
    """
    from flask import g as _flask_g, has_app_context
    if has_app_context():
        cached = getattr(_flask_g, '_programs_cache', None)
        if cached is not None:
            return cached
    try:
        res = supabase.table('program').select('*').order('program_name').execute()
        programs = res.data or []
    except Exception as err:
        logging.debug(f"Failed to fetch programs: {err}")
        programs = []
    if has_app_context():
        try:
            _flask_g._programs_cache = programs
        except Exception:
            pass
    return programs

def _find_program_id_by_name(program_name):
    """Resolve a program name or numeric ID to public.program.id."""
    value = str(program_name or '').strip()
    if not value:
        return None

    for program in _get_programs():
        if value.isdigit() and str(program.get('id')) == value:
            return program.get('id')
        if str(program.get('program_name') or '').strip().casefold() == value.casefold():
            return program.get('id')
    return None

def _get_user_program_id(user_id=None):
    """Return the program_id for the current user or specified user_id."""
    has_ctx = has_request_context()
    if has_ctx and not user_id and session.get('program_id'):
        return session.get('program_id')

    uid = user_id or (session.get('user_id') if has_ctx else None)
    if uid:
        try:
            res = supabase.table('users').select('program_id').eq('id', str(uid)).limit(1).execute()
            if res.data and res.data[0].get('program_id'):
                pid = res.data[0]['program_id']
                if has_ctx and not user_id:
                    session['program_id'] = pid
                return pid
        except Exception as err:
            logging.debug(f"Error fetching user program_id: {err}")

    # Fallback by program name in session
    prog_name = session.get('program') if has_ctx else None
    if prog_name:
        try:
            res = supabase.table('program').select('id').eq('program_name', prog_name).limit(1).execute()
            if res.data and res.data[0].get('id'):
                pid = res.data[0]['id']
                if has_ctx and not user_id:
                    session['program_id'] = pid
                return pid
        except Exception:
            pass

    return None

def _get_active_semester(program_id=None):
    """Return the currently active semester record, optionally scoped by program_id."""
    try:
        q = supabase.table('semester').select('*, program:program_id(program_name)').eq('is_active', True)
        if program_id:
            q = q.eq('program_id', program_id)
        res = q.order('updated_at', desc=True).limit(1).execute()
        if res.data and len(res.data) > 0:
            sem = res.data[0]
            prog = _rel(sem, 'program') or {}
            sem['program_name'] = prog.get('program_name') or ''
            return sem

        # Fallback: if no active semester specifically for this program_id, fetch any active semester
        res_any = supabase.table('semester').select('*, program:program_id(program_name)').eq('is_active', True).limit(1).execute()
        if res_any.data and len(res_any.data) > 0:
            sem = res_any.data[0]
            prog = _rel(sem, 'program') or {}
            sem['program_name'] = prog.get('program_name') or ''
            return sem
    except Exception as err:
        logging.debug(f"Error fetching active semester: {err}")
    return None

def _get_semesters(program_id=None):
    """Return all semester records, optionally filtered by program_id."""
    try:
        q = supabase.table('semester').select('*, program:program_id(program_name)')
        if program_id:
            q = q.eq('program_id', program_id)
        res = q.order('school_year', desc=True).order('term').execute()
        rows = res.data or []
        for r in rows:
            prog = _rel(r, 'program') or {}
            r['program_name'] = prog.get('program_name') or ''
        return rows
    except Exception as err:
        logging.debug(f"Error fetching semesters: {err}")
        return []

def _get_section_configs(semester_id):
    """Return section configs for a semester as a dict mapping year_level -> config dict."""
    if not semester_id:
        return {}
    try:
        res = supabase.table('section_config').select('*').eq('semester_id', semester_id).execute()
        configs = {}
        for r in (res.data or []):
            yl = int(r.get('year_level') or 1)
            configs[yl] = r
        return configs
    except Exception as err:
        logging.debug(f"Error fetching section configs: {err}")
        return {}


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

def _set_delete_request_status(match_dict, status='approved'):
    """Helper to update delete_requests status, is_read, and updated_at."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        q = supabase.table('delete_requests').update({
            'status': status,
            'is_read': False,
            'updated_at': now_iso
        })
        for k, v in match_dict.items():
            q = q.eq(k, v)
        q.execute()
    except Exception:
        try:
            q = supabase.table('delete_requests').update({'status': status})
            for k, v in match_dict.items():
                q = q.eq(k, v)
            q.execute()
        except Exception as err:
            logging.debug(f"Failed to update delete_requests status: {err}")

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
        'course_id, schedule(day, class_start, class_end, professor_load_id, professor_load(course_id, course(course_name)))'
    ).eq('student_id', student_id).execute()

    existing_entries = []
    for row in (res.data or []):
        sch = _rel(row, 'schedule') or {}
        pc = _rel(sch, 'professor_load') or {}
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
        if (user_role or '').lower() in ('admin', 'super_admin'):
            res = supabase.table('delete_requests').select('*', count='exact').eq('status', 'pending').execute()
            context['pending_delete_requests_count'] = res.count if res.count is not None else 0

            pending = supabase.table('delete_requests').select(
                'id, user_id, username, first_name, last_name, item_type, item_id, item_details, status, created_at'
            ).eq('status', 'pending').order('created_at', desc=True).execute()
            context['pending_delete_requests'] = _normalize_created_at(pending.data or [])

        # Derive notifications directly from delete_requests (Zero-Table)
        try:
            n_res = supabase.table('delete_requests').select('*', count='exact').eq('user_id', str(user_id)).in_('status', ['approved', 'rejected']).eq('is_read', False).execute()
            context['unread_notifications_count'] = n_res.count if n_res.count is not None else 0

            notifs = supabase.table('delete_requests').select(
                'id, user_id, item_details, status, is_read, created_at, updated_at'
            ).eq('user_id', str(user_id)).in_('status', ['approved', 'rejected']).order('updated_at', desc=True).limit(50).execute()
            rows = notifs.data or []
        except Exception:
            # Fallback if updated_at / is_read columns are pending migration
            notifs = supabase.table('delete_requests').select(
                'id, user_id, item_details, status, created_at'
            ).eq('user_id', str(user_id)).in_('status', ['approved', 'rejected']).order('created_at', desc=True).limit(50).execute()
            rows = notifs.data or []
            context['unread_notifications_count'] = len(rows)

        derived_notifs = []
        for r in rows:
            st = r.get('status') or 'processed'
            details = r.get('item_details') or 'item'
            derived_notifs.append({
                'id': r.get('id'),
                'request_id': r.get('id'),
                'message': f"Admin {st} the deletion of {details}.",
                'status': st,
                'is_read': r.get('is_read', False),
                'created_at': r.get('updated_at') or r.get('created_at'),
            })
        context['scheduler_notifications'] = _normalize_created_at(derived_notifs)
    except Exception:
        pass

    return context

def _request_delete_if_scheduler(item_type, item_id, item_details):
    role = (session.get('role') or '').lower()
    if role == 'scheduler':
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
            elif item_type == 'academic_ranking':
                res = supabase.table('academic_ranking').select('name, program_id, program:program_id(program_name)').eq('academic_ranking_id', item_id).execute()
                ar_row = _first(res.data or [])
                if ar_row and ar_row.get('name'):
                    prog_lbl = (_rel(ar_row, 'program') or {}).get('program_name') or ar_row.get('program') or ''
                    details_str = f"Academic Ranking {ar_row['name']} ({prog_lbl}) (ID: {item_id})"

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

#-------------------------------------------------------FALLBACK_PROFESSOR_HELPERS-----------------------------------------------------------------------------------------
def _index_to_letter(index):
    """Convert 0-based integer index to alphabetical string (0->A, 1->B, 25->Z, 26->AA)."""
    result = ""
    while index >= 0:
        result = chr(index % 26 + ord('A')) + result
        index = index // 26 - 1
    return result


# Per-request cache for fallback professor lookups to avoid repeated DB round-trips
# within a single schedule generation run. Cleared implicitly each request via the
# _clear_preview_generation_state() call at the start of each generate_schedule run.
_fallback_prof_cache: dict = {}


def _ensure_fallback_professor_by_index(index=0, department=None):
    """Fallback professors are prohibited. Return None without querying or inserting records."""
    return None


def _ensure_fallback_professor(department=None):
    """Fallback professors are prohibited. Return None without querying or inserting records."""
    return None

#-------------------------------------------------------PROGRAM_TO_DEPARTMENT----------------------------------------------------------------------------------------------
def _get_department(program=None):
    """Dynamically fetch the department associated with a program from the program_department table in Supabase.

    1. Retrieves the user's program from the argument, session, or the users table.
    2. For Admin role without program specified, returns None without error.
    3. Queries the program_department table using the Supabase Python SDK.
    4. Returns the matching department_name, or None if not found.
    """
    has_ctx = has_request_context()
    user_role = (session.get('role') or '').lower() if has_ctx else ''
    user_program = program or (session.get('program') if has_ctx else None)
    if not user_program and has_ctx and session.get('user_id'):
        try:
            res = supabase.auth.get_user(session.get('jwt_token'))
            if res and res.user:
                metadata = getattr(res.user, 'user_metadata', None) or {}
                user_program = metadata.get('program')
                if user_program:
                    session['program'] = user_program
        except Exception as err:
            logging.error(f"Error retrieving program for user_id {session.get('user_id')}: {err}")
        if not user_program:
            try:
                u_res = supabase.table('users').select('program_id, program:program_id(program_name)').eq('id', session.get('user_id')).single().execute()
                if u_res.data:
                    u_prog = (_rel(u_res.data, 'program') or {}).get('program_name') or u_res.data.get('program')
                    if u_prog:
                        user_program = u_prog
                        session['program'] = user_program
            except Exception:
                pass

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
    session.pop('unscheduled_loads', None)
    session.modified = True
    # Clear per-run caches so stale results from a previous generation don't carry over
    _fallback_prof_cache.clear()


def _major_matches(course_major, selected_major):
    if not selected_major:
        return True
    if not course_major:
        return True

    normalized_course = str(course_major).strip().lower()
    normalized_selected = str(selected_major).strip().lower()

    if normalized_course in ['general', 'none', '', 'all']:
        return True

    track_groups = [
        {'database systems', 'database system', 'database', 'dst', '(dst)'},
        {'web systems', 'web system', 'web development', 'web dev', 'web', 'wst', '(wst)'},
        {'networking', 'network', 'net', 'nst', '(nst)'},
        {'general', 'none', '', 'all'}
    ]

    for grp in track_groups:
        course_in_grp = normalized_course in grp or any(alias in normalized_course for alias in ['(wst)', '(dst)', '(nst)'] if alias in grp)
        selected_in_grp = normalized_selected in grp or any(alias in normalized_selected for alias in ['(wst)', '(dst)', '(nst)'] if alias in grp)
        if course_in_grp and selected_in_grp:
            return True

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
                    'year_level': _year_of_section(section_name),
                    'theme': get_section_theme(section_name),
                },
                'entries': []
            }
        sections_by_key[key]['entries'].append(entry)

    sections_with_entries = list(sections_by_key.values())
    year_groups = _group_preview_sections(sections_with_entries)

    courses = []
    rooms = []
    professor_load_assignments = {}
    try:
        user_role = (session.get('role') or 'scheduler').lower()
        is_super_admin = user_role in ('super_admin', 'admin')
        user_prog_id = _get_user_program_id()
        program = session.get('program', '')

        # Super Admin sees all courses, Dean and Scheduler see their program's courses
        c_query = supabase.table('course').select('course_id, course_name').order('course_name')
        if not is_super_admin:
            eff_pid = user_prog_id or _find_program_id_by_name(program)
            if eff_pid:
                c_query = c_query.eq('program_id', eff_pid)
        courses_res = c_query.execute()
        courses = courses_res.data or []

        # Super Admin sees all rooms, Dean and Scheduler see their program's rooms
        r_query = supabase.table('room').select('room_id, room_name').order('room_name')
        if not is_super_admin and user_prog_id:
            try:
                r_query = r_query.eq('program_id', user_prog_id)
            except Exception:
                pass
        rooms_res = r_query.execute()
        rooms = rooms_res.data or []

        # Professor load assignments
        professor_loads = []
        pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, prof_id, professor(prof_id, first_name, last_name), course(course_id, course_name, program_id)').execute()
        assignments = pc_res.data or []
        for row in assignments:
            p = _rel(row, 'professor') or {}
            c = _rel(row, 'course') or {}
            prof_name = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or ''
            prof_entry = {'id': p.get('prof_id'), 'name': prof_name}
            if row.get('course_id'):
                course_key = str(row['course_id'])
                professor_load_assignments.setdefault(course_key, []).append(prof_entry)
            c_name = (c.get('course_name') if c else None) or f"Course #{row.get('course_id')}"
            professor_loads.append({
                'professor_load_id': row.get('professor_load_id'),
                'prof_id': p.get('prof_id'),
                'course_id': row.get('course_id'),
                'prof_name': prof_name,
                'course_name': c_name,
                'label': f"{prof_name} - {c_name}",
            })
        professor_loads.sort(key=lambda x: (x['prof_name'].lower(), x['course_name'].lower()))
    except Exception:
        courses = []
        rooms = []
        professor_load_assignments = {}
        professor_loads = []

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
        'professor_load_assignments': professor_load_assignments,
        'all_professor_loads': professor_loads,
        'room_utilization': room_utilization,
        'unscheduled_loads': session.get('unscheduled_loads', []),
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
@roles_required('scheduler')
def registrar_counts():
    """Return registrar mock data for year levels as JSON."""
    year_level = request.args.get('year_level', '')
    semester = request.args.get('semester', '')
    if year_level and year_level.lower() not in ('all', 'batch'):
        return jsonify(_get_registrar_counts_for_year(year_level, semester=semester))
    return jsonify(_generate_mock_registrar_data(semester=semester))


def calculate_semester_section_counts(program_id=None, semester=None, department=None):
    """Calculates section counts per course and year level derived strictly from professor_load.
    Validates section count consistency across courses in each year level and specialization group.
    Enforces General courses total_sections == sum of specialization groups.
    Enforces active schedule rule (same semester blocks generation).
    """
    if not semester or semester not in SEMESTER_CHOICES:
        return {
            'valid': False,
            'errors': ['Please select a valid semester (1st Semester or 2nd Semester).'],
            'breakdown': [],
            'active_schedule': {'exists': False, 'same_semester': False}
        }

    errors = []
    warnings = []

    # 1. Fetch curriculum courses for this semester & program
    try:
        c_query = supabase.table('course').select(
            'course_id, course_name, year_level, semester, specialization, major, program_id, units, lecture_hours, lab_hours, ilp_hours'
        ).eq('semester', semester)
        if program_id:
            c_query = c_query.eq('program_id', program_id)
        courses_data = c_query.execute().data or []
    except Exception:
        try:
            c_query = supabase.table('course').select('*').eq('semester', semester)
            if program_id:
                c_query = c_query.eq('program_id', program_id)
            courses_data = c_query.execute().data or []
        except Exception:
            courses_data = []

    if not courses_data and program_id:
        try:
            fb_c = supabase.table('course').select('*').eq('semester', semester).execute().data or []
            if fb_c:
                courses_data = [c for c in fb_c if c.get('program_id') is None or str(c.get('program_id')) == str(program_id)]
        except Exception:
            pass

    if not courses_data:
        return {
            'valid': False,
            'errors': [f'No courses found for {semester} in this program.'],
            'breakdown': [],
            'active_schedule': {'exists': False, 'same_semester': False}
        }

    # 2. Fetch professor_load section counts per course
    try:
        pl_query = supabase.table('professor_load').select('id, prof_id, course_id, sections')
        pl_data = pl_query.execute().data or []
    except Exception:
        pl_data = []

    pl_by_course = {}
    for pl in pl_data:
        cid = pl.get('course_id')
        sec = int(pl.get('sections') or 0)
        pl_by_course.setdefault(cid, []).append({
            'prof_id': pl.get('prof_id'),
            'sections': sec
        })

    # Group courses by year_level
    courses_by_year = {}
    for c in courses_data:
        try:
            yl = int(c.get('year_level') or 1)
        except (ValueError, TypeError):
            yl = 1
        courses_by_year.setdefault(yl, []).append(c)

    breakdown = []
    year_names = {1: '1st Year', 2: '2nd Year', 3: '3rd Year', 4: '4th Year'}

    for yl in sorted(courses_by_year.keys()):
        yl_courses = courses_by_year[yl]
        yl_name = year_names.get(yl, f'{yl}th Year')
        is_spec_term = (yl, semester) in SPECIALIZED_TERMS

        if not is_spec_term:
            # Regular (non-specialized) term
            course_counts = {}
            course_details = []
            for c in yl_courses:
                cid = c['course_id']
                cname = c['course_name']
                loads = pl_by_course.get(cid, [])
                tot_sec = sum(l['sections'] for l in loads)
                course_counts[cname] = tot_sec
                course_details.append({
                    'course_id': cid,
                    'course_name': cname,
                    'sections': tot_sec,
                    'units': c.get('units', 0),
                    'loads': loads
                })
                if tot_sec == 0:
                    errors.append(f"{cname} has no assigned load")

            # Check consistency: all courses in this year level must have the exact same section count
            unique_counts = set(course_counts.values())
            if len(unique_counts) > 1:
                mismatch_items = [f"{cname} has {cnt} sections" for cname, cnt in course_counts.items()]
                errors.append(f"{yl_name}: mismatched section counts - " + ", ".join(mismatch_items))

            first_count = next(iter(course_counts.values())) if course_counts else 0
            is_uniform = len(unique_counts) == 1 and first_count > 0
            sec_names = [f"{yl}{chr(65 + i)}" for i in range(first_count)] if is_uniform else []

            breakdown.append({
                'year_level': yl,
                'year_label': yl_name,
                'is_specialized': False,
                'section_count': first_count if is_uniform else 0,
                'section_names': sec_names,
                'courses': course_details
            })
        else:
            # Specialized term: courses carry specialization (Database Systems, Web Systems, Networking, General)
            spec_groups = {'Database Systems': [], 'Web Systems': [], 'Networking': []}
            general_courses = []

            for c in yl_courses:
                spec = (c.get('specialization') or c.get('major') or '').strip()
                if spec in spec_groups:
                    spec_groups[spec].append(c)
                elif spec == 'General':
                    general_courses.append(c)
                else:
                    errors.append(f"{c['course_name']} in {yl_name} {semester} has invalid or missing specialization ('{spec}')")

            group_counts = {}
            group_breakdown = []
            all_spec_section_names = []

            for spec_name, group_c_list in spec_groups.items():
                if not group_c_list:
                    continue
                grp_counts = {}
                grp_details = []
                for c in group_c_list:
                    cid = c['course_id']
                    cname = c['course_name']
                    loads = pl_by_course.get(cid, [])
                    tot_sec = sum(l['sections'] for l in loads)
                    grp_counts[cname] = tot_sec
                    grp_details.append({
                        'course_id': cid,
                        'course_name': cname,
                        'sections': tot_sec,
                        'units': c.get('units', 0),
                        'loads': loads
                    })
                    if tot_sec == 0:
                        errors.append(f"{cname} has no assigned load")

                unique_grp_counts = set(grp_counts.values())
                if len(unique_grp_counts) > 1:
                    mismatch_items = [f"{cname} has {cnt} sections" for cname, cnt in grp_counts.items()]
                    errors.append(f"{yl_name} ({spec_name}): mismatched section counts - " + ", ".join(mismatch_items))

                # If all courses in the track have the same section count, use that count.
                # If there's an internal mismatch, use the representative count (most frequent or max)
                # so the General course check computes the actual expected track total instead of collapsing to 0.
                if len(unique_grp_counts) == 1:
                    grp_sec_cnt = next(iter(unique_grp_counts))
                elif grp_counts:
                    counts_freq = Counter(grp_counts.values())
                    grp_sec_cnt = sorted(counts_freq.keys(), key=lambda k: (counts_freq[k], k), reverse=True)[0]
                else:
                    grp_sec_cnt = 0

                is_grp_uniform = len(unique_grp_counts) == 1 and grp_sec_cnt > 0
                group_counts[spec_name] = grp_sec_cnt

                sec_names = [f"{yl}{chr(65 + i)}-{spec_name}" for i in range(grp_sec_cnt)] if is_grp_uniform else []
                all_spec_section_names.extend(sec_names)

                group_breakdown.append({
                    'specialization': spec_name,
                    'section_count': group_counts[spec_name],
                    'section_names': sec_names,
                    'courses': grp_details
                })

            # General courses validation: must equal sum of all specialization groups
            required_general_total = sum(group_counts.values())
            gen_details = []
            for c in general_courses:
                cid = c['course_id']
                cname = c['course_name']
                loads = pl_by_course.get(cid, [])
                tot_sec = sum(l['sections'] for l in loads)
                gen_details.append({
                    'course_id': cid,
                    'course_name': cname,
                    'sections': tot_sec,
                    'units': c.get('units', 0),
                    'loads': loads
                })
                if tot_sec == 0:
                    errors.append(f"{cname} (General) has no assigned load")
                elif tot_sec != required_general_total:
                    track_descs = []
                    for s_name in spec_groups.keys():
                        if s_name in group_counts:
                            s_courses = spec_groups[s_name]
                            c_items = [f"{gc['course_name']}: {sum(l['sections'] for l in pl_by_course.get(gc['course_id'], []))}" for gc in s_courses]
                            track_descs.append(f"{s_name}: {group_counts[s_name]} ({', '.join(c_items)})")
                    spec_sum_desc = " + ".join(track_descs)
                    errors.append(
                        f"{yl_name} {semester}: General course {cname} has {tot_sec} sections, but requires {required_general_total} sections (sum of {spec_sum_desc})"
                    )

            breakdown.append({
                'year_level': yl,
                'year_label': yl_name,
                'is_specialized': True,
                'specialization_groups': group_breakdown,
                'general_total_required': required_general_total,
                'general_courses': gen_details,
                'all_section_names': all_spec_section_names
            })

    # Check active schedule status for program
    prog_name = None
    if has_request_context():
        prog_name = session.get('program')

    sched_q = supabase.table('schedule').select('schedule_id, semester, program_id').eq('archive', False)
    if program_id:
        try:
            sched_q = sched_q.eq('program_id', program_id)
        except Exception:
            pass
    try:
        sched_rows = sched_q.execute().data or []
    except Exception:
        try:
            sched_rows = supabase.table('schedule').select('*').eq('archive', False).execute().data or []
        except Exception:
            sched_rows = []

    if program_id:
        sched_rows = [r for r in sched_rows if r.get('program_id') is None or str(r.get('program_id')) == str(program_id)]
    elif prog_name:
        sched_rows = [r for r in sched_rows if not r.get('program') or str(r.get('program') or '').strip().upper() == str(prog_name).strip().upper()]

    active_info = {'exists': False, 'same_semester': False, 'semester': None, 'count': 0}
    if sched_rows:
        active_semesters = list({r.get('semester') for r in sched_rows if r.get('semester')})
        has_same_sem = semester in active_semesters
        display_sem = semester if has_same_sem else (active_semesters[0] if active_semesters else None)
        active_info = {
            'exists': True,
            'same_semester': has_same_sem,
            'semester': display_sem,
            'count': len(sched_rows)
        }
        if has_same_sem:
            errors.append(f"An active schedule for {semester} already exists. Archive it first to generate a new one.")

    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'breakdown': breakdown,
        'active_schedule': active_info
    }


@app.route('/api/semester-section-counts')
@roles_required('scheduler')
def api_semester_section_counts():
    """Return calculated section counts and validation status derived strictly from professor_load."""
    semester = request.args.get('semester', '').strip()
    user_prog_id = _get_user_program_id()
    department = _get_department()

    try:
        result = calculate_semester_section_counts(program_id=user_prog_id, semester=semester, department=department)
        return jsonify(result)
    except Exception as err:
        logging.error(f"Error in api_semester_section_counts: {err}")
        return jsonify({
            'valid': False,
            'errors': [f"Calculation error: {err}"],
            'breakdown': [],
            'active_schedule': {'exists': False, 'same_semester': False}
        }), 200
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
                    session['role'] = metadata.get('role', 'scheduler')

                    # Refresh role, program_id, and program directly from database profile
                    try:
                        u_res = supabase.table('users').select('*, program:program_id(id, program_name)').eq('id', str(auth_user.id)).limit(1).execute()
                        if u_res.data and len(u_res.data) > 0:
                            u_row = u_res.data[0]
                            if u_row.get('role'):
                                session['role'] = u_row['role']
                            if u_row.get('program_id'):
                                session['program_id'] = u_row['program_id']
                            p_rel = _rel(u_row, 'program') or {}
                            if p_rel.get('program_name'):
                                session['program'] = p_rel['program_name']
                            if u_row.get('first_name'):
                                session['first_name'] = u_row['first_name']
                            if u_row.get('last_name'):
                                session['last_name'] = u_row['last_name']
                            if u_row.get('username'):
                                session['username'] = u_row['username']
                    except Exception as u_err:
                        logging.debug(f"Could not load public.users profile: {u_err}")

                    if not session.get('program_id'):
                        _get_user_program_id(auth_user.id)

                    log_activity('login', 'auth', session['username'])

                    # Redirect users to appropriate starting page
                    norm_role = _normalize_role(session.get('role', ''))
                    if norm_role in ('admin', 'viewer'):
                        return redirect(url_for('schedules'))
                    return redirect(url_for('home'))
            except Exception as err:
                err_str = str(err)
                logging.error(f"Login error for '{identifier}': {err_str}")
                if 'database error' in err_str.lower():
                    error = 'Database trigger error during authentication. Please run the migration fix script in Supabase.'
                else:
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

                # Resolve program_id from program name
                prog_id = None
                try:
                    p_res = supabase.table('program').select('id').eq('program_name', program).limit(1).execute()
                    if p_res.data:
                        prog_id = p_res.data[0]['id']
                except Exception:
                    pass

                res = supabase.auth.sign_up({
                    'email': effective_email,
                    'password': password,
                    'options': {
                        'data': {
                            'first_name': first_name,
                            'last_name': last_name,
                            'username': effective_username,
                            'program': program,
                            'program_id': prog_id,
                            'role': 'scheduler',
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
@roles_required('admin', 'scheduler', 'viewer')
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
@roles_required('admin', 'scheduler', 'viewer')
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
@admin_required
def add_user_columns():
    # Schema migration is handled in Supabase; no runtime DDL required.
    return "Columns are part of the Supabase schema. You can close this page."

@app.route('/set_admin_role/<email>')
@admin_required
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

    programs = _get_programs()
    return render_template('users.html', active_page='users', users=users_list, programs=programs, search_query=search_query)

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
            new_role = request.form.get('role', '').strip().lower()
            if new_role not in ('admin', 'super_admin'):
                return jsonify({'success': False, 'message': 'You cannot remove your own administrator access.'}), 403

        # Update user
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', '').strip()
        is_global_admin = role.lower() in ('admin', 'super_admin')

        program_input = request.form.get('program', '').strip() if not is_global_admin else None
        program_id_input = request.form.get('program_id', '').strip() if not is_global_admin else None

        if not is_global_admin and not (program_input or program_id_input):
            return jsonify({'success': False, 'message': 'Program is required for Scheduler role.'}), 400

        # Resolve program and program_id
        programs = _get_programs()
        program = program_input
        program_id = None
        if program_id_input:
            try:
                program_id = int(program_id_input)
                for p in programs:
                    if p.get('id') == program_id:
                        program = p.get('program_name')
                        break
            except Exception:
                pass
        elif program_input:
            for p in programs:
                if str(p.get('program_name')).upper() == program_input.upper():
                    program_id = p.get('id')
                    program = p.get('program_name')
                    break

        update_payload = {
            'first_name': first_name,
            'last_name': last_name,
            'role': role,
        }
        if program_id is not None:
            update_payload['program_id'] = program_id
        if email:
            update_payload['email'] = email

        try:
            res = supabase.table('users').update(update_payload).eq('id', user_id).execute()
        except Exception:
            update_payload.pop('program_id', None)
            update_payload['program'] = program
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

        # Validate required fields
        if not first_name or not last_name or not email or not password or not role:
            return jsonify({'success': False, 'message': 'All required fields must be filled.'}), 400

        is_global_admin = role.lower() in ('admin', 'super_admin')
        program_input = request.form.get('program', '').strip() if not is_global_admin else None
        program_id_input = request.form.get('program_id', '').strip() if not is_global_admin else None

        if not is_global_admin and not (program_input or program_id_input):
            return jsonify({'success': False, 'message': 'Program is required for Scheduler role.'}), 400

        programs = _get_programs()
        program = program_input
        program_id = None
        if program_id_input:
            try:
                program_id = int(program_id_input)
                for p in programs:
                    if p.get('id') == program_id:
                        program = p.get('program_name')
                        break
            except Exception:
                pass
        elif program_input:
            for p in programs:
                if str(p.get('program_name')).upper() == program_input.upper():
                    program_id = p.get('id')
                    program = p.get('program_name')
                    break

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
                        'program_id': program_id,
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
            'role': role,
        }
        if program_id is not None:
            payload['program_id'] = program_id
        if user_id:
            payload['id'] = str(user_id)

        try:
            supabase.table('users').upsert(payload, on_conflict='email').execute()
        except Exception:
            payload.pop('program_id', None)
            payload['program'] = program
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
@admin_required
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

                v_prog_id = None
                try:
                    p_res = supabase.table('program').select('id').eq('program_name', account['program']).limit(1).execute()
                    if p_res.data:
                        v_prog_id = p_res.data[0]['id']
                except Exception:
                    pass

                payload = {
                    'email': account['email'],
                    'username': account['email'].split('@')[0],
                    'first_name': account['first_name'],
                    'last_name': account['last_name'],
                    'role': account['role'],
                }
                if v_prog_id is not None:
                    payload['program_id'] = v_prog_id
                if user_id:
                    payload['id'] = str(user_id)
                try:
                    supabase.table('users').upsert(payload, on_conflict='email').execute()
                except Exception:
                    payload.pop('program_id', None)
                    payload['program'] = account['program']
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
@roles_required('scheduler')
def home():
    semesters = ['1st Semester', '2nd Semester']
    return render_template('index.html', active_page='home', semesters=semesters)


@app.route('/index.html')
@roles_required('scheduler')
def legacy_index_html():
    return redirect(url_for('home'))


@app.route('/courses.html')
@roles_required('scheduler')
def legacy_courses_html():
    return redirect(url_for('show_courses'))


@app.route('/professors.html')
@roles_required('scheduler')
def legacy_professors_html():
    return redirect(url_for('professors'))


@app.route('/section.html')
@roles_required('admin', 'scheduler', 'viewer')
def legacy_section_html():
    return redirect(url_for('schedules'))


@app.route('/room.html')
@roles_required('scheduler')
def legacy_room_html():
    return redirect(url_for('rooms'))


@app.route('/timeslot.html')
@roles_required('scheduler')
def legacy_timeslot_html():
    return redirect(url_for('timeslot'))


@app.route('/schedules.html')
@roles_required('admin', 'scheduler', 'viewer')
def legacy_schedules_html():
    return redirect(url_for('schedules'))


@app.route('/generated_schedule.html')
@roles_required('admin', 'scheduler', 'viewer')
def legacy_generated_schedule_html():
    return redirect(url_for('schedules'))


@app.route('/schedule.html')
@roles_required('admin', 'scheduler', 'viewer')
def legacy_schedule_html():
    return redirect(url_for('schedules'))
#-------------------------------------------------------add_course----------------------------------------------------------------------------------------------
@app.route('/add_course', methods=['POST'])
@roles_required('scheduler')
def add_course():
    try:
        course_name = request.form['course_name']
        lecture_hours = request.form.get('lecture_hours', 0)
        lab_hours = request.form.get('lab_hours', 0)
        ilp_hours = request.form.get('ilp_hours', 0)
        units_raw = request.form.get('units', '').strip()
        try:
            units = float(units_raw) if units_raw else 0
            if units.is_integer():
                units = int(units)
        except (ValueError, TypeError):
            units = 0

        try:
            ilp_val = float(ilp_hours)
            if ilp_val not in (0.0, 1.0):
                session['course_message'] = 'ILP hours must be either 0 or 1.'
                session.modified = True
                return redirect(url_for('show_courses'))
        except (ValueError, TypeError):
            session['course_message'] = 'ILP hours must be a valid number.'
            session.modified = True
            return redirect(url_for('show_courses'))

        year_level = request.form.get('year_level', '')
        
        # Resolve program_id
        program_id = _get_user_program_id()
        program = session.get('program', '')

        # Resolve semester_id and semester name
        raw_sem_id = request.form.get('semester_id')
        raw_semester = request.form.get('semester', '').strip()
        semester_id = int(raw_sem_id) if raw_sem_id and str(raw_sem_id).isdigit() else None
        
        if not semester_id and not raw_semester:
            session['course_message'] = 'Semester is required.'
            session.modified = True
            return redirect(url_for('show_courses'))

        if not semester_id and raw_semester:
            try:
                term_search = '1st Semester' if '1' in raw_semester else ('2nd Semester' if '2' in raw_semester else raw_semester)
                q_sem = supabase.table('semester').select('id, term').ilike('term', f"%{term_search}%")
                if program_id:
                    q_sem = q_sem.eq('program_id', program_id)
                sem_res = q_sem.limit(1).execute()
                if sem_res.data:
                    semester_id = sem_res.data[0]['id']
            except Exception:
                pass

        semester_str = raw_semester or ('1st Semester' if not semester_id else '')
        major = request.form.get('major', '').strip() or None

        course_data = {
            'course_name': course_name,
            'units': units,
            'lecture_hours': int(lecture_hours) if lecture_hours else 0,
            'lab_hours': int(lab_hours) if lab_hours else 0,
            'ilp_hours': int(float(ilp_hours)) if ilp_hours else 0,
            'year_level': year_level,
            'major': major,
            'semester': semester_str,
        }
        if program_id:
            course_data['program_id'] = program_id

        try:
            supabase.table('course').insert(course_data).execute()
        except Exception:
            course_data.pop('semester', None)
            supabase.table('course').insert(course_data).execute()

        session.pop('course_message', None)
        log_activity('create', 'course', course_name)
        if _is_ajax_request():
            return jsonify({'success': True, 'message': 'Course created successfully.', 'course': course_data}), 200
        flash('Created successfully', 'success')
        return redirect(url_for('show_courses'))
    except Exception as err:
        logging.error(f"Error adding course: {err}")
        if _is_ajax_request():
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('show_courses'))
#-------------------------------------------------------show_courses----------------------------------------------------------------------------------------------
@app.route('/courses')
@roles_required('scheduler')
def show_courses():
    user_prog_id = _get_user_program_id()
    user_program = session.get('program', '')
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    program_filter = request.args.get('program', '').strip()
    _ensure_course_semester_column()

    eff_prog_id = None
    if program_filter and program_filter.lower() != 'all':
        eff_prog_id = int(program_filter) if program_filter.isdigit() else _find_program_id_by_name(program_filter)
    elif not is_super_admin:
        eff_prog_id = user_prog_id or _find_program_id_by_name(user_program)

    try:
        query = supabase.table('course').select('*, program:program_id(id, program_name)')
        if eff_prog_id:
            query = query.eq('program_id', eff_prog_id)
        all_courses = query.execute().data or []
    except Exception:
        query = supabase.table('course').select('*')
        if eff_prog_id:
            query = query.eq('program_id', eff_prog_id)
        all_courses = query.execute().data or []

    for c in all_courses:
        p_rel = _rel(c, 'program') or {}
        c['program_name'] = p_rel.get('program_name') or user_program or ''
        c['program'] = c['program_name']

    all_courses.sort(key=lambda c: (str(c.get('year_level') or ''), str(c.get('major') or ''), str(c.get('course_name') or '')))

    counts = {
        'all': len(all_courses),
        '1st': len([c for c in all_courses if str(c.get('year_level')) == '1']),
        '2nd': len([c for c in all_courses if str(c.get('year_level')) == '2']),
        '3rd': len([c for c in all_courses if str(c.get('year_level')) == '3']),
        '4th': len([c for c in all_courses if str(c.get('year_level')) == '4']),
    }

    semesters = _get_semesters(program_id=user_prog_id)
    course_message = session.pop('course_message', None)
    return render_template(
        'courses.html',
        active_page='courses',
        courses=all_courses,
        counts=counts,
        program=user_program,
        selected_program=program_filter,
        course_message=course_message,
        semesters=semesters,
    )

#-------------------------------------------------------search_courses----------------------------------------------------------------------------------------------
@app.route('/search_courses', methods=['GET'])
@roles_required('scheduler')
def search_courses():
    query_str = request.args.get('q', '').strip()
    user_program = session.get('program', '')
    user_role = (session.get('role') or 'scheduler').lower()
    program_filter = request.args.get('program', '').strip()
    user_prog_id = _get_user_program_id()

    if not query_str:
        return jsonify({'courses': [], 'exact_match': False})

    eff_prog_id = None
    if user_role in ('super_admin', 'admin'):
        if program_filter and program_filter.lower() != 'all':
            eff_prog_id = int(program_filter) if program_filter.isdigit() else _find_program_id_by_name(program_filter)
    else:
        eff_prog_id = user_prog_id or _find_program_id_by_name(user_program)

    try:
        cols = 'course_id, course_name, program_id, program:program_id(id, program_name), year_level, major, lecture_hours, lab_hours, ilp_hours, units'
        try:
            query = supabase.table('course').select(cols)
        except Exception:
            cols = 'course_id, course_name, program_id, year_level, major, lecture_hours, lab_hours, ilp_hours, units'
            query = supabase.table('course').select(cols)
        exact_query = supabase.table('course').select('course_id')

        if eff_prog_id:
            query = query.eq('program_id', eff_prog_id)
            exact_query = exact_query.eq('program_id', eff_prog_id)

        matching_courses = query.ilike('course_name', f'%{query_str}%').order('course_name').limit(10).execute().data or []
        exact = exact_query.ilike('course_name', query_str).limit(1).execute()
        exact_match_row = _first(exact.data or [])

        for c in matching_courses:
            p_rel = _rel(c, 'program') or {}
            c['program_name'] = p_rel.get('program_name') or user_program or ''
            c['program'] = c['program_name']

        return jsonify({
            'courses': matching_courses,
            'exact_match': exact_match_row is not None
        })
    except Exception as err:
        return jsonify({'error': str(err), 'courses': [], 'exact_match': False}), 500


@app.route('/api/courses')
@roles_required('scheduler')
def api_courses():
    user_role = (session.get('role') or 'scheduler').lower()
    user_program = session.get('program', '')
    user_prog_id = _get_user_program_id()
    program_filter = request.args.get('program', '').strip()
    year_level = request.args.get('year_level', '').strip()
    semester = request.args.get('semester', '').strip()
    major = request.args.get('major', '').strip()

    _ensure_course_semester_column()

    eff_prog_id = None
    if user_role in ('super_admin', 'admin'):
        if program_filter and program_filter.lower() != 'all':
            eff_prog_id = int(program_filter) if program_filter.isdigit() else _find_program_id_by_name(program_filter)
    else:
        eff_prog_id = user_prog_id or _find_program_id_by_name(user_program)

    try:
        try:
            query = supabase.table('course').select('course_id, course_name, program_id, program:program_id(id, program_name), year_level, major, semester, units, lecture_hours, lab_hours, ilp_hours')
        except Exception:
            query = supabase.table('course').select('course_id, course_name, program_id, year_level, major, semester, units, lecture_hours, lab_hours, ilp_hours')

        if eff_prog_id:
            query = query.eq('program_id', eff_prog_id)

        if year_level and year_level.lower() != 'all':
            query = query.eq('year_level', year_level)

        if semester:
            query = query.eq('semester', semester)

        if major:
            query = query.eq('major', major)

        courses = query.order('course_name').execute().data or []
        for c in courses:
            p_rel = _rel(c, 'program') or {}
            c['program_name'] = p_rel.get('program_name') or user_program or ''
            c['program'] = c['program_name']
        return jsonify({'courses': courses})
    except Exception as err:
        return jsonify({'error': str(err), 'courses': []}), 500


@app.route('/api/professor_load/<int:prof_id>')
@roles_required('scheduler')
def api_professor_load(prof_id):
    try:
        rows = []
        try:
            rows = supabase.table('professor_load').select('course_id, sections').eq('prof_id', prof_id).execute().data or []
        except Exception as e1:
            try:
                rows = supabase.table('professor_load').select('course_id').eq('prof_id', prof_id).execute().data or []
            except Exception as e2:
                try:
                    rows = supabase.table('prof_course').select('course_id, sections').eq('prof_id', prof_id).execute().data or []
                except Exception as e3:
                    rows = supabase.table('prof_course').select('course_id').eq('prof_id', prof_id).execute().data or []
        return jsonify({
            'prof_id': prof_id,
            'assignments': [
                {'course_id': row.get('course_id'), 'sections': int(row.get('sections') or 1)}
                for row in rows
            ],
        })
    except Exception as err:
        logging.error(f'Error fetching professor load for {prof_id}: {err}')
        return jsonify({'error': 'Unable to fetch professor load.', 'assignments': []}), 500

#-------------------------------------------------------delete_course----------------------------------------------------------------------------------------------
@app.route('/delete_course/<int:course_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_course(course_id):
    is_ajax = _is_ajax_request()
    try:
        # Data protection: Check if course is assigned in teaching loads
        try:
            load_check = supabase.table('professor_load').select('id').eq('course_id', course_id).limit(1).execute()
            if load_check.data:
                err_msg = 'Cannot delete course: this course is currently assigned in faculty teaching loads. Please remove the teaching load assignments first.'
                if is_ajax:
                    return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
                flash(err_msg, 'danger')
                return redirect(url_for('show_courses'))
        except Exception as e:
            logging.debug(f"Error checking course professor_load references: {e}")

        # Data protection: Check if course is assigned in active schedules
        try:
            sched_check = supabase.table('schedule').select('schedule_id').eq('course_id', course_id).eq('archive', False).limit(1).execute()
            if sched_check.data:
                err_msg = 'Cannot delete course: this course is assigned to active class schedules. Please remove schedule entries or archive the schedule first.'
                if is_ajax:
                    return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
                flash(err_msg, 'danger')
                return redirect(url_for('show_courses'))
        except Exception as e:
            logging.debug(f"Error checking course schedule references: {e}")

        supabase.table('course').delete().eq('course_id', course_id).execute()
        try:
            _set_delete_request_status({'item_type': 'course', 'item_id': str(course_id), 'status': 'pending'}, 'approved')
        except Exception:
            pass
        log_activity('delete', 'course', f'Course ID {course_id}')
        if is_ajax:
            return jsonify({'success': True, 'message': 'Course deleted successfully.'}), 200
        flash('Deleted successfully', 'success')
        return redirect(url_for('show_courses'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('show_courses'))

#-------------------------------------------------------edit_course----------------------------------------------------------------------------------------------
@app.route('/edit_course/<int:course_id>', methods=['POST'])
@roles_required('scheduler')
def edit_course(course_id):
    is_ajax = _is_ajax_request()
    try:
        data = request.get_json(silent=True) if request.is_json else None
        if not data:
            data = request.form or {}

        course_name = (data.get('course_name') or '').strip()
        lecture_hours = data.get('lecture_hours', 0)
        lab_hours = data.get('lab_hours', 0)
        ilp_hours = data.get('ilp_hours', 0)
        units_raw = str(data.get('units', '')).strip()
        try:
            units = float(units_raw) if units_raw else 0
            if units.is_integer():
                units = int(units)
        except (ValueError, TypeError):
            units = 0

        try:
            ilp_val = float(ilp_hours)
            if ilp_val not in (0.0, 1.0):
                msg = 'ILP hours must be either 0 or 1.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg, 'error': msg}), 400
                session['course_message'] = msg
                session.modified = True
                return redirect(url_for('show_courses'))
        except (ValueError, TypeError):
            msg = 'ILP hours must be a valid number.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg, 'error': msg}), 400
            session['course_message'] = msg
            session.modified = True
            return redirect(url_for('show_courses'))

        year_level = data.get('year_level', '')
        raw_prog = str(data.get('program') or data.get('program_id') or '').strip()
        program_id = None
        if raw_prog:
            program_id = int(raw_prog) if raw_prog.isdigit() else _find_program_id_by_name(raw_prog)
        if not program_id:
            program_id = _get_user_program_id()

        raw_semester = (data.get('semester') or '').strip()
        major = (data.get('major') or '').strip() or None

        update_data = {
            'course_name': course_name,
            'units': units,
            'lecture_hours': int(lecture_hours) if lecture_hours else 0,
            'lab_hours': int(lab_hours) if lab_hours else 0,
            'ilp_hours': int(float(ilp_hours)) if ilp_hours else 0,
            'year_level': year_level,
            'major': major,
        }
        if program_id:
            update_data['program_id'] = program_id
        if raw_semester:
            update_data['semester'] = raw_semester

        try:
            supabase.table('course').update(update_data).eq('course_id', course_id).execute()
        except Exception:
            update_data.pop('semester', None)
            supabase.table('course').update(update_data).eq('course_id', course_id).execute()

        session.pop('course_message', None)
        log_activity('edit', 'course', course_name)
        if is_ajax:
            return jsonify({'success': True, 'message': 'Course edited successfully.'}), 200
        flash('Edited successfully', 'success')
        return redirect(url_for('show_courses'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('show_courses'))
#-------------------------------------------------------add_professor----------------------------------------------------------------------------------------------
#-------------------------------------------------------add_professor----------------------------------------------------------------------------------------------
@app.route('/add_professor', methods=['POST'])
@roles_required('scheduler')
def add_professor():
    try:
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        specialization = request.form.get('specialization', '').strip()
        academic_ranking_id_raw = request.form.get('academic_ranking_id', '').strip()
        time_desig_raw = request.form.get('time_designation', '5').strip()
        try:
            time_designation = int(time_desig_raw)
            if time_designation not in (4, 5):
                time_designation = 5
        except (ValueError, TypeError):
            time_designation = 5

        try:
            academic_ranking_id = int(academic_ranking_id_raw) if academic_ranking_id_raw else None
        except (ValueError, TypeError):
            academic_ranking_id = None

        if not first_name or not last_name or not academic_ranking_id:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'Name and academic ranking are required.'}), 400
            flash('Name and academic ranking are required.', 'warning')
            return redirect(url_for('professors'))

        program_id = _get_user_program_id()
        department = _get_department()
        user_role = (session.get('role') or '').lower()
        user_program = session.get('program', '').strip()

        if academic_ranking_id:
            try:
                r_check = supabase.table('academic_ranking').select('academic_ranking_id, program_id, program:program_id(program_name), name').eq('academic_ranking_id', academic_ranking_id).execute()
                r_row = _first(r_check.data or [])
                if r_row:
                    row_prog = str((_rel(r_row, 'program') or {}).get('program_name') or r_row.get('program') or '').strip().upper()
                    row_prog_id = r_row.get('program_id')
                    mismatch = False
                    if user_role not in ('admin', 'super_admin'):
                        if row_prog_id and program_id and row_prog_id != program_id:
                            mismatch = True
                        elif user_program and row_prog and row_prog != user_program.upper():
                            mismatch = True
                    if mismatch:
                        err_msg = 'Selected academic ranking does not belong to your assigned program.'
                        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return jsonify({'error': err_msg}), 400
                        flash(err_msg, 'danger')
                        return redirect(url_for('professors'))
            except Exception as r_err:
                logging.warning(f"Error checking academic ranking {academic_ranking_id}: {r_err}")

        # Server-side duplicate check (case-insensitive, trimmed)
        existing = supabase.table('professor').select('prof_id, first_name, last_name').ilike('first_name', first_name).ilike('last_name', last_name).execute()
        if existing.data:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': f'Professor "{first_name} {last_name}" already exists.'}), 400
            flash(f'Professor "{first_name} {last_name}" already exists.', 'warning')
            return redirect(url_for('professors'))

        payload = {
            'first_name': first_name,
            'last_name': last_name,
            'specialization': specialization,
            'academic_ranking_id': academic_ranking_id,
            'time_designation': time_designation,
        }
        if program_id:
            payload['program_id'] = program_id

        insert_res = supabase.table('professor').insert(payload).execute()

        new_id = _first(insert_res.data or [])
        new_id = new_id.get('prof_id') if isinstance(new_id, dict) else None

        log_activity('create', 'professor', f'{first_name} {last_name}')
        flash('Created successfully', 'success')

        if _is_ajax_request():
            return jsonify({
                'success': True,
                'message': 'Professor added successfully.',
                'professor': {
                    'prof_id': new_id,
                    'first_name': first_name,
                    'last_name': last_name,
                    'time_designation': time_designation,
                    'specialization': specialization,
                    'academic_ranking_id': academic_ranking_id,
                }
            })

        return redirect(url_for('professors'))
    except Exception as err:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(err)}), 500
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('professors'))

#-------------------------------------------------------edit_professor----------------------------------------------------------------------------------------------
@app.route('/edit_professor/<int:professor_id>', methods=['POST'])
@roles_required('scheduler')
def edit_professor(professor_id):
    is_ajax = _is_ajax_request()
    try:
        data = request.get_json(silent=True) if request.is_json else None
        if not data:
            data = request.form or {}

        first_name = (data.get('first_name') or '').strip()
        last_name = (data.get('last_name') or '').strip()
        specialization = (data.get('specialization') or '').strip()
        academic_ranking_id_raw = str(data.get('academic_ranking_id') or '').strip()
        time_desig_raw = str(data.get('time_designation', '5')).strip()
        try:
            time_designation = int(time_desig_raw)
            if time_designation not in (4, 5):
                time_designation = 5
        except (ValueError, TypeError):
            time_designation = 5

        try:
            academic_ranking_id = int(academic_ranking_id_raw) if academic_ranking_id_raw else None
        except (ValueError, TypeError):
            academic_ranking_id = None

        if not academic_ranking_id:
            msg = 'Academic ranking is required.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg, 'error': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('professors'))

        user_role = (session.get('role') or '').lower()
        user_program = session.get('program', '').strip()
        program_id = _get_user_program_id()
        if academic_ranking_id:
            try:
                r_check = supabase.table('academic_ranking').select('academic_ranking_id, program_id, program:program_id(program_name), name').eq('academic_ranking_id', academic_ranking_id).execute()
                r_row = _first(r_check.data or [])
                if r_row:
                    row_prog = str((_rel(r_row, 'program') or {}).get('program_name') or r_row.get('program') or '').strip().upper()
                    row_prog_id = r_row.get('program_id')
                    mismatch = False
                    if user_role not in ('admin', 'super_admin'):
                        if row_prog_id and program_id and row_prog_id != program_id:
                            mismatch = True
                        elif user_program and row_prog and row_prog != user_program.upper():
                            mismatch = True
                    if mismatch:
                        msg = 'Selected academic ranking does not belong to your assigned program.'
                        if is_ajax:
                            return jsonify({'success': False, 'message': msg, 'error': msg}), 400
                        flash(msg, 'danger')
                        return redirect(url_for('professors'))
            except Exception as r_err:
                logging.warning(f"Error checking academic ranking {academic_ranking_id}: {r_err}")

        update_payload = {
            'first_name': first_name,
            'last_name': last_name,
            'specialization': specialization,
            'academic_ranking_id': academic_ranking_id,
            'time_designation': time_designation,
        }
        if program_id:
            update_payload['program_id'] = program_id

        supabase.table('professor').update(update_payload).eq('prof_id', professor_id).execute()

        log_activity('edit', 'professor', f'{first_name} {last_name}')
        if is_ajax:
            return jsonify({'success': True, 'message': 'Professor updated successfully.'}), 200
        flash('Edited successfully', 'success')
        return redirect(url_for('professors'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('professors'))

#-------------------------------------------------------delete_professor----------------------------------------------------------------------------------------------
@app.route('/delete_professor/<int:professor_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_professor(professor_id):
    is_ajax = _is_ajax_request()
    try:
        # Data protection: Check if professor has teaching loads
        try:
            load_check = supabase.table('professor_load').select('id').eq('prof_id', professor_id).limit(1).execute()
            if load_check.data:
                err_msg = 'Cannot delete professor: this faculty member has assigned courses in teaching load. Please remove the teaching load assignments first.'
                if is_ajax:
                    return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
                flash(err_msg, 'danger')
                return redirect(url_for('professors'))
        except Exception as e:
            logging.debug(f"Error checking professor teaching load references: {e}")

        # Data protection: Check if professor is assigned in active schedules
        try:
            sched_check = supabase.table('schedule').select('schedule_id').eq('prof_id', professor_id).eq('archive', False).limit(1).execute()
            if sched_check.data:
                err_msg = 'Cannot delete professor: this professor is assigned to active class schedules. Please remove schedule assignments or archive the schedule first.'
                if is_ajax:
                    return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
                flash(err_msg, 'danger')
                return redirect(url_for('professors'))
        except Exception as e:
            logging.debug(f"Error checking professor schedule references: {e}")

        supabase.table('professor').delete().eq('prof_id', professor_id).execute()
        try:
            _set_delete_request_status({'item_type': 'professor', 'item_id': str(professor_id), 'status': 'pending'}, 'approved')
        except Exception:
            pass
        log_activity('delete', 'professor', f'Professor ID {professor_id}')
        if is_ajax:
            return jsonify({'success': True, 'message': 'Professor deleted successfully.'}), 200
        flash('Deleted successfully', 'success')
        return redirect(url_for('professors'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('professors'))

#-------------------------------------------------------show_professors----------------------------------------------------------------------------------------------
@app.route('/professors')
@roles_required('scheduler')
def professors():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')

    try:
        query = supabase.table('professor').select('*, program:program_id(id, program_name)')
        if not is_super_admin and user_prog_id:
            query = query.eq('program_id', user_prog_id)
        all_professors = query.execute().data or []
    except Exception:
        all_professors = (supabase.table('professor').select('*').execute().data) or []

    for p in all_professors:
        p_rel = _rel(p, 'program') or {}
        p['department'] = p['program_name'] = p_rel.get('program_name') or session.get('program', '')

    all_professors.sort(key=lambda p: (str(p.get('last_name') or ''), str(p.get('first_name') or '')))

    # Fetch academic rankings for dropdown
    ranking_query = supabase.table('academic_ranking').select('*')
    if not is_super_admin and user_prog_id:
        try:
            ranking_query = ranking_query.eq('program_id', user_prog_id)
        except Exception:
            pass

    try:
        academic_rankings = ranking_query.execute().data or []
        academic_rankings.sort(key=lambda r: str(r.get('name') or ''))
    except Exception:
        academic_rankings = []

    try:
        all_rankings_res = supabase.table('academic_ranking').select('academic_ranking_id, name, min_units, max_units, min_hours, max_hours').execute().data or []
        ranking_map = {str(r['academic_ranking_id']): r for r in all_rankings_res}
    except Exception:
        ranking_map = {}

    for p in all_professors:
        ranking_id = p.get('academic_ranking_id')
        r_info = ranking_map.get(str(ranking_id)) if ranking_id is not None else None
        r_info = r_info or {}
        p['academic_ranking_name'] = r_info.get('name', '')
        p['ranking_constraints'] = r_info
        p['time_designation'] = p.get('time_designation') or 5
        p['total_assigned_units'] = 0
        p['total_assigned_hours'] = 0

    try:
        load_rows = supabase.table('professor_load').select(
            'prof_id, sections, course(units, lecture_hours, lab_hours, ilp_hours)'
        ).execute().data or []
        totals = {}
        for row in load_rows:
            course = _rel(row, 'course') or {}
            sections = max(1, int(row.get('sections') or 1))
            prof_totals = totals.setdefault(str(row.get('prof_id')), {'units': 0, 'hours': 0})
            prof_totals['units'] += float(course.get('units') or 0) * sections
            prof_totals['hours'] += sum(float(course.get(key) or 0) for key in ('lecture_hours', 'lab_hours', 'ilp_hours')) * sections
        for p in all_professors:
            total = totals.get(str(p.get('prof_id')), {'units': 0, 'hours': 0})
            p['total_assigned_units'] = int(total['units']) if total['units'].is_integer() else round(total['units'], 2)
            p['total_assigned_hours'] = int(total['hours']) if total['hours'].is_integer() else round(total['hours'], 2)
    except Exception as err:
        logging.warning(f'Unable to calculate professor assigned totals: {err}')

    return render_template('professors.html', active_page='professors', professors=all_professors, academic_rankings=academic_rankings)

#-------------------------------------------------------ACADEMIC_RANKING_MODULE----------------------------------------------------------------------------------------------
@app.route('/academic_ranking')
@roles_required('scheduler')
def academic_ranking():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    user_program = session.get('program', '').strip()
    program_filter = request.args.get('program', '').strip()

    try:
        query = supabase.table('academic_ranking').select('*, program:program_id(id, program_name)')
        if not is_super_admin and user_prog_id:
            query = query.eq('program_id', user_prog_id)
        elif is_super_admin and program_filter and program_filter.lower() != 'all':
            p_id = _find_program_id_by_name(program_filter)
            if p_id:
                query = query.eq('program_id', p_id)
        rankings = query.execute().data or []
    except Exception as err:
        try:
            query = supabase.table('academic_ranking').select('*')
            if not is_super_admin and user_prog_id:
                query = query.eq('program_id', user_prog_id)
            rankings = query.execute().data or []
        except Exception:
            logging.warning(f"Error querying academic_ranking: {err}")
            rankings = []

    for r in rankings:
        r_prog = (_rel(r, 'program') or {}).get('program_name') or r.get('program') or user_program or 'General'
        r['program_name'] = r_prog
        r['program'] = r_prog

    rankings.sort(key=lambda r: str(r.get('name') or ''))

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'rankings': rankings})

    return render_template('academic_ranking.html',
                           active_page='academic_ranking',
                           rankings=rankings,
                           program=user_program)

@app.route('/add_academic_ranking', methods=['POST'])
@roles_required('scheduler')
def add_academic_ranking():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    user_program = session.get('program', '').strip()

    data = request.get_json(silent=True) or request.form or {}
    name = (data.get('name') or data.get('ranking_name') or '').strip()
    try:
        min_units = float(data.get('min_units', 0) or 0)
        max_units = float(data.get('max_units', 24) or 24)
        min_hours = float(data.get('min_hours', 0) or 0)
        max_hours = float(data.get('max_hours', 40) or 40)
        if min_units < 0 or max_units <= 0 or min_units > max_units or min_hours < 0 or max_hours <= 0 or min_hours > max_hours:
            raise ValueError
    except (TypeError, ValueError):
        err_msg = 'Load limits must be valid, non-negative values with minimums not exceeding maximums.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': err_msg}), 400
        flash(err_msg, 'danger')
        return redirect(url_for('academic_ranking'))

    if is_super_admin and not user_program:
        program = request.form.get('program', '').strip() or 'General'
    else:
        program = user_program

    if not name:
        err_msg = 'Rank name is required.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': err_msg}), 400
        flash(err_msg, 'danger')
        return redirect(url_for('academic_ranking'))

    try:
        eff_pid = user_prog_id or _find_program_id_by_name(program)
        payload = {
            'name': name,
            'min_units': min_units,
            'max_units': max_units,
            'min_hours': min_hours,
            'max_hours': max_hours,
        }
        if eff_pid:
            payload['program_id'] = eff_pid

        try:
            ins_res = supabase.table('academic_ranking').insert(payload).execute()
        except Exception:
            payload['program'] = program
            ins_res = supabase.table('academic_ranking').insert(payload).execute()

        new_ranking = _first(ins_res.data or []) or {
            'name': name,
            'program': program
        }
        if isinstance(new_ranking, dict):
            new_ranking.setdefault('program', program)

        log_activity('create', 'academic_ranking', f'{name} ({program})')
        flash('Academic ranking added successfully.', 'success')

        if _is_ajax_request():
            return jsonify({
                'success': True,
                'message': 'Academic ranking added successfully.',
                'ranking': new_ranking
            })

        return redirect(url_for('academic_ranking'))
    except Exception as err:
        logging.exception(f"Error adding academic ranking: {err}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(err)}), 500
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('academic_ranking'))

@app.route('/edit_academic_ranking/<int:ranking_id>', methods=['POST'])
@roles_required('scheduler')
def edit_academic_ranking(ranking_id):
    is_ajax = _is_ajax_request()
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    user_program = session.get('program', '').strip()

    data = request.get_json(silent=True) if request.is_json else None
    if not data:
        data = request.form or {}

    name = (data.get('name') or '').strip()
    try:
        min_units = float(data.get('min_units', 0) or 0)
        max_units = float(data.get('max_units', 24) or 24)
        min_hours = float(data.get('min_hours', 0) or 0)
        max_hours = float(data.get('max_hours', 40) or 40)
        if min_units < 0 or max_units <= 0 or min_units > max_units or min_hours < 0 or max_hours <= 0 or min_hours > max_hours:
            raise ValueError
    except (TypeError, ValueError):
        msg = 'Load limits must be valid, non-negative values with minimums not exceeding maximums.'
        if is_ajax:
            return jsonify({'success': False, 'message': msg, 'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('academic_ranking'))
    if not name:
        msg = 'Rank name is required.'
        if is_ajax:
            return jsonify({'success': False, 'message': msg, 'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('academic_ranking'))

    try:
        curr_res = supabase.table('academic_ranking').select('*').eq('academic_ranking_id', ranking_id).execute()
        curr_row = _first(curr_res.data or [])
        if not curr_row:
            msg = 'Academic ranking not found.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg, 'error': msg}), 404
            flash(msg, 'danger')
            return redirect(url_for('academic_ranking'))

        # Restrict editing to own program
        if not is_super_admin and user_prog_id:
            row_pid = curr_row.get('program_id')
            if row_pid and row_pid != user_prog_id:
                msg = 'You do not have permission to edit academic rankings outside your program.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg, 'error': msg}), 403
                flash(msg, 'danger')
                return redirect(url_for('academic_ranking'))

        update_payload = {
            'name': name,
            'min_units': min_units,
            'max_units': max_units,
            'min_hours': min_hours,
            'max_hours': max_hours,
        }
        supabase.table('academic_ranking').update(update_payload).eq('academic_ranking_id', ranking_id).execute()

        log_activity('edit', 'academic_ranking', f'{name} (ID: {ranking_id})')
        if is_ajax:
            return jsonify({'success': True, 'message': 'Academic ranking updated successfully.'}), 200
        flash('Academic ranking updated successfully.', 'success')
        return redirect(url_for('academic_ranking'))
    except Exception as err:
        logging.exception(f"Error updating academic ranking {ranking_id}: {err}")
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('academic_ranking'))

@app.route('/delete_academic_ranking/<int:ranking_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_academic_ranking(ranking_id):
    is_ajax = _is_ajax_request()
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')

    try:
        curr_res = supabase.table('academic_ranking').select('*').eq('academic_ranking_id', ranking_id).execute()
        curr_row = _first(curr_res.data or [])
        if not curr_row:
            msg = 'Academic ranking not found.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg, 'error': msg}), 404
            flash(msg, 'danger')
            return redirect(url_for('academic_ranking'))

        if not is_super_admin and user_prog_id:
            row_pid = curr_row.get('program_id')
            if row_pid and row_pid != user_prog_id:
                msg = 'You do not have permission to delete academic rankings outside your program.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg, 'error': msg}), 403
                flash(msg, 'danger')
                return redirect(url_for('academic_ranking'))

        ranking_name = curr_row.get('name', f'ID {ranking_id}')

        # Data protection: Check if professors are assigned
        assigned = supabase.table('professor').select('prof_id').eq('academic_ranking_id', ranking_id).execute().data or []
        if assigned:
            err_msg = 'This ranking cannot be deleted while professors are assigned to it.'
            if is_ajax:
                return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
            flash(err_msg, 'danger')
            return redirect(url_for('academic_ranking'))

        supabase.table('academic_ranking').delete().eq('academic_ranking_id', ranking_id).execute()
        try:
            _set_delete_request_status({'item_type': 'academic_ranking', 'item_id': str(ranking_id), 'status': 'pending'}, 'approved')
        except Exception:
            pass

        log_activity('delete', 'academic_ranking', f'{ranking_name} (ID {ranking_id})')
        if is_ajax:
            return jsonify({'success': True, 'message': 'Academic ranking deleted successfully.'}), 200
        flash('Academic ranking deleted successfully.', 'success')
        return redirect(url_for('academic_ranking'))
    except Exception as err:
        logging.exception(f"Error deleting academic ranking {ranking_id}: {err}")
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('academic_ranking'))

@app.route('/api/academic_rankings', methods=['GET'])
@roles_required('scheduler')
def api_academic_rankings():
    user_role = (session.get('role') or 'scheduler').lower()
    user_program = session.get('program', '').strip()
    prog_arg = request.args.get('program', '').strip()

    target_prog = prog_arg or user_program
    target_pid = _find_program_id_by_name(target_prog) or _get_user_program_id()
    query = supabase.table('academic_ranking').select('*, program:program_id(id, program_name)')
    if user_role not in ('super_admin', 'admin') or target_pid:
        if target_pid:
            query = query.eq('program_id', target_pid)

    try:
        data = query.execute().data or []
    except Exception:
        query = supabase.table('academic_ranking').select('*')
        if target_pid:
            query = query.eq('program_id', target_pid)
        data = query.execute().data or []

    for r in data:
        r_prog = (_rel(r, 'program') or {}).get('program_name') or r.get('program') or target_prog or 'General'
        r['program_name'] = r_prog
        r['program'] = r_prog

    data.sort(key=lambda r: str(r.get('name') or ''))
    return jsonify({'academic_rankings': data})
#-------------------------------------------------------show_rooms----------------------------------------------------------------------------------------------
@app.route('/rooms')
@roles_required('scheduler')
def rooms():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    selected_program = request.args.get('program', '').strip()

    try:
        query = supabase.table('room').select('*, program:program_id(id, program_name)')
        if not is_super_admin and user_prog_id:
            query = query.eq('program_id', user_prog_id)
        elif is_super_admin and selected_program and selected_program.lower() != 'all':
            p_id = _find_program_id_by_name(selected_program)
            if p_id:
                query = query.eq('program_id', p_id)
        all_rooms = query.execute().data or []
    except Exception:
        all_rooms = (supabase.table('room').select('*').execute().data) or []

    programs = _get_programs()
    prog_map = {p['id']: p['program_name'] for p in programs}
    for r in all_rooms:
        r['program_name'] = (_rel(r, 'program') or {}).get('program_name') or prog_map.get(r.get('program_id')) or session.get('program', '')

    all_rooms.sort(key=lambda r: str(r.get('room_name') or ''))
    return render_template(
        'room.html',
        active_page='rooms',
        rooms=all_rooms,
        programs=programs,
        user_program_id=user_prog_id,
        is_super_admin=is_super_admin,
        selected_program=selected_program
    )

#-------------------------------------------------------search_rooms----------------------------------------------------------------------------------------------
@app.route('/search_rooms', methods=['GET'])
@roles_required('scheduler')
def search_rooms():
    query_str = request.args.get('q', '').strip()
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')

    if not query_str:
        return jsonify({'rooms': [], 'exact_match': False})

    try:
        cols = 'room_id, room_name, room_type, program_id, program:program_id(id, program_name)'
        try:
            query = supabase.table('room').select(cols)
        except Exception:
            cols = 'room_id, room_name, room_type, program_id'
            query = supabase.table('room').select(cols)
        exact_query = supabase.table('room').select('room_id')

        if not is_super_admin and user_prog_id:
            query = query.eq('program_id', user_prog_id)
            exact_query = exact_query.eq('program_id', user_prog_id)

        matching_rooms = query.ilike('room_name', f'%{query_str}%').order('room_name').limit(10).execute().data or []
        exact = exact_query.ilike('room_name', query_str).limit(1).execute()
        exact_match_row = _first(exact.data or [])

        programs = _get_programs()
        prog_map = {p['id']: p['program_name'] for p in programs}
        for r in matching_rooms:
            prog_info = _rel(r, 'program') or {}
            r['program_name'] = prog_info.get('program_name') or prog_map.get(r.get('program_id')) or session.get('program', '')
            r.pop('department', None)

        return jsonify({
            'rooms': matching_rooms,
            'exact_match': exact_match_row is not None
        })
    except Exception as err:
        return jsonify({'error': str(err), 'rooms': [], 'exact_match': False}), 500

@app.route('/add_room', methods=['POST'])
@roles_required('scheduler')
def add_room():
    is_ajax = (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '').lower()
    )
    try:
        data = request.get_json(silent=True) if request.is_json else None
        if not data:
            data = request.form or {}

        room_name = (data.get('room_name') or '').strip()
        room_type = (data.get('room_type') or 'Lecture Room').strip()
        raw_prog = data.get('program_id')

        if not room_name:
            err_msg = 'Room name is required.'
            if is_ajax:
                return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
            flash(err_msg, 'warning')
            return redirect(url_for('rooms'))

        program_id = None
        if raw_prog and str(raw_prog).strip():
            try:
                program_id = int(raw_prog)
            except (ValueError, TypeError):
                program_id = _find_program_id_by_name(str(raw_prog).strip())
        if not program_id:
            program_id = _get_user_program_id()

        payload = {
            'room_name': room_name,
            'room_type': room_type,
        }
        if program_id:
            payload['program_id'] = program_id

        res = supabase.table('room').insert(payload).execute()

        log_activity('create', 'room', room_name)
        if is_ajax:
            created_room = res.data[0] if (res and res.data) else payload
            return jsonify({'success': True, 'message': 'Created successfully', 'room': created_room}), 200
        flash('Created successfully', 'success')
        return redirect(url_for('rooms'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('rooms'))

#-------------------------------------------------------edit_room----------------------------------------------------------------------------------------------
@app.route('/edit_room/<int:room_id>', methods=['POST'])
@roles_required('scheduler')
def edit_room(room_id):
    is_ajax = (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '').lower()
    )
    try:
        data = request.get_json(silent=True) if request.is_json else None
        if not data:
            data = request.form or {}

        room_name = (data.get('room_name') or '').strip()
        room_type = (data.get('room_type') or 'Lecture Room').strip()
        raw_prog = data.get('program_id')

        if not room_name:
            err_msg = 'Room name is required.'
            if is_ajax:
                return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
            flash(err_msg, 'warning')
            return redirect(url_for('rooms'))

        program_id = None
        if raw_prog and str(raw_prog).strip():
            try:
                program_id = int(raw_prog)
            except (ValueError, TypeError):
                program_id = _find_program_id_by_name(str(raw_prog).strip())

        update_payload = {
            'room_name': room_name,
            'room_type': room_type,
        }
        if program_id is not None:
            update_payload['program_id'] = program_id

        supabase.table('room').update(update_payload).eq('room_id', room_id).execute()

        log_activity('edit', 'room', room_name)
        if is_ajax:
            return jsonify({'success': True, 'message': 'Edited successfully'}), 200
        flash('Edited successfully', 'success')
        return redirect(url_for('rooms'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('rooms'))

#-------------------------------------------------------delete_room----------------------------------------------------------------------------------------------
@app.route('/delete_room/<int:room_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_room(room_id):
    is_ajax = (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '').lower()
    )
    try:
        # Data protection: Check if room is referenced by active schedule rows
        in_active_schedule = False
        try:
            sched_check = supabase.table('schedule').select('schedule_id').eq('room_id', room_id).eq('archive', False).limit(1).execute()
            in_active_schedule = bool(sched_check.data)
        except Exception:
            try:
                sched_check = supabase.table('schedule').select('schedule_id').eq('room_id', room_id).limit(1).execute()
                in_active_schedule = bool(sched_check.data)
            except Exception:
                in_active_schedule = False

        if in_active_schedule:
            err_msg = 'Cannot delete room: this room is currently assigned to one or more active schedules. Please remove the room assignment or archive the schedule first.'
            if is_ajax:
                return jsonify({'success': False, 'message': err_msg, 'error': err_msg}), 400
            flash(err_msg, 'danger')
            return redirect(url_for('rooms'))

        supabase.table('room').delete().eq('room_id', room_id).execute()
        try:
            _set_delete_request_status({'item_type': 'room', 'item_id': str(room_id), 'status': 'pending'}, 'approved')
        except Exception:
            pass
        log_activity('delete', 'room', f'Room ID {room_id}')
        if is_ajax:
            return jsonify({'success': True, 'message': 'Deleted successfully'}), 200
        flash('Deleted successfully', 'success')
        return redirect(url_for('rooms'))
    except Exception as err:
        if is_ajax:
            return jsonify({'success': False, 'message': str(err), 'error': str(err)}), 400
        flash(f"Error: {err}", 'danger')
        return redirect(url_for('rooms'))

#-------------------------------------------------------show_professor_load----------------------------------------------------------------------------------------------
@app.route('/professor_load')
@roles_required('scheduler')
def professor_load():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    department = _get_department()
    program = session.get('program', '')

    cols_pc_full = 'professor_load_id:id, prof_id, course_id, sections, course(course_name, program_id, program:program_id(program_name), lecture_hours, lab_hours, ilp_hours, units), professor(first_name, last_name, specialization, program_id, program:program_id(program_name), academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours))'
    cols_pc_fallback = 'professor_load_id:id, prof_id, course_id, sections, course(course_name, program_id, program:program_id(program_name), lecture_hours, lab_hours, ilp_hours, units), professor(first_name, last_name, specialization, program_id)'

    def _fetch_pc_rows(cols):
        q = supabase.table('professor_load').select(cols)
        return q.execute().data or []

    try:
        pc_rows = _fetch_pc_rows(cols_pc_full)
    except Exception:
        try:
            pc_rows = _fetch_pc_rows(cols_pc_fallback)
        except Exception as e:
            logging.error(f"Error fetching professor_load: {e}")
            pc_rows = []

    professor_loads = []
    existing_professor_assignments = {}
    for row in pc_rows:
        c = _rel(row, 'course') or {}
        p = _rel(row, 'professor') or {}
        c['program_name'] = (_rel(c, 'program') or {}).get('program_name') or ''
        c['program'] = c['program_name']
        p['department'] = p['program_name'] = (_rel(p, 'program') or {}).get('program_name') or department or ''
        
        # Filter by program_id for Dean
        if not is_super_admin and user_prog_id:
            row_prog_id = p.get('program_id') or c.get('program_id')
            if row_prog_id and row_prog_id != user_prog_id:
                continue

        try:
            sections = int(row.get('sections') or 1)
        except (ValueError, TypeError):
            sections = 1
        if sections >= 999 or sections <= 0:
            sections = 1
        lec = float(c.get('lecture_hours') or 0)
        lab = float(c.get('lab_hours') or 0)
        ilp = float(c.get('ilp_hours') or 0)
        units = float(c.get('units') or 0)
        weekly_hours = (lec + lab + ilp) * sections
        total_units = units * sections
        prof_id = row.get('prof_id')
        existing_professor_assignments.setdefault(str(prof_id), []).append({
            'course_id': row.get('course_id'),
            'sections': sections,
        })
        professor_loads.append({
            'professor_load_id': row.get('professor_load_id'),
            'prof_id': prof_id,
            'course_id': row.get('course_id'),
            'sections': sections,
            'course_name': c.get('course_name'),
            'program': c.get('program'),
            'lecture_hours': int(lec) if lec.is_integer() else lec,
            'lab_hours': int(lab) if lab.is_integer() else lab,
            'ilp_hours': int(ilp) if ilp.is_integer() else ilp,
            'units': int(units) if units.is_integer() else units,
            'weekly_hours': int(weekly_hours) if weekly_hours.is_integer() else weekly_hours,
            'total_units': int(total_units) if total_units.is_integer() else total_units,
            'prof_first_name': p.get('first_name'),
            'prof_last_name': p.get('last_name'),
            'prof_department': p.get('department'),
            'specialization': p.get('specialization') or '',
            **_ranking_constraints(p),
        })
    professor_loads.sort(key=lambda x: (str(x.get('prof_id') or ''), str(x.get('course_id') or '')))

    def _fetch_professors():
        cols_p = 'prof_id, first_name, last_name, specialization, program_id, program:program_id(program_name), academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)'
        try:
            q = supabase.table('professor').select(cols_p)
            if not is_super_admin and user_prog_id:
                q = q.eq('program_id', user_prog_id)
            return q.execute().data or []
        except Exception:
            try:
                q = supabase.table('professor').select('prof_id, first_name, last_name, specialization, program_id')
                if not is_super_admin and user_prog_id:
                    q = q.eq('program_id', user_prog_id)
                return q.execute().data or []
            except Exception:
                return []

    professors = _fetch_professors()
    for professor in professors:
        professor.update(_ranking_constraints(professor))
        p_rel = _rel(professor, 'program') or {}
        professor['department'] = professor['program_name'] = p_rel.get('program_name') or department or ''
    professors.sort(key=lambda p: (str(p.get('last_name') or ''), str(p.get('first_name') or '')))

    cols_c = 'course_id, course_name, program_id, program:program_id(program_name), year_level, lecture_hours, lab_hours, ilp_hours, units, semester'
    try:
        course_query = supabase.table('course').select(cols_c)
        if not is_super_admin and user_prog_id:
            course_query = course_query.eq('program_id', user_prog_id)
        courses = course_query.execute().data or []
    except Exception:
        try:
            course_query = supabase.table('course').select('*')
            if not is_super_admin and user_prog_id:
                course_query = course_query.eq('program_id', user_prog_id)
            courses = course_query.execute().data or []
        except Exception:
            courses = []
    for c in courses:
        p_rel = _rel(c, 'program') or {}
        c['program_name'] = p_rel.get('program_name') or program or ''
        c['program'] = c['program_name']
    courses.sort(key=lambda c: str(c.get('course_name') or ''))

    return render_template(
        'professor_load.html',
        active_page='professor_load',
        professor_loads=professor_loads,
        existing_professor_assignments=existing_professor_assignments,
        professors=professors,
        courses=courses,
    )
#-------------------------------------------------------add_professor_load----------------------------------------------------------------------------------------------
@app.route('/add_professor_load', methods=['POST'])
@roles_required('scheduler')
def add_professor_load():
    prof_id = request.form.get('prof_id')
    course_ids = request.form.getlist('course_ids')

    if not prof_id or not course_ids:
        flash('Please select a professor and at least one course.', 'warning')
        return redirect(url_for('professor_load'))

    try:
        prof_res = supabase.table('professor').select('*, academic_ranking(min_units, max_units, min_hours, max_hours)').eq('prof_id', prof_id).execute()
        prof_data = _first(prof_res.data or []) or {}
        limits = _ranking_constraints(prof_data)

        # Build rows with section counts and calculate load
        total_hours = 0.0
        total_units = 0.0

        c_res = supabase.table('course').select('course_id, course_name, year_level, semester, lecture_hours, lab_hours, ilp_hours, units').in_('course_id', [int(cid) for cid in course_ids]).execute()
        course_map = {str(c['course_id']): c for c in (c_res.data or [])}

        course_sections = {}
        for cid in course_ids:
            sec_val = request.form.get(f'sections_{cid}') or request.form.get(f'sections[{cid}]') or request.form.get('sections', 1)
            try:
                sections = max(1, int(sec_val))
            except (ValueError, TypeError):
                sections = 1

            course_sections[int(cid)] = sections
            c_info = course_map.get(str(cid), {})
            lec = float(c_info.get('lecture_hours') or 0)
            lab = float(c_info.get('lab_hours') or 0)
            ilp = float(c_info.get('ilp_hours') or 0)
            u = float(c_info.get('units') or 0)

            total_hours += (lec + lab + ilp) * sections
            total_units += u * sections

        if total_units > limits['max_units'] or total_hours > limits['max_hours']:
            flash(
                f"Cannot assign courses: load exceeds ranking limits ({total_hours:.1f}/{limits['max_hours']:.1f} hours, "
                f"{total_units:.1f}/{limits['max_units']:.1f} units).",
                'danger'
            )
            return redirect(url_for('professor_load'))

        # Section capping validation against active semester section_config
        active_sem = _get_active_semester()
        sem_id = active_sem.get('id') if active_sem else None
        section_configs = _get_section_configs(sem_id) if sem_id else {}

        # Fetch existing assignments for these courses to check total sections
        cids_int = [int(cid) for cid in course_ids]
        try:
            existing_course_loads = supabase.table('professor_load').select('prof_id, course_id, sections').in_('course_id', cids_int).execute().data or []
        except Exception:
            existing_course_loads = []

        for cid_int, new_sec in course_sections.items():
            c_info = course_map.get(str(cid_int), {})
            yl = c_info.get('year_level')
            try:
                yl_int = int(yl) if yl is not None else None
            except (ValueError, TypeError):
                yl_int = None
            if yl_int and yl_int in section_configs:
                cap = int(section_configs[yl_int].get('number_of_sections') or 0)
                other_sections = sum(
                    int(r.get('sections') or 1)
                    for r in existing_course_loads
                    if str(r.get('course_id')) == str(cid_int) and str(r.get('prof_id')) != str(prof_id)
                )
                if cap > 0 and (other_sections + new_sec) > cap:
                    cname = c_info.get('course_name') or f"Course #{cid_int}"
                    flash(
                        f"Cannot assign courses: Total sections for {cname} would be {other_sections + new_sec}, "
                        f"exceeding the configured limit of {cap} sections for Year {yl_int}.",
                        'danger'
                    )
                    return redirect(url_for('professor_load'))

        # Smart sync: update existing assignments, insert new ones, and remove unselected
        try:
            existing_pc = supabase.table('professor_load').select('professor_load_id:id, course_id, sections').eq('prof_id', prof_id).execute().data or []
        except Exception:
            try:
                existing_pc = supabase.table('professor_load').select('professor_load_id:id, course_id').eq('prof_id', prof_id).execute().data or []
            except Exception:
                existing_pc = []
        existing_map = {row['course_id']: row['professor_load_id'] for row in existing_pc}

        for cid_int, sections in course_sections.items():
            if cid_int in existing_map:
                pcid = existing_map[cid_int]
                try:
                    supabase.table('professor_load').update({'sections': sections}).eq('id', pcid).execute()
                except Exception as update_err:
                    if 'sections' in str(update_err) or '42703' in str(update_err):
                        pass
                    else:
                        raise
            else:
                try:
                    supabase.table('professor_load').insert({'prof_id': int(prof_id), 'course_id': cid_int, 'sections': sections}).execute()
                except Exception as insert_err:
                    if 'sections' in str(insert_err) or '42703' in str(insert_err):
                        supabase.table('professor_load').insert({'prof_id': int(prof_id), 'course_id': cid_int}).execute()
                    else:
                        raise

        # Remove unselected course assignments
        submitted_cids = set(course_sections.keys())
        for old_cid, pcid in existing_map.items():
            if old_cid not in submitted_cids:
                try:
                    supabase.table('professor_load').delete().eq('id', pcid).execute()
                except Exception as del_err:
                    logging.warning(f"Could not delete unselected professor_load {pcid}: {del_err}")

        flash(f'Assigned {len(course_ids)} courses successfully ({total_hours:.1f} hrs, {total_units:.1f} units).', 'success')

        log_activity('create', 'professor_load', f'Assigned {len(course_ids)} courses to Prof ID {prof_id} ({total_hours:.1f} hrs, {total_units:.1f} units)')
    except Exception as err:
        flash(f'Error assigning courses: {err}', 'danger')

    return redirect(url_for('professor_load'))
#-------------------------------------------------------update_prof_with_courses----------------------------------------------------------------------------------------------
@app.route('/update_prof_with_courses/<int:prof_id>', methods=['POST'])
@roles_required('scheduler')
def update_prof_with_courses(prof_id):
    try:
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        department = request.form.get('department', '').strip()
        specialization = request.form.get('specialization', '').strip()
        course_ids = request.form.getlist('course_ids')

        prog_id = _find_program_id_by_name(department) or _get_user_program_id()
        update_data = {
            'first_name': first_name,
            'last_name': last_name,
            'specialization': specialization,
        }
        if prog_id:
            update_data['program_id'] = prog_id

        try:
            supabase.table('professor').update(update_data).eq('prof_id', prof_id).execute()
        except Exception as err:
            if 'specialization' in str(err) or '42703' in str(err):
                update_data.pop('specialization', None)
                supabase.table('professor').update(update_data).eq('prof_id', prof_id).execute()
            else:
                raise

        # Check professor limits
        prof_res = supabase.table('professor').select('*, academic_ranking(min_units, max_units, min_hours, max_hours)').eq('prof_id', prof_id).execute()
        prof_data = _first(prof_res.data or []) or {}
        limits = _ranking_constraints(prof_data)

        # Build rows with sections and validate
        total_hours = 0.0
        total_units = 0.0
        course_sections = {}

        if course_ids:
            c_res = supabase.table('course').select('course_id, course_name, year_level, semester, lecture_hours, lab_hours, ilp_hours, units').in_('course_id', [int(cid) for cid in course_ids]).execute()
            course_map = {str(c['course_id']): c for c in (c_res.data or [])}

            for cid in course_ids:
                sec_val = (
                    request.form.get(f'edit_sections_{cid}')
                    or request.form.get(f'edit_sections[{cid}]')
                    or request.form.get(f'sections_{cid}')
                    or request.form.get(f'sections[{cid}]')
                    or 1
                )
                try:
                    sections = max(1, int(sec_val))
                except (ValueError, TypeError):
                    sections = 1

                course_sections[int(cid)] = sections
                c_info = course_map.get(str(cid), {})
                lec = float(c_info.get('lecture_hours') or 0)
                lab = float(c_info.get('lab_hours') or 0)
                ilp = float(c_info.get('ilp_hours') or 0)
                u = float(c_info.get('units') or 0)

                total_hours += (lec + lab + ilp) * sections
                total_units += u * sections

            if total_units > limits['max_units'] or total_hours > limits['max_hours']:
                flash(
                    f"Cannot update assignments: load exceeds ranking limits ({total_hours:.1f}/{limits['max_hours']:.1f} hours, "
                    f"{total_units:.1f}/{limits['max_units']:.1f} units).",
                    'danger'
                )
                return redirect(url_for('professor_load'))

            # Section capping validation against active semester section_config
            active_sem = _get_active_semester()
            sem_id = active_sem.get('id') if active_sem else None
            section_configs = _get_section_configs(sem_id) if sem_id else {}

            cids_int = [int(cid) for cid in course_ids]
            try:
                existing_course_loads = supabase.table('professor_load').select('prof_id, course_id, sections').in_('course_id', cids_int).execute().data or []
            except Exception:
                existing_course_loads = []

            for cid_int, new_sec in course_sections.items():
                c_info = course_map.get(str(cid_int), {})
                yl = c_info.get('year_level')
                try:
                    yl_int = int(yl) if yl is not None else None
                except (ValueError, TypeError):
                    yl_int = None
                if yl_int and yl_int in section_configs:
                    cap = int(section_configs[yl_int].get('number_of_sections') or 0)
                    other_sections = sum(
                        int(r.get('sections') or 1)
                        for r in existing_course_loads
                        if str(r.get('course_id')) == str(cid_int) and str(r.get('prof_id')) != str(prof_id)
                    )
                    if cap > 0 and (other_sections + new_sec) > cap:
                        cname = c_info.get('course_name') or f"Course #{cid_int}"
                        flash(
                            f"Cannot update assignments: Total sections for {cname} would be {other_sections + new_sec}, "
                            f"exceeding the configured limit of {cap} sections for Year {yl_int}.",
                            'danger'
                        )
                        return redirect(url_for('professor_load'))

        # Smart sync for professor_load assignments
        try:
            existing_pc = supabase.table('professor_load').select('professor_load_id:id, course_id, sections').eq('prof_id', prof_id).execute().data or []
        except Exception:
            try:
                existing_pc = supabase.table('professor_load').select('professor_load_id:id, course_id').eq('prof_id', prof_id).execute().data or []
            except Exception:
                existing_pc = []
        existing_map = {row['course_id']: row['professor_load_id'] for row in existing_pc}

        # Update or insert
        for cid_int, sections in course_sections.items():
            if cid_int in existing_map:
                pcid = existing_map[cid_int]
                try:
                    supabase.table('professor_load').update({'sections': sections}).eq('id', pcid).execute()
                except Exception as update_err:
                    if 'sections' in str(update_err) or '42703' in str(update_err):
                        pass
                    else:
                        raise
            else:
                try:
                    supabase.table('professor_load').insert({'prof_id': prof_id, 'course_id': cid_int, 'sections': sections}).execute()
                except Exception as insert_err:
                    if 'sections' in str(insert_err) or '42703' in str(insert_err):
                        supabase.table('professor_load').insert({'prof_id': prof_id, 'course_id': cid_int}).execute()
                    else:
                        raise

        # Remove unselected
        submitted_cids = set(course_sections.keys())
        for old_cid, pcid in existing_map.items():
            if old_cid not in submitted_cids:
                try:
                    supabase.table('professor_load').delete().eq('id', pcid).execute()
                except Exception as del_err:
                    logging.warning(f"Could not remove unselected professor_load {pcid}: {del_err}")

        flash(f'Updated assignments successfully ({total_hours:.1f} hrs, {total_units:.1f} units).', 'success')

        log_activity('edit', 'professor_load', f'Updated assignments for Prof ID {prof_id} ({total_hours:.1f} hrs, {total_units:.1f} units)')
    except Exception as err:
        flash(f'Error updating assignments: {err}', 'danger')

    return redirect(url_for('professor_load'))
#-------------------------------------------------------edit_professor_load----------------------------------------------------------------------------------------------
@app.route('/edit_professor_load/<int:professor_load_id>', methods=['POST'])
@roles_required('scheduler')
def edit_professor_load(professor_load_id):
    try:
        prof_id = request.form['prof_id']
        course_id = request.form['course_id']

        supabase.table('professor_load').update({
            'prof_id': prof_id,
            'course_id': course_id,
        }).eq('id', professor_load_id).execute()
        log_activity('edit', 'professor_load', f'Prof-Course ID {professor_load_id}')
        flash('Edited successfully', 'success')
        return redirect(url_for('professor_load'))
    except Exception as err:
        return f"Error: {err}"
#-------------------------------------------------------delete_professor_load_all----------------------------------------------------------------------------------------------
@app.route('/delete_professor_load_all/<int:prof_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_professor_load_all(prof_id):
    is_ajax = _is_ajax_request()
    try:
        pl_rows = supabase.table('professor_load').select('id').eq('prof_id', prof_id).execute().data or []
        pl_ids = [r['id'] for r in pl_rows if r.get('id')]
        if pl_ids:
            in_sched = supabase.table('schedule').select('schedule_id').in_('professor_load_id', pl_ids).eq('archive', False).limit(1).execute()
            if in_sched.data:
                msg = 'Cannot delete all assignments for this professor because one or more are assigned in the active schedule.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'danger')
                return redirect(url_for('professor_load'))

        supabase.table('professor_load').delete().eq('prof_id', prof_id).execute()
        log_activity('delete', 'professor_load', f'All assignments for Prof ID {prof_id}')
        msg = 'All assignments for professor deleted successfully.'
        if is_ajax:
            return jsonify({'success': True, 'message': msg})
        flash(msg, 'success')
        return redirect(url_for('professor_load'))
    except Exception as err:
        msg = f'Error deleting assignments: {err}'
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 500
        flash(msg, 'danger')
        return redirect(url_for('professor_load'))
#-------------------------------------------------------delete_professor_load----------------------------------------------------------------------------------------------
@app.route('/delete_professor_load/<int:professor_load_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_professor_load(professor_load_id):
    is_ajax = _is_ajax_request()
    try:
        in_sched = supabase.table('schedule').select('schedule_id').eq('professor_load_id', professor_load_id).eq('archive', False).limit(1).execute()
        if in_sched.data:
            msg = 'Cannot delete course assignment because it is currently assigned in the active schedule.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('professor_load'))

        supabase.table('professor_load').delete().eq('id', professor_load_id).execute()
        log_activity('delete', 'professor_load', f'Prof-Course ID {professor_load_id}')
        msg = 'Assignment deleted successfully.'
        if is_ajax:
            return jsonify({'success': True, 'message': msg})
        flash(msg, 'success')
        return redirect(url_for('professor_load'))
    except Exception as err:
        msg = f'Error deleting assignment: {err}'
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 500
        flash(msg, 'danger')
        return redirect(url_for('professor_load'))
#-------------------------------------------------------professor_load_import----------------------------------------------------------------------------------------------
_PROFESSOR_LOAD_IMPORT_CACHE = {}

def _cleanup_import_cache():
    now = time.time()
    expired = [k for k, v in _PROFESSOR_LOAD_IMPORT_CACHE.items() if now - v.get('timestamp', 0) > 3600]
    for k in expired:
        _PROFESSOR_LOAD_IMPORT_CACHE.pop(k, None)

def _fetch_professor_load_import_context():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')

    # 1. Fetch professors
    cols_p = 'prof_id, first_name, last_name, specialization, program_id, academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)'
    try:
        q = supabase.table('professor').select(cols_p)
        if not is_super_admin and user_prog_id:
            q = q.eq('program_id', user_prog_id)
        professors = q.execute().data or []
    except Exception:
        try:
            q = supabase.table('professor').select('prof_id, first_name, last_name, specialization, program_id')
            if not is_super_admin and user_prog_id:
                q = q.eq('program_id', user_prog_id)
            professors = q.execute().data or []
        except Exception:
            professors = []

    # 2. Fetch courses
    cols_c = 'course_id, course_name, program_id, year_level, lecture_hours, lab_hours, ilp_hours, units, semester'
    try:
        cq = supabase.table('course').select(cols_c)
        if not is_super_admin and user_prog_id:
            cq = cq.eq('program_id', user_prog_id)
        courses = cq.execute().data or []
    except Exception:
        try:
            cq = supabase.table('course').select('*')
            if not is_super_admin and user_prog_id:
                cq = cq.eq('program_id', user_prog_id)
            courses = cq.execute().data or []
        except Exception:
            courses = []

    # 3. Fetch existing professor loads
    cols_pc = 'id, prof_id, course_id, sections, course(course_name, lecture_hours, lab_hours, ilp_hours, units)'
    try:
        existing_loads = supabase.table('professor_load').select(cols_pc).execute().data or []
    except Exception:
        try:
            existing_loads = supabase.table('professor_load').select('id, prof_id, course_id, sections').execute().data or []
        except Exception:
            existing_loads = []

    return professors, courses, existing_loads

def _check_import_access():
    if 'user_id' not in session:
        return False, "Authentication required. Please log in."
    role = session.get('role', '')
    if _normalize_role(role) != 'scheduler':
        return False, "Access denied. Insufficient permissions to import professor load."
    return True, None

@app.route('/professor_load/import/template', methods=['GET'])
@roles_required('scheduler')
def professor_load_import_template():
    allowed, msg = _check_import_access()
    if not allowed:
        flash(msg, 'error')
        return redirect(url_for('professor_load'))
    xlsx_bytes = professor_load_importer.generate_import_template_xlsx()
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='professor_load_template.xlsx'
    )

@app.route('/professor_load/import/preview', methods=['POST'])
@roles_required('scheduler')
def professor_load_import_preview():
    try:
        allowed, msg = _check_import_access()
        if not allowed:
            return jsonify({'ok': False, 'success': False, 'error': msg}), 403

        if 'file' not in request.files:
            return jsonify({'ok': False, 'success': False, 'error': 'No file uploaded.'}), 400

        file = request.files['file']
        if not file or not (file.filename or '').strip():
            return jsonify({'ok': False, 'success': False, 'error': 'No file uploaded.'}), 400

        filename = secure_filename(file.filename or '')
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.xlsx', '.csv'):
            return jsonify({'ok': False, 'success': False, 'error': 'Unsupported file type. Invalid file format. Please upload .xlsx or .csv.'}), 400

        file_bytes = file.read()
        if len(file_bytes) == 0:
            return jsonify({'ok': False, 'success': False, 'error': 'The uploaded file is empty.'}), 400
        if len(file_bytes) > professor_load_importer.MAX_IMPORT_FILE_SIZE:
            return jsonify({'ok': False, 'success': False, 'error': 'File size exceeds maximum allowed limit of 5 MB.'}), 413

        _cleanup_import_cache()

        data_rows, dividers, parse_errors = professor_load_importer.parse_import_file(file_bytes, filename)
        if parse_errors:
            return jsonify({'ok': False, 'success': False, 'error': parse_errors[0]}), 400

        professors, courses, existing_loads = _fetch_professor_load_import_context()

        validation_result = professor_load_importer.validate_import_data(
            data_rows, dividers, professors, courses, existing_loads
        )

        token = str(uuid.uuid4())
        _PROFESSOR_LOAD_IMPORT_CACHE[token] = {
            'bytes': file_bytes,
            'filename': filename,
            'timestamp': time.time(),
            'user_id': session.get('user_id'),
        }

        sanitized_rows = []
        for r in validation_result['rows']:
            item = dict(r)
            item.pop('matched_course_info', None)
            item.pop('matched_prof_info', None)
            sanitized_rows.append(item)

        return jsonify({
            'ok': True,
            'success': True,
            'token': token,
            'filename': filename,
            'summary': validation_result['summary'],
            'rows': sanitized_rows,
            'course_section_totals': validation_result['course_section_totals'],
            'professor_workloads': validation_result['professor_workloads'],
        })
    except Exception as e:
        logging.exception("Unhandled error in professor_load_import_preview")
        return jsonify({'ok': False, 'success': False, 'error': f"Failed to preview import file: {str(e)}"}), 500

@app.route('/professor_load/import/confirm', methods=['POST'])
@roles_required('scheduler')
def professor_load_import_confirm():
    try:
        allowed, msg = _check_import_access()
        if not allowed:
            return jsonify({'ok': False, 'success': False, 'error': msg}), 403

        token = request.form.get('token') or (request.json.get('token') if request.is_json else None)
        if not token or token not in _PROFESSOR_LOAD_IMPORT_CACHE:
            return jsonify({'ok': False, 'success': False, 'error': 'Import session expired or invalid. Please re-upload your file.'}), 400

        cache_entry = _PROFESSOR_LOAD_IMPORT_CACHE[token]
        file_bytes = cache_entry['bytes']
        filename = cache_entry['filename']

        data_rows, dividers, parse_errors = professor_load_importer.parse_import_file(file_bytes, filename)
        if parse_errors:
            return jsonify({'ok': False, 'success': False, 'error': parse_errors[0]}), 400

        professors, courses, existing_loads = _fetch_professor_load_import_context()
        validation_result = professor_load_importer.validate_import_data(
            data_rows, dividers, professors, courses, existing_loads
        )

        valid_rows = [r for r in validation_result['rows'] if r['status'] in ("Ready", "Updated", "Warning")]
        if not valid_rows:
            return jsonify({'ok': False, 'success': False, 'error': 'No valid rows to import. All rows contain errors.'}), 400

        updated_cnt, inserted_cnt, bulk_errors = professor_load_importer.execute_bulk_import(
            supabase, valid_rows, existing_loads
        )

        if bulk_errors:
            logging.error(f"Bulk import database error: {bulk_errors}")
            return jsonify({'ok': False, 'success': False, 'error': f"Database write failed: {bulk_errors[0]}"}), 500

        total_imported = updated_cnt + inserted_cnt
        total_skipped = validation_result['summary']['error_count']

        log_activity(
            'import',
            'professor_load',
            f'Imported {total_imported} course assignments ({inserted_cnt} new, {updated_cnt} updated, {total_skipped} skipped) from {filename}'
        )

        _PROFESSOR_LOAD_IMPORT_CACHE.pop(token, None)

        return jsonify({
            'ok': True,
            'success': True,
            'imported_count': total_imported,
            'new_count': inserted_cnt,
            'updated_count': updated_cnt,
            'skipped_count': total_skipped,
            'message': f'Successfully imported {total_imported} assignments ({inserted_cnt} new, {updated_cnt} updated). {total_skipped} rows skipped.'
        })
    except Exception as e:
        logging.exception("Unhandled error in professor_load_import_confirm")
        return jsonify({'ok': False, 'success': False, 'error': f"Database write failed: {str(e)}"}), 500

@app.route('/professor_load/import/error-report', methods=['POST'])
@roles_required('scheduler')
def professor_load_import_error_report():
    try:
        allowed, msg = _check_import_access()
        if not allowed:
            return jsonify({'ok': False, 'success': False, 'error': msg}), 403

        token = request.form.get('token') or (request.json.get('token') if request.is_json else None)
        if not token or token not in _PROFESSOR_LOAD_IMPORT_CACHE:
            return jsonify({'ok': False, 'success': False, 'error': 'Import session expired or invalid.'}), 400

        cache_entry = _PROFESSOR_LOAD_IMPORT_CACHE[token]
        file_bytes = cache_entry['bytes']
        filename = cache_entry['filename']

        data_rows, dividers, parse_errors = professor_load_importer.parse_import_file(file_bytes, filename)
        if parse_errors:
            return jsonify({'ok': False, 'success': False, 'error': parse_errors[0]}), 400

        professors, courses, existing_loads = _fetch_professor_load_import_context()
        validation_result = professor_load_importer.validate_import_data(
            data_rows, dividers, professors, courses, existing_loads
        )

        error_rows = [r for r in validation_result['rows'] if r['status'] == "Error"]
        xlsx_bytes = professor_load_importer.generate_error_report_xlsx(error_rows)

        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='import_error_report.xlsx'
        )
    except Exception as e:
        logging.exception("Unhandled error in professor_load_import_error_report")
        return jsonify({'ok': False, 'success': False, 'error': f"Failed to generate error report: {str(e)}"}), 500
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
    # Build one-hour candidate slots using timeslot rows that specify a single day or a day range
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    day_to_index = {d: i for i, d in enumerate(days)}
    candidate_slots = []

    for row in timeslots:
        start_time = _parse_time(row.get('start_time'))
        end_time = _parse_time(row.get('end_time'))
        lunch_time = _parse_time(row.get('lunch_time'))

        if start_time is None or end_time is None or end_time <= start_time:
            continue

        if row.get('day'):
            row_days = [row.get('day').strip().title()]
        else:
            # Read day range from row; default to Monday..Friday when missing/invalid
            start_day = (row.get('start_day') or 'Monday').strip().title()
            end_day = (row.get('end_day') or 'Friday').strip().title()
            start_idx = day_to_index.get(start_day, 0)
            end_idx = day_to_index.get(end_day, 4)
            if end_idx < start_idx:
                # if end is before start, treat as single-day
                end_idx = start_idx
            row_days = [days[idx] for idx in range(start_idx, end_idx + 1)]

        for day in row_days:
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


def _check_professor_cutoff_conflict(prof_id, day, start_time, end_time, session_type=None,
                                     prof_cutoff_map=None, day_cutoff_map=None):
    """Check if a professor's end time violates the day cutoff.

    Optimized path: when `prof_cutoff_map` and `day_cutoff_map` are supplied
    (pre-fetched once per generation run) no DB query is executed.
    Falls back to live DB queries when called without the cache maps.
    """
    if session_type == 'ILP':
        return None
    try:
        # --- Fast path: use pre-fetched maps (used during schedule generation) ---
        if prof_cutoff_map is not None and day_cutoff_map is not None:
            has_cutoff = prof_cutoff_map.get(prof_id, True)
            if not has_cutoff:
                return None
            cutoff_td = day_cutoff_map.get(day)
            if cutoff_td is None:
                return None
            if _to_seconds(end_time) > _to_seconds(cutoff_td):
                return {'message': f"Professor has a daily cutoff at {cutoff_td}."}
            return None

        # --- Slow path: live DB queries (called outside generation, e.g. edit_schedule_entry) ---
        prof_res = supabase.table('professor').select('academic_ranking(has_cutoff)').eq('prof_id', prof_id).limit(1).execute()
        if not prof_res.data:
            return None
        rank = prof_res.data[0].get('academic_ranking') or {}
        has_cutoff = rank.get('has_cutoff', True)
        if not has_cutoff:
            return None

        ts_res = supabase.table('timeslot').select('professor_cutoff').eq('day', day).execute()
        if not ts_res.data:
            return None

        end_td = _to_seconds(end_time)
        for ts in ts_res.data:
            cutoff = ts.get('professor_cutoff')
            if cutoff:
                cutoff_td = _to_seconds(cutoff)
                if end_td > cutoff_td:
                    return {'message': f"Professor has a daily cutoff at {cutoff}."}
    except Exception as e:
        logging.error(f"Cutoff check error: {e}")
    return None

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


def _find_room_schedule_conflict(room_id, day, start_time, end_time, exclude_schedule_id=None):
    """Return an active schedule occupying the same room and overlapping time, if any."""
    if room_id in (None, '', 0, '0') or not day or start_time is None or end_time is None:
        return None

    rows = (supabase.table('schedule').select(
        'schedule_id, room_id, day, class_start, class_end, section'
    ).eq('room_id', room_id).eq('day', day).eq('archive', False).execute().data) or []

    for row in rows:
        if exclude_schedule_id is not None and str(row.get('schedule_id')) == str(exclude_schedule_id):
            continue
        existing_start = row.get('class_start')
        existing_end = row.get('class_end')
        if existing_start is None or existing_end is None:
            continue
        if _has_conflict(day, start_time, end_time, [(row.get('day'), existing_start, existing_end)]):
            return row
    return None


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

    queue = []
    if lec_hours > 0 and lab_hours > 0:
        queue.append({'paired': True, 'lec_duration': lec_hours, 'lab_duration': lab_hours})
    elif lec_hours > 0:
        queue.append({'paired': False, 'session_type': 'Lecture', 'duration': lec_hours})
    elif lab_hours > 0:
        queue.append({'paired': False, 'session_type': 'Laboratory', 'duration': lab_hours})
        
    ilp_hours = int(float(course.get('ilp_hours') or 0))
    if ilp_hours > 0:
        queue.append({'paired': False, 'session_type': 'ILP', 'duration': ilp_hours, 'is_ilp': True})
        
    return queue


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
                    'course_name': ent.get('course_name') or ent.get('course') or '',
                    'professor': ent.get('professor') or ent.get('professor_name') or (prof_name if entity_type == 'professor' else ''),
                    'section': ent.get('section') or (section_name if entity_type == 'section' else ''),
                    'session_type': ent.get('session_type') or 'Lecture',
                    'room_name': ent.get('room_name') or ent.get('room') or (room_name if entity_type == 'room' else ''),
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

            assigned_prof = ent.get('professor') or ent.get('professor_name') or (prof_name if entity_type == 'professor' else '')
            assigned_sec = ent.get('section') or (section_name if entity_type == 'section' else '')
            assigned_room = ent.get('room_name') or ent.get('room') or (room_name if entity_type == 'room' else '')

            blocks.append({
                'id': ent.get('schedule_id') or ent.get('id') or f"occ_{d}_{clamped_s}",
                'schedule_id': ent.get('schedule_id') or ent.get('id'),
                'type': 'occupied',
                'professor_load_id': ent.get('professor_load_id'),
                'room_id': ent.get('room_id'),
                'course_name': ent.get('course_name') or ent.get('course') or '',
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
@roles_required('admin', 'scheduler', 'viewer')
def professor_schedule():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    program = session.get('program', '')

    active_semester = _get_active_semester(user_prog_id)

    prof_filter = request.args.get('prof_id', '').strip()
    day_filter = request.args.get('day', '').strip()
    year_filter = request.args.get('year', '').strip()
    major_filter = request.args.get('major', '').strip()
    semester_filter = request.args.get('semester', '').strip()

    if not semester_filter and active_semester:
        semester_filter = active_semester.get('term') or ''

    fetch_error = False
    error_message = None
    all_prof_options = []
    professors = []
    year_options = []
    semester_options = []
    major_options = []

    try:
        # Fetch all professors for filter dropdown
        p_query = supabase.table('professor').select('prof_id, first_name, last_name, specialization, time_designation, academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)')
        if not is_super_admin and user_prog_id:
            try:
                p_query = p_query.eq('program_id', user_prog_id)
            except Exception:
                pass
        all_prof_options = p_query.execute().data or []
        all_prof_options.sort(key=lambda p: (str(p.get('last_name') or ''), str(p.get('first_name') or '')))

        # Fetch schedules with pagination loop
        sched_cols = 'schedule_id, professor_load_id, section, semester, major, program_id, day, class_start, class_end, professor_load(prof_id, course_id, course(course_name)), room(room_name)'
        
        sched_rows = []
        page_size = 1000
        offset = 0
        program_id_filter = user_prog_id if (not is_super_admin and user_prog_id) else None
        while True:
            query = supabase.table('schedule').select(sched_cols).eq('archive', False)
            if program_id_filter:
                query = query.eq('program_id', program_id_filter)
            if hasattr(query, 'range'):
                batch = query.range(offset, offset + page_size - 1).execute().data or []
            else:
                batch = query.execute().data or []
            sched_rows.extend(batch)
            if len(batch) < page_size or offset > 50000 or not hasattr(query, 'range'):
                break
            offset += page_size

        year_options = sorted({_year_of_section(r.get('section')) for r in sched_rows if _year_of_section(r.get('section'))})
        semester_options = sorted({r.get('semester') for r in sched_rows if r.get('semester')})
        major_options = sorted({r.get('major') for r in sched_rows if r.get('major')})

        def _matches(r):
            if semester_filter and r.get('semester') != semester_filter:
                return False
            if year_filter and _year_of_section(r.get('section')) != year_filter:
                return False
            if major_filter and r.get('major') != major_filter:
                return False
            if day_filter and (r.get('day') or '').strip().title() != day_filter.strip().title():
                return False
            return True

        filtered = [r for r in sched_rows if _matches(r)]

        prof_entries = {}
        for r in filtered:
            pid = (r.get('professor_load') or {}).get('prof_id') or r.get('prof_id')
            if pid is not None:
                prof_entries.setdefault(str(pid), []).append(r)

        for p in all_prof_options:
            pid_str = str(p.get('prof_id'))
            if prof_filter and pid_str != prof_filter:
                continue

            entries = prof_entries.get(pid_str, [])
            if not prof_filter and not entries:
                continue

            ranking = _rel(p, 'academic_ranking') or {}
            ranking_name = ranking.get('name') or ''
            max_hours = ranking.get('max_hours') or 40

            p_wl = _calculate_professor_workload(entries, max_hours=max_hours)
            prof_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()

            professors.append({
                'professor_id': p.get('prof_id'),
                'professor_name': prof_name,
                'specialization': p.get('specialization') or '',
                'time_designation': p.get('time_designation') or 5,
                'ranking_name': ranking_name,
                'class_count': len(entries),
                'total_hours': p_wl['total_scheduled_hours_display'],
                'workload_pct': p_wl['workload_percentage'],
            })
    except Exception as err:
        logging.error(f"Error in professor_schedule: {err}")
        fetch_error = True
        error_message = f"Failed to load professor schedules: {err}"
        professors = []
        year_options = []
        semester_options = []
        major_options = []

    return render_template(
        'professor_schedule.html',
        active_page='professor_schedule',
        active_semester=active_semester,
        all_prof_options=all_prof_options,
        selected_prof_id=prof_filter,
        selected_day=day_filter,
        year_options=year_options,
        year_filter=year_filter,
        semester_options=semester_options,
        semester_filter=semester_filter,
        major_options=major_options,
        major_filter=major_filter,
        professors=professors,
        fetch_error=fetch_error,
        error_message=error_message,
    )


@app.route('/professor_schedule/<professor_id>')
@roles_required('admin', 'scheduler', 'viewer')
def view_professor_schedule(professor_id):
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    program = session.get('program', '')
    user_role = (session.get('role') or 'scheduler').lower()

    try:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name), academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)').eq('prof_id', professor_id).execute()
    except Exception:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])

    if not professor:
        return redirect(url_for('professor_schedule'))

    prog_obj = _rel(professor, 'program') or {}
    prog_name = prog_obj.get('program_name') or ''
    professor['program_name'] = prog_name
    professor['department'] = prog_name
    professor['program'] = prog_name

    professor_name = f"{professor['first_name']} {professor['last_name']}"
    ranking_limits = _ranking_constraints(professor)
    max_hours = ranking_limits['max_hours']

    pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, course(course_name)').eq('prof_id', professor_id).execute()
    pc_data = pc_res.data or []
    prof_pc_ids = {item['professor_load_id'] for item in pc_data if item.get('professor_load_id')}
    pc_course_names = {}
    for item in pc_data:
        pcid = item.get('professor_load_id')
        c = _rel(item, 'course') or {}
        if pcid and c.get('course_name'):
            pc_course_names[pcid] = c.get('course_name')

    preview_pool = _get_preview_for_user()
    has_preview = bool(preview_pool)
    is_preview = (mode == 'preview') and has_preview

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('professor_load_id')
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

            if user_role in ('super_admin', 'admin'):
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
                'professor_load_id': p_cid,
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
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name)), '
            'room(room_name)'
        ).eq('archive', False)
        if prof_pc_ids:
            query = query.in_('professor_load_id', list(prof_pc_ids))
        else:
            query = query.eq('professor_load_id', -1)

        if user_role in ('super_admin', 'admin'):
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
        else:
            user_program_id = _get_user_program_id()
            if user_program_id:
                query = query.eq('program_id', user_program_id)

        if year_filter:
            query = query.like('section', f'{year_filter}%')
        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.eq('major', major_filter)

        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'professor_load_id': row.get('professor_load_id'),
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
                          major_filter=major_filter, program=program, sort_day=sort_day,
                          current_theme=request.args.get('theme', 'Blue'),
                          theme_options=list(EXCEL_THEMES.keys()))


@app.route('/professor_schedule/<professor_id>/export')
@app.route('/export/professor_schedule/<professor_id>')
@roles_required('admin', 'scheduler', 'viewer')
def export_professor_schedule(professor_id):
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()
    theme_arg = request.args.get('theme', 'Blue').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    program = session.get('program', '')
    user_role = (session.get('role') or 'scheduler').lower()

    try:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name), academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)').eq('prof_id', professor_id).execute()
    except Exception:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])

    if not professor:
        flash("Professor not found", "error")
        return redirect(url_for('professor_schedule'))

    prog_obj = _rel(professor, 'program') or {}
    prog_name = prog_obj.get('program_name') or ''
    professor['program_name'] = prog_name
    professor['department'] = prog_name
    professor['program'] = prog_name

    professor_name = f"{professor.get('first_name', '')} {professor.get('last_name', '')}".strip() or 'Professor'

    pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, course(course_name)').eq('prof_id', professor_id).execute()
    pc_data = pc_res.data or []
    prof_pc_ids = {item['professor_load_id'] for item in pc_data if item.get('professor_load_id')}
    pc_course_names = {}
    for item in pc_data:
        pcid = item.get('professor_load_id')
        c = _rel(item, 'course') or {}
        if pcid and c.get('course_name'):
            pc_course_names[pcid] = c.get('course_name')

    preview_pool = _get_preview_for_user()
    has_preview = bool(preview_pool)
    is_preview = (mode == 'preview') and has_preview

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('professor_load_id')
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

            if user_role in ('super_admin', 'admin'):
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
                'course_name': course_name,
                'room': p_entry.get('room_name') or 'TBA',
                'day': p_entry.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'section': sec,
                'semester': sem,
                'major': maj,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'year_level': _year_of_section(sec),
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name)), '
            'room(room_name)'
        ).eq('archive', False)

        if user_role in ('super_admin', 'admin'):
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
        else:
            user_program_id = _get_user_program_id()
            if user_program_id:
                query = query.eq('program_id', user_program_id)

        if year_filter:
            query = query.like('section', f'{year_filter}%')
        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.eq('major', major_filter)

        rows = query.execute().data or []
        for row in rows:
            pc_id = row.get('professor_load_id')
            if pc_id not in prof_pc_ids:
                continue
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row.get('schedule_id'),
                'course_name': c.get('course_name') or 'TBA',
                'room': r.get('room_name') or 'TBA',
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'section': row.get('section'),
                'semester': row.get('semester'),
                'major': row.get('major'),
                'session_type': row.get('session_type') or 'Lecture',
                'year_level': _year_of_section(row.get('section')),
            })

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    clean_prof = re.sub(r'[^a-zA-Z0-9_-]', '_', professor_name)
    filename = f"Teacher_Schedule_{clean_prof}.xlsx"

    excel_buffer = generate_timetable_excel(
        schedule_type='professor',
        entity_info=professor,
        entries=entries,
        timeslots=timeslots,
        filter_metadata={
            'semester': semester_filter,
            'year': year_filter,
            'major': major_filter,
            'program': program_filter or program,
        },
        theme=theme_arg
    )

    return send_file(
        excel_buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


@app.route('/professor_schedule/<professor_id>/export_pdf')
@app.route('/export/professor_schedule/<professor_id>/pdf')
@roles_required('admin', 'scheduler', 'viewer')
def export_professor_schedule_pdf(professor_id):
    year_filter = (request.args.get('year') or '').strip()
    semester_filter = (request.args.get('semester') or '').strip()
    major_filter = (request.args.get('major') or '').strip()
    program_filter = (request.args.get('program') or '').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    # Empty filters or 'all' must mean "no filter"
    if year_filter.lower() in ('all', 'all years', 'none', ''):
        year_filter = ''
    if semester_filter.lower() in ('all', 'all semesters', 'none', ''):
        semester_filter = ''
    if major_filter.lower() in ('all', 'all majors', 'none', ''):
        major_filter = ''
    if program_filter.lower() in ('all', 'all programs', 'none', ''):
        program_filter = ''

    program = session.get('program', '')
    user_role = (session.get('role') or 'scheduler').lower()

    try:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name), academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)').eq('prof_id', professor_id).execute()
    except Exception:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, academic_ranking_id, academic_ranking(name, min_units, max_units, min_hours, max_hours)').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])

    if not professor:
        flash("Professor not found.", "error")
        return redirect(url_for('professor_schedule'))

    prog_obj = _rel(professor, 'program') or {}
    prog_name = prog_obj.get('program_name') or ''
    professor['program_name'] = prog_name
    professor['department'] = prog_name
    professor['program'] = prog_name

    professor_name = f"{professor.get('first_name', '')} {professor.get('last_name', '')}".strip() or 'Professor'

    pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, course(course_name)').eq('prof_id', professor_id).execute()
    pc_data = pc_res.data or []
    prof_pc_ids = {item['professor_load_id'] for item in pc_data if item.get('professor_load_id')}
    pc_course_names = {}
    for item in pc_data:
        pcid = item.get('professor_load_id')
        c = _rel(item, 'course') or {}
        if pcid and c.get('course_name'):
            pc_course_names[pcid] = c.get('course_name')

    preview_pool = _get_preview_for_user()
    has_preview = bool(preview_pool)
    is_preview = (mode == 'preview') and has_preview

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('professor_load_id')
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

            if user_role in ('super_admin', 'admin'):
                if program_filter and prog != program_filter:
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
                'course_name': course_name,
                'room': p_entry.get('room_name') or 'TBA',
                'day': p_entry.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'section': sec,
                'semester': sem,
                'major': maj,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'year_level': _year_of_section(sec),
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name)), '
            'room(room_name)'
        ).eq('archive', False)

        if user_role in ('super_admin', 'admin'):
            if program_filter:
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
        else:
            user_program_id = _get_user_program_id()
            if user_program_id:
                query = query.eq('program_id', user_program_id)

        if year_filter:
            query = query.like('section', f'{year_filter}%')
        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.eq('major', major_filter)

        rows = query.execute().data or []
        for row in rows:
            pc_id = row.get('professor_load_id')
            if pc_id not in prof_pc_ids:
                continue
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row.get('schedule_id'),
                'course_name': c.get('course_name') or 'TBA',
                'room': r.get('room_name') or 'TBA',
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'section': row.get('section'),
                'semester': row.get('semester'),
                'major': row.get('major'),
                'session_type': row.get('session_type') or 'Lecture',
                'year_level': _year_of_section(row.get('section')),
            })

    if not entries:
        flash(f"No active schedule entries found to export for Professor {professor_name}.", "info")
        return redirect(url_for('view_professor_schedule', professor_id=professor_id, year=year_filter, semester=semester_filter, major=major_filter, mode=mode if is_preview else None))

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    clean_prof = re.sub(r'[^a-zA-Z0-9_-]', '_', professor_name)
    sem_suffix = f"_{re.sub(r'[^a-zA-Z0-9_-]', '_', semester_filter)}" if semester_filter else ""
    filename = f"Teacher_Schedule_{clean_prof}{sem_suffix}.pdf"

    active_sem = _get_active_semester(_get_user_program_id())
    school_year = active_sem.get('school_year') if active_sem and active_sem.get('school_year') else "2026-2027"

    pdf_buffer = generate_timetable_pdf(
        schedule_type='professor',
        entity_info=professor,
        entries=entries,
        timeslots=timeslots,
        filter_metadata={
            'semester': semester_filter,
            'year': year_filter,
            'major': major_filter,
            'program': program_filter or program,
            'school_year': school_year,
        }
    )

    pdf_buffer.seek(0)
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )




@app.route('/api/professor_availability/<int:professor_id>', methods=['GET'])
@roles_required('admin', 'scheduler', 'viewer')
def api_professor_availability(professor_id):
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    try:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name)').eq('prof_id', professor_id).execute()
    except Exception:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])
    if not professor:
        return jsonify({'success': False, 'error': 'Professor not found.'}), 404

    prog_obj = _rel(professor, 'program') or {}
    prog_name = prog_obj.get('program_name') or ''
    professor['program_name'] = prog_name
    professor['department'] = prog_name
    professor['program'] = prog_name

    prof_name = f"{professor.get('first_name','')} {professor.get('last_name','')}".strip()
    pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, course(course_name)').eq('prof_id', professor_id).execute()
    pc_data = pc_res.data or []
    prof_pc_ids = {item['professor_load_id'] for item in pc_data if item.get('professor_load_id')}
    pc_course_names = {item['professor_load_id']: (_rel(item, 'course') or {}).get('course_name') for item in pc_data if item.get('professor_load_id')}

    preview_pool = _get_preview_for_user()
    is_preview = (mode == 'preview') and bool(preview_pool)

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('professor_load_id')
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
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, session_type, '
            'professor_load(professor_load_id:id, prof_id, course(course_name)), room(room_name)'
        ).eq('archive', False)
        if prof_pc_ids:
            query = query.in_('professor_load_id', list(prof_pc_ids))
        else:
            query = query.eq('professor_load_id', -1)
        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
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
@roles_required('admin', 'scheduler', 'viewer')
def api_professor_workload(professor_id):
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    try:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name)').eq('prof_id', professor_id).execute()
    except Exception:
        prof_res = supabase.table('professor').select('prof_id, first_name, last_name').eq('prof_id', professor_id).execute()
    professor = _first(prof_res.data or [])
    if not professor:
        return jsonify({'error': 'Professor not found.'}), 404

    prog_obj = _rel(professor, 'program') or {}
    prog_name = prog_obj.get('program_name') or ''
    professor['program_name'] = prog_name
    professor['department'] = prog_name
    professor['program'] = prog_name

    prof_name = f"{professor.get('first_name','')} {professor.get('last_name','')}".strip()
    ranking_limits = _ranking_constraints(professor)
    max_hours = ranking_limits['max_hours']

    pc_res = supabase.table('professor_load').select('professor_load_id:id').eq('prof_id', professor_id).execute()
    prof_pc_ids = {item['professor_load_id'] for item in (pc_res.data or []) if item.get('professor_load_id')}

    preview_pool = _get_preview_for_user()
    is_preview = (mode == 'preview') and bool(preview_pool)

    entries = []
    if is_preview:
        for p_entry in preview_pool:
            p_cid = p_entry.get('professor_load_id')
            p_pid = p_entry.get('prof_id')
            if (p_cid and p_cid in prof_pc_ids) or (p_pid and str(p_pid) == str(professor_id)):
                entries.append(p_entry)
    else:
        query = supabase.table('schedule').select('class_start, class_end, day, professor_load_id').eq('archive', False)
        if prof_pc_ids:
            query = query.in_('professor_load_id', list(prof_pc_ids))
        else:
            query = query.eq('professor_load_id', -1)
        entries = query.execute().data or []

    workload = _calculate_professor_workload(entries, max_hours=max_hours)
    return jsonify({
        'professor_id': professor_id,
        'professor_name': prof_name,
        'is_preview': is_preview,
        **workload
    })


@app.route('/room_schedule')
@roles_required('admin', 'scheduler', 'viewer')
def room_schedule():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    is_super_admin = user_role in ('super_admin', 'admin')
    program = session.get('program', '')

    active_semester = _get_active_semester(user_prog_id)

    room_filter = request.args.get('room_id', '').strip()
    day_filter = request.args.get('day', '').strip()
    year_filter = request.args.get('year', '').strip()
    major_filter = request.args.get('major', '').strip()
    semester_filter = request.args.get('semester', '').strip()
    program_filter = request.args.get('program', '').strip()

    if not semester_filter and active_semester:
        semester_filter = active_semester.get('term') or ''

    # Fetch all rooms for filter dropdown and display
    try:
        r_query = supabase.table('room').select('room_id, room_name, room_type')
        if not is_super_admin and user_prog_id:
            try:
                r_query = r_query.eq('program_id', user_prog_id)
            except Exception:
                pass
        all_room_options = r_query.execute().data or []
        all_room_options.sort(key=lambda r: str(r.get('room_name') or ''))
    except Exception:
        all_room_options = []

    try:
        sched_cols = 'schedule_id, professor_load_id, room_id, section, semester, major, program_id, day, class_start, class_end'
        query = supabase.table('schedule').select(sched_cols).eq('archive', False)

        if not is_super_admin and user_prog_id:
            query = query.eq('program_id', user_prog_id)
        elif is_super_admin and program_filter and program_filter.lower() != 'all':
            p_id = int(program_filter) if program_filter.isdigit() else _find_program_id_by_name(program_filter)
            query = query.eq('program_id', p_id or -1)

        sched_rows = query.execute().data or []

        year_options = sorted({_year_of_section(r.get('section')) for r in sched_rows if _year_of_section(r.get('section'))})
        semester_options = sorted({r.get('semester') for r in sched_rows if r.get('semester')})
        major_options = sorted({r.get('major') for r in sched_rows if r.get('major')})

        def _matches(r):
            if semester_filter and r.get('semester') != semester_filter:
                return False
            if year_filter and _year_of_section(r.get('section')) != year_filter:
                return False
            if major_filter and r.get('major') != major_filter:
                return False
            if day_filter and (r.get('day') or '').strip().title() != day_filter.strip().title():
                return False
            return True

        filtered = [r for r in sched_rows if _matches(r)]
        room_count = {}
        for r in filtered:
            rid = r.get('room_id')
            if rid is not None:
                room_count[rid] = room_count.get(rid, 0) + 1

        rooms = []
        for rr in all_room_options:
            rid = rr.get('room_id')
            if room_filter and str(rid) != room_filter:
                continue
            if not room_filter and rid not in room_count:
                continue
            rooms.append({
                'room_id': rid,
                'room_name': rr.get('room_name'),
                'room_type': rr.get('room_type'),
                'class_count': room_count.get(rid, 0),
            })
        rooms.sort(key=lambda x: str(x.get('room_name') or ''))
    except Exception as err:
        logging.error(f"Error in room_schedule: {err}")
        rooms = []
        year_options = []
        semester_options = []
        major_options = []

    return render_template(
        'room_schedule.html',
        active_page='room_schedule',
        active_semester=active_semester,
        all_room_options=all_room_options,
        selected_room_id=room_filter,
        selected_day=day_filter,
        rooms=rooms,
        year_options=year_options,
        semester_options=semester_options,
        major_options=major_options,
        year_filter=year_filter,
        semester_filter=semester_filter,
        major_filter=major_filter,
        program=program,
        no_professor_match=False
    )


@app.route('/room_schedule/<room_id>')
@roles_required('admin', 'scheduler', 'viewer')
def view_room_schedule(room_id):
    day_filter = request.args.get('day', '').strip()
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    program = session.get('program', '')
    user_role = (session.get('role') or '').lower()

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

            if user_role in ('super_admin', 'admin'):
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
            if day_filter and (p_entry.get('day') or '').strip().title() != day_filter.title():
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            entries.append({
                'schedule_id': p_entry.get('id'),
                'course_name': p_entry.get('course_name') or '',
                'professor': p_entry.get('professor_name') or '',
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
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        ).eq('room_id', room_id).eq('archive', False)

        if user_role in ('super_admin', 'admin'):
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
        else:
            user_program_id = _get_user_program_id()
            if user_program_id:
                query = query.eq('program_id', user_program_id)

        if year_filter:
            query = query.like('section', f'{year_filter}%')
        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.eq('major', major_filter)
        if day_filter:
            query = query.ilike('day', day_filter)

        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'course_name': c.get('course_name') or '',
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
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
                          major_filter=major_filter, program=program, sort_day=sort_day,
                          current_theme=request.args.get('theme', 'Blue'),
                          theme_options=list(EXCEL_THEMES.keys()))


@app.route('/room_schedule/<room_id>/export')
@app.route('/export/room_schedule/<room_id>')
@roles_required('admin', 'scheduler', 'viewer')
def export_room_schedule(room_id):
    year_filter = request.args.get('year', '')
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    program_filter = request.args.get('program', '').strip()
    theme_arg = request.args.get('theme', 'Blue').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    program = session.get('program', '')
    user_role = (session.get('role') or '').lower()

    room_res = supabase.table('room').select('room_id, room_name, room_type').eq('room_id', room_id).execute()
    room = _first(room_res.data or [])

    if not room:
        flash("Room not found", "error")
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

            if user_role in ('super_admin', 'admin'):
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
                'course_name': p_entry.get('course_name') or '',
                'professor': p_entry.get('professor_name') or '',
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
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, semester, major, session_type, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        ).eq('room_id', room_id).eq('archive', False)

        if user_role in ('super_admin', 'admin'):
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
        else:
            user_program_id = _get_user_program_id()
            if user_program_id:
                query = query.eq('program_id', user_program_id)

        if year_filter:
            query = query.like('section', f'{year_filter}%')
        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.eq('major', major_filter)

        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row.get('schedule_id'),
                'course_name': c.get('course_name') or '',
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
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

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', str(room.get('room_name') or 'Room'))
    filename = f"Room_Schedule_{clean_name}.xlsx"

    excel_buffer = generate_timetable_excel(
        schedule_type='room',
        entity_info=room,
        entries=entries,
        timeslots=timeslots,
        filter_metadata={
            'semester': semester_filter,
            'year': year_filter,
            'major': major_filter,
            'program': program_filter or program,
        },
        theme=theme_arg
    )

    return send_file(
        excel_buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )




@app.route('/api/room_availability/<room_id>', methods=['GET'])
@roles_required('admin', 'scheduler', 'viewer')
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
                        'course_name': p_entry.get('course_name') or '',
                        'professor': p_entry.get('professor_name') or '',
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
                'schedule_id, professor_load_id, room_id, day, class_start, class_end, section, session_type, '
                'professor_load(course(course_name), professor(first_name, last_name))'
            ).eq('room_id', room_id).eq('archive', False).execute().data) or []

            for row in rows:
                pc = _rel(row, 'professor_load') or {}
                c = _rel(pc, 'course') or _rel(row, 'course') or {}
                p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
                st_raw = row.get('class_start')
                et_raw = row.get('class_end')
                entries.append({
                    'schedule_id': row.get('schedule_id'),
                    'course_name': c.get('course_name') or '',
                    'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
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
@roles_required('admin', 'scheduler', 'viewer')
def schedules():
    year_filter = (request.args.get('year') or '').strip()
    semester_filter = (request.args.get('semester') or '').strip()
    major_filter = (request.args.get('major') or '').strip()
    program_filter = (request.args.get('program') or '').strip()

    program = session.get('program', '')
    user_program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

    try:
        sched_cols = (
            'schedule_id, professor_load_id, section, semester, major, program_id, day, class_start, class_end, session_type, room_id, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        )
        query = supabase.table('schedule').select(sched_cols).eq('archive', False)

        if user_role in ('super_admin', 'admin'):
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
        else:
            if program_filter and program_filter.lower() != 'all':
                query = query.eq('program_id', _find_program_id_by_name(program_filter) or -1)
            elif user_program_id:
                query = query.eq('program_id', user_program_id)

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

        target_rows = [r for r in sched_rows if _matches(r)]

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
                    'theme': get_section_theme(sec),
                })

            if key not in sections_by_key:
                sections_by_key[key] = {
                    'section': {
                        'section': sec,
                        'section_name': sec,
                        'semester': semester,
                        'major': major_key,
                        'year_level': _year_of_section(sec),
                        'theme': get_section_theme(sec),
                    },
                    'entries': []
                }

            if r.get('schedule_id'):
                pc = _rel(r, 'professor_load') or {}
                c = _rel(pc, 'course') or _rel(r, 'course') or {}
                rm = _rel(r, 'room') or {}
                p = _rel(pc, 'professor') or _rel(r, 'professor') or {}
                fname = p.get('first_name') or ''
                lname = p.get('last_name') or ''
                prof_name = f"{fname} {lname}".strip() or ''
                start_fmt = _format_time(r.get('class_start'))
                end_fmt = _format_time(r.get('class_end'))
                sections_by_key[key]['entries'].append({
                    'id': r.get('schedule_id'),
                    'schedule_id': r.get('schedule_id'),
                    'professor_load_id': r.get('professor_load_id'),
                    'course_id': pc.get('course_id') or r.get('course_id'),
                    'course_name': c.get('course_name') or '',
                    'professor_name': prof_name,
                    'room_name': rm.get('room_name') or '',
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
        no_professor_match=False,
        theme_options=list(EXCEL_THEMES.keys())
    )


@app.route('/schedule/<section_name>')
@roles_required('admin', 'scheduler', 'viewer')
def view_schedule(section_name):
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    program = session.get('program', '')
    user_program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

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

            if user_role not in ('super_admin', 'admin') and program and prog and prog != program:
                continue

            if semester_filter and sem != semester_filter:
                continue
            if major_filter and maj != major_filter:
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            prof_name = p_entry.get('professor_name') or ''

            entries.append({
                'schedule_id': p_entry.get('id'),
                'professor_load_id': p_entry.get('professor_load_id'),
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
                'course_name': p_entry.get('course_name') or '',
                'room_name': p_entry.get('room_name') or '',
                'first_name': '',
                'last_name': '',
                'professor': prof_name,
                'professor_name': prof_name,
                'section': section_name,
                'year_level': _year_of_section(section_name),
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else '',
                'timeslot_display': f"{p_entry.get('day')} | {st_fmt} - {et_fmt}" if st_fmt and et_fmt else '',
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, day, class_start, class_end, session_type, semester, major, professor_load_id, room_id, '
            'professor_load(id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        ).eq('section', section_name).eq('archive', False)

        if user_role not in ('super_admin', 'admin') and user_program_id:
            query = query.eq('program_id', user_program_id)

        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.or_(f'major.eq.{major_filter},major.is.null')

        rows = query.execute().data or []

        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            r = _rel(row, 'room') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            first_name = p.get('first_name') or ''
            last_name = p.get('last_name') or ''
            prof_name = f"{first_name} {last_name}".strip() or ''
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entry = {
                'schedule_id': row['schedule_id'],
                'professor_load_id': row.get('professor_load_id'),
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

    user_prog_id = _get_user_program_id()
    r_query = supabase.table('room').select('room_id, room_name, room_type, program_id')
    if user_prog_id:
        rooms = (r_query.eq('program_id', user_prog_id).execute().data) or []
        if not rooms:
            rooms = (supabase.table('room').select('room_id, room_name, room_type, program_id').execute().data) or []
    else:
        rooms = (r_query.execute().data) or []

    pc_query = supabase.table('professor_load').select(
        'professor_load_id:id, course_id, prof_id, '
        'course(course_id, course_name, program_id, program:program_id(program_name)), '
        'professor(prof_id, first_name, last_name, program_id, program:program_id(program_name))'
    )
    if user_prog_id:
        pc_rows = (pc_query.eq('professor.program_id', user_prog_id).execute().data) or []
        if not pc_rows:
            pc_rows = (supabase.table('professor_load').select(
                'professor_load_id:id, course_id, prof_id, '
                'course(course_id, course_name, program_id, program:program_id(program_name)), '
                'professor(prof_id, first_name, last_name, program_id, program:program_id(program_name))'
            ).execute().data) or []
    else:
        pc_rows = (pc_query.execute().data) or []

    all_professor_loads = []
    for pc_item in pc_rows:
        pcid = pc_item.get('professor_load_id')
        p_obj = _rel(pc_item, 'professor') or {}
        c_obj = _rel(pc_item, 'course') or {}
        p_name = f"{p_obj.get('first_name') or ''} {p_obj.get('last_name') or ''}".strip() or ''
        c_name = c_obj.get('course_name') or f"Course #{pc_item.get('course_id')}"
        all_professor_loads.append({
            'professor_load_id': pcid,
            'prof_id': pc_item.get('prof_id'),
            'course_id': pc_item.get('course_id'),
            'label': f"{p_name} - {c_name}",
            'prof_name': p_name,
            'course_name': c_name,
        })
    all_professor_loads.sort(key=lambda x: x['label'])

    current_theme = get_section_theme(section_name)
    theme_options = list(EXCEL_THEMES.keys())

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
        all_professor_loads=all_professor_loads,
        current_theme=current_theme,
        theme_options=theme_options
    )


@app.route('/schedule/<section_name>/export')
@app.route('/export/section_schedule/<section_name>')
@roles_required('admin', 'scheduler', 'viewer')
def export_section_schedule(section_name):
    semester_filter = request.args.get('semester', '')
    major_filter = request.args.get('major', '')
    theme_arg = request.args.get('theme', '').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()
    program = session.get('program', '')
    user_program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

    if theme_arg:
        selected_theme = set_section_theme(section_name, theme_arg)
    else:
        selected_theme = get_section_theme(section_name)

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

            if user_role not in ('super_admin', 'admin') and program and prog and prog != program:
                continue

            if semester_filter and sem != semester_filter:
                continue
            if major_filter and maj != major_filter:
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            prof_name = p_entry.get('professor_name') or ''

            entries.append({
                'schedule_id': p_entry.get('id'),
                'day': p_entry.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'semester': sem,
                'major': maj,
                'course_name': p_entry.get('course_name') or '',
                'room': p_entry.get('room_name') or '',
                'room_name': p_entry.get('room_name') or '',
                'professor': prof_name,
                'professor_name': prof_name,
                'section': section_name,
                'year_level': _year_of_section(section_name),
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, day, class_start, class_end, session_type, semester, major, professor_load_id, room_id, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        ).eq('section', section_name).eq('archive', False)

        if user_role not in ('super_admin', 'admin') and user_program_id:
            query = query.eq('program_id', user_program_id)

        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.eq('major', major_filter)

        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row.get('schedule_id'),
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'session_type': row.get('session_type') or 'Lecture',
                'semester': row.get('semester'),
                'major': row.get('major'),
                'course_name': c.get('course_name') or '',
                'room': r.get('room_name') or '',
                'room_name': r.get('room_name') or '',
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
                'section': section_name,
                'year_level': _year_of_section(section_name),
            })

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    clean_sec = re.sub(r'[^a-zA-Z0-9_-]', '_', str(section_name))
    filename = f"Section_Schedule_{clean_sec}.xlsx"

    section_info = {
        'section_name': section_name,
        'year_level': _year_of_section(section_name),
        'semester': semester_filter or (entries[0].get('semester', '') if entries else ''),
        'major': major_filter or (entries[0].get('major', '') if entries else ''),
    }

    excel_buffer = generate_timetable_excel(
        schedule_type='section',
        entity_info=section_info,
        entries=entries,
        timeslots=timeslots,
        filter_metadata={
            'semester': section_info['semester'],
            'year': section_info['year_level'],
            'major': section_info['major'],
            'program': program,
        },
        theme=selected_theme
    )

    return send_file(
        excel_buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


@app.route('/schedule/<section_name>/export_pdf')
@app.route('/export/section_schedule/<section_name>/pdf')
@roles_required('admin', 'scheduler', 'viewer')
def export_section_schedule_pdf(section_name):
    year_filter = (request.args.get('year') or '').strip()
    semester_filter = (request.args.get('semester') or '').strip()
    major_filter = (request.args.get('major') or '').strip()
    program_filter = (request.args.get('program') or '').strip()
    mode = (request.args.get('mode') or request.args.get('source') or '').strip().lower()

    # Empty filters or 'all' must mean "no filter"
    if year_filter.lower() in ('all', 'all years', 'none', ''):
        year_filter = ''
    if semester_filter.lower() in ('all', 'all semesters', 'none', ''):
        semester_filter = ''
    if major_filter.lower() in ('all', 'all majors', 'none', ''):
        major_filter = ''
    if program_filter.lower() in ('all', 'all programs', 'none', ''):
        program_filter = ''

    program = session.get('program', '')
    user_program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

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

            if user_role not in ('super_admin', 'admin') and program and prog and prog != program:
                continue

            if semester_filter and sem != semester_filter:
                continue
            if major_filter and maj and maj != major_filter:
                continue

            st = p_entry.get('start')
            et = p_entry.get('end')
            st_fmt = _format_time(st) or str(st or '')
            et_fmt = _format_time(et) or str(et or '')
            prof_name = p_entry.get('professor_name') or ''

            entries.append({
                'schedule_id': p_entry.get('id'),
                'day': p_entry.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st,
                'end_time_raw': et,
                'session_type': p_entry.get('session_type') or 'Lecture',
                'semester': sem,
                'major': maj,
                'course_name': p_entry.get('course_name') or '',
                'room': p_entry.get('room_name') or '',
                'room_name': p_entry.get('room_name') or '',
                'professor': prof_name,
                'professor_name': prof_name,
                'section': section_name,
                'year_level': _year_of_section(section_name),
            })
    else:
        query = supabase.table('schedule').select(
            'schedule_id, day, class_start, class_end, session_type, semester, major, professor_load_id, room_id, '
            'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
            'room(room_name)'
        ).eq('section', section_name).eq('archive', False)

        if user_role not in ('super_admin', 'admin') and user_program_id:
            query = query.eq('program_id', user_program_id)

        if semester_filter:
            query = query.eq('semester', semester_filter)
        if major_filter:
            query = query.or_(f'major.eq.{major_filter},major.is.null')

        rows = query.execute().data or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or _rel(row, 'course') or {}
            p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
            r = _rel(row, 'room') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row.get('schedule_id'),
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'session_type': row.get('session_type') or 'Lecture',
                'semester': row.get('semester'),
                'major': row.get('major'),
                'course_name': c.get('course_name') or '',
                'room': r.get('room_name') or '',
                'room_name': r.get('room_name') or '',
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
                'professor_name': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
                'section': section_name,
                'year_level': _year_of_section(section_name),
            })

    if not entries:
        flash(f"No active schedule entries found to export for Section {section_name}.", "info")
        return redirect(url_for('view_schedule', section_name=section_name, semester=semester_filter, major=major_filter, mode=mode if is_preview else None))

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    clean_sec = re.sub(r'[^a-zA-Z0-9_-]', '_', str(section_name))
    sem_suffix = f"_{re.sub(r'[^a-zA-Z0-9_-]', '_', semester_filter)}" if semester_filter else ""
    filename = f"Section_Schedule_{clean_sec}{sem_suffix}.pdf"

    active_sem = _get_active_semester(user_program_id)
    school_year = f"S.Y. {active_sem.get('school_year')}" if active_sem and active_sem.get('school_year') else "A.Y. 2026-2027"

    section_info = {
        'section_name': section_name,
        'year_level': _year_of_section(section_name),
        'semester': semester_filter or (entries[0].get('semester', '') if entries else ''),
        'major': major_filter or (entries[0].get('major', '') if entries else ''),
    }

    pdf_buffer = generate_timetable_pdf(
        schedule_type='section',
        entity_info=section_info,
        entries=entries,
        timeslots=timeslots,
        filter_metadata={
            'semester': section_info['semester'],
            'year': section_info['year_level'],
            'major': section_info['major'],
            'program': program_filter or program,
            'school_year': school_year,
        }
    )

    pdf_buffer.seek(0)
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )




@app.route('/api/section_theme', methods=['GET', 'POST'])
@roles_required('admin', 'scheduler', 'viewer')
def api_section_theme():
    """Get or update section export theme assignment."""
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        section = (data.get('section') or data.get('section_name') or '').strip()
        theme = (data.get('theme') or '').strip()
        if not section:
            return jsonify({'success': False, 'error': 'Section name is required'}), 400
        # Validate against 7 allowed themes (case-insensitive)
        matched = None
        for t_name in EXCEL_THEMES:
            if t_name.lower() == theme.lower():
                matched = t_name
                break
        if not matched:
            return jsonify({'success': False, 'error': f'Invalid theme. Allowed: {list(EXCEL_THEMES.keys())}'}), 400

        saved_theme = set_section_theme(section, matched)
        return jsonify({'success': True, 'section': section, 'theme': saved_theme})

    section = request.args.get('section', '').strip()
    if section:
        return jsonify({'section': section, 'theme': get_section_theme(section)})
    return jsonify({'themes': get_all_section_themes(), 'allowed_themes': list(EXCEL_THEMES.keys())})




@app.route('/api/section_availability/<section_name>', methods=['GET'])
@roles_required('admin', 'scheduler', 'viewer')
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
                    'course_name': p_entry.get('course_name') or '',
                    'room_name': p_entry.get('room_name') or '',
                    'day': p_entry.get('day'),
                    'start_time': st_fmt,
                    'end_time': et_fmt,
                    'start_time_raw': st,
                    'end_time_raw': et,
                    'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else '',
                    'section': section_name,
                    'session_type': p_entry.get('session_type') or 'Lecture',
                    'professor': p_entry.get('professor_name') or '',
                })
    else:
        rows = (supabase.table('schedule').select(
            'schedule_id, day, class_start, class_end, session_type, professor_load(course(course_name), professor(first_name, last_name)), room(room_name)'
        ).eq('section', section_name).eq('archive', False).execute().data) or []
        for row in rows:
            pc = _rel(row, 'professor_load') or {}
            c = _rel(pc, 'course') or {}
            r = _rel(row, 'room') or {}
            p = _rel(pc, 'professor') or {}
            st_raw = row.get('class_start')
            et_raw = row.get('class_end')
            st_fmt = _format_time(st_raw) or str(st_raw or '')
            et_fmt = _format_time(et_raw) or str(et_raw or '')
            entries.append({
                'schedule_id': row['schedule_id'],
                'course_name': c.get('course_name') or '',
                'room_name': r.get('room_name') or '',
                'day': row.get('day'),
                'start_time': st_fmt,
                'end_time': et_fmt,
                'start_time_raw': st_raw,
                'end_time_raw': et_raw,
                'time_range': f"{st_fmt} - {et_fmt}" if st_fmt and et_fmt else '',
                'section': section_name,
                'session_type': row.get('session_type') or 'Lecture',
                'professor': f"{p.get('first_name','')} {p.get('last_name','')}".strip() or '',
            })

    try:
        timeslots = (supabase.table('timeslot').select('*').execute().data) or []
    except Exception:
        timeslots = []

    avail = _calculate_section_availability(entries, timeslots=timeslots, section=section_name)
    return jsonify({'success': True, 'availability': avail})



@app.route('/delete_schedule/<int:schedule_id>')
@roles_required('scheduler')
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
        supabase.table('schedule').update({'archive': True}).eq('schedule_id', schedule_id).execute()
        _set_delete_request_status({'item_type': 'schedule', 'item_id': str(schedule_id), 'status': 'pending'}, 'approved')
        log_activity('archive', 'schedule', f'Schedule ID {schedule_id} in {section_name}')
        flash('Schedule moved to archive successfully', 'success')
    except Exception as err:
        return f"Error: {err}"

    if section_name:
        return redirect(url_for('view_schedule', section_name=section_name, semester=semester, major=major))
    return redirect(url_for('schedules'))


@app.route('/delete_section_schedule/<section_name>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_section_schedule(section_name):
    major = request.args.get('major', '')
    semester = request.args.get('semester', '')
    program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

    handled, resp = _request_delete_if_scheduler('section_schedule', section_name, f'Section Schedule {section_name}')
    if handled:
        return resp or redirect(url_for('schedules'))

    try:
        query = supabase.table('schedule').update({'archive': True}).eq('section', section_name)

        if user_role not in ('super_admin', 'admin') and program_id:
            query = query.eq('program_id', program_id)

        if semester:
            query = query.eq('semester', semester)
        if major:
            query = query.eq('major', major)

        query.execute()
        _set_delete_request_status({'item_type': 'section_schedule', 'item_id': str(section_name), 'status': 'pending'}, 'approved')
        log_activity('archive', 'schedule', f'All entries for section {section_name}')
        flash('Section schedule moved to archive successfully', 'success')
    except Exception as err:
        if request.method == 'POST':
            return jsonify({'success': False, 'message': 'Something went wrong. Please try again.'}), 500
        return f"Error: {err}"

    sections = session.get('generated_sections', [])
    session['generated_sections'] = [s for s in sections if str(s.get('section')) != str(section_name)]

    if request.method == 'POST':
        return jsonify({'success': True, 'message': 'Section schedule moved to archive successfully.'})
    return redirect(url_for('schedules'))


@app.route('/delete_all_schedules', methods=['POST'])
@roles_required('scheduler')
def delete_all_schedules():
    payload = request.get_json(silent=True) or {}
    password = (payload.get('password') or '').strip()
    program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

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

        # Super Admin can archive all schedules, scoped roles archive their program's schedules
        if user_role not in ('super_admin', 'admin') and program_id:
            supabase.table('schedule').update({'archive': True}).eq('program_id', program_id).execute()
        else:
            supabase.table('schedule').update({'archive': True}).neq('schedule_id', 0).execute()
    except Exception:
        return jsonify({'success': False, 'message': 'Something went wrong. Please try again.'}), 500

    session['generated_sections'] = []
    log_activity('archive', 'schedule', 'Archived all schedules')
    flash('All schedules moved to archive successfully', 'success')
    return jsonify({'success': True, 'message': 'All schedules were moved to archive successfully.'})


@app.route('/edit_schedule/<section_name>', methods=['POST'])
@roles_required('scheduler')
def edit_schedule(section_name):
    payload = request.get_json(silent=True) or {}
    year_level = (payload.get('year_level') or '').strip()
    semester = (payload.get('semester') or '').strip()
    major = (payload.get('major') or '').strip()
    program = session.get('program', '')
    user_program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

    custom_section = (payload.get('section') or '').strip()
    if not year_level and not custom_section:
        return jsonify({'success': False, 'message': 'Please select a year level or enter a section name.'}), 400

    new_section_name = custom_section or _build_section_name_for_year(section_name, year_level)

    try:
        query = supabase.table('schedule').update({
            'section': new_section_name,
            'semester': semester,
            'major': major,
        }).eq('section', section_name).eq('archive', False)
        if user_program_id and user_role not in ('super_admin', 'admin'):
            query = query.eq('program_id', user_program_id)
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
@roles_required('scheduler')
def edit_schedule_entry(schedule_id):
    payload = request.get_json(silent=True) or {}
    course_name = (payload.get('course_name') or '').strip()
    professor_name = (payload.get('professor_name') or '').strip()
    room_name = (payload.get('room_name') or '').strip()
    timeslot = (payload.get('timeslot') or '').strip()
    session_type = (payload.get('session_type') or '').strip()
    professor_load_id = payload.get('professor_load_id')
    day = payload.get('day')
    start = payload.get('start')
    end = payload.get('end')
    room_id = payload.get('room_id')
    program = session.get('program', '')
    user_role = (session.get('role') or 'scheduler').lower()

    if not course_name and not professor_name and not room_name and not timeslot and not session_type and not professor_load_id and not day and not start and not end and not room_id:
        return jsonify({'success': False, 'message': 'Please complete at least one field.'}), 400

    try:
        query = supabase.table('schedule').select(
            'schedule_id, professor_load_id, room_id, day, class_start, class_end, session_type, section, semester, major, program_id, '
            'professor_load(id, prof_id, course_id, course(course_name), professor(first_name, last_name))'
        ).eq('schedule_id', schedule_id).eq('archive', False)
        user_prog_id = _get_user_program_id()
        if user_role not in ('super_admin', 'admin') and user_prog_id:
            query = query.eq('program_id', user_prog_id)

        res = query.execute()
        existing = _first(res.data or [])

        if not existing:
            return jsonify({'success': False, 'message': 'Schedule entry not found.'}), 404

        existing_pc = _rel(existing, 'professor_load') or {}
        existing_course_id = existing_pc.get('course_id')
        existing_prof_id = existing_pc.get('prof_id')

        target_professor_load_id = None

        if professor_load_id:
            try:
                target_professor_load_id = int(professor_load_id)
            except (ValueError, TypeError):
                return jsonify({'success': False, 'message': 'Invalid professor-course selection.'}), 400
        elif course_name or professor_name:
            target_course_id = existing_course_id
            if course_name:
                c_query = supabase.table('course').select('course_id').eq('course_name', course_name)
                eff_pid = _find_program_id_by_name(program) or user_prog_id
                if eff_pid:
                    c_res = c_query.eq('program_id', eff_pid).limit(1).execute()
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
                if professor_name.upper() in ('NONE', 'N/A', 'UNASSIGNED'):
                    target_prof_id = None
                elif professor_name.upper() in ('TBA', 'PROFESSOR A'):
                    target_prof_id = None
                else:
                    eff_pid = _find_program_id_by_name(program) or user_prog_id
                    p_query = supabase.table('professor').select('prof_id, first_name, last_name, program_id')
                    if eff_pid:
                        p_res = p_query.eq('program_id', eff_pid).execute()
                        if not (p_res.data or []):
                            p_res = supabase.table('professor').select('prof_id, first_name, last_name').execute()
                    else:
                        p_res = p_query.execute()
                    for p in (p_res.data or []):
                        full = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip()
                        if full.lower() == professor_name.lower():
                            target_prof_id = p['prof_id']
                            break
                    if not target_prof_id and professor_name.lower() in ('professor a', 'tba'):
                        target_prof_id = None

            if target_course_id and target_prof_id:
                pc_match = supabase.table('professor_load').select('professor_load_id:id').eq('prof_id', target_prof_id).eq('course_id', target_course_id).limit(1).execute()
                pc_row = _first(pc_match.data or [])
                if pc_row:
                    target_professor_load_id = pc_row['professor_load_id']
                else:
                    new_pc = supabase.table('professor_load').insert({'prof_id': target_prof_id, 'course_id': target_course_id}).execute()
                    if new_pc.data:
                        target_professor_load_id = new_pc.data[0]['professor_load_id']
            elif not target_prof_id:
                target_professor_load_id = None
            else:
                target_professor_load_id = existing.get('professor_load_id')
        else:
            target_professor_load_id = existing.get('professor_load_id')

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
                eff_pid = _find_program_id_by_name(program) or user_prog_id
                r_query = supabase.table('room').select('room_id, room_name, room_type, program_id')
                if eff_pid:
                    r_res = r_query.eq('room_name', room_name).eq('program_id', eff_pid).limit(1).execute()
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

        room_conflict = _find_room_schedule_conflict(
            target_room_id,
            target_day,
            target_start,
            target_end,
            exclude_schedule_id=schedule_id,
        )
        if room_conflict:
            conflict_section = room_conflict.get('section') or 'another section'
            conflict_start = _format_time(room_conflict.get('class_start')) or str(room_conflict.get('class_start'))
            conflict_end = _format_time(room_conflict.get('class_end')) or str(room_conflict.get('class_end'))
            return jsonify({
                'success': False,
                'message': (
                    f"Room conflict: The selected room is already booked on {target_day} "
                    f"from {conflict_start} to {conflict_end} for Section {conflict_section}."
                ),
            }), 409

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
            'professor_load_id': target_professor_load_id,
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
@roles_required('scheduler')
def add_timeslot():
    is_ajax = _is_ajax_request()
    data = request.get_json(silent=True) or request.form
    day = (data.get('day') or '').strip()
    start_time = (data.get('start_time') or '').strip()
    end_time = (data.get('end_time') or '').strip()
    lunch_time = (data.get('lunch_time') or '').strip() or None
    semester_id = data.get('semester_id')

    if not day:
        msg = 'Day of the week is required.'
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('timeslot'))

    try:
        existing = supabase.table('timeslot').select('timeslot_id, day').execute().data or []
        for ts in existing:
            if (ts.get('day') or '').strip().lower() == day.lower():
                msg = f'A timeslot for {day} already exists.'
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'danger')
                return redirect(url_for('timeslot'))

        payload = {
            'day': day.capitalize(),
            'start_time': _to_time_string(start_time) if start_time else '08:00:00',
            'end_time': _to_time_string(end_time) if end_time else '20:00:00',
            'lunch_time': _to_time_string(lunch_time) if lunch_time else '12:00:00'
        }
        if semester_id:
            try:
                payload['semester_id'] = int(semester_id)
            except (ValueError, TypeError):
                pass

        supabase.table('timeslot').insert(payload).execute()
        log_activity('add', 'timeslot', f'Timeslot {day}')
        msg = f'Operating day {day} added successfully.'
        if is_ajax:
            return jsonify({'success': True, 'message': msg})
        flash(msg, 'success')
        return redirect(url_for('timeslot'))
    except Exception as err:
        msg = f'Error adding timeslot: {err}'
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 500
        flash(msg, 'danger')
        return redirect(url_for('timeslot'))

#-------------------------------------------------------delete_timeslot----------------------------------------------------------------------------------------------
@app.route('/delete_timeslot/<int:timeslot_id>', methods=['GET', 'POST'])
@roles_required('scheduler')
def delete_timeslot(timeslot_id):
    is_ajax = _is_ajax_request()
    try:
        ts_res = supabase.table('timeslot').select('*').eq('timeslot_id', timeslot_id).execute()
        ts_rows = ts_res.data or []
        if not ts_rows:
            msg = 'Timeslot not found.'
            if is_ajax:
                return jsonify({'success': False, 'message': msg}), 404
            flash(msg, 'danger')
            return redirect(url_for('timeslot'))

        ts_row = ts_rows[0]
        day_name = ts_row.get('day') or ts_row.get('start_day')

        # Keep existing data protections: check if active schedule uses this day
        if day_name:
            in_sched = supabase.table('schedule').select('schedule_id').eq('day', day_name).eq('archive', False).limit(1).execute()
            if in_sched.data:
                msg = f"Cannot delete {day_name} because active classes are scheduled on this day."
                if is_ajax:
                    return jsonify({'success': False, 'message': msg}), 400
                flash(msg, 'danger')
                return redirect(url_for('timeslot'))

        supabase.table('timeslot').delete().eq('timeslot_id', timeslot_id).execute()
        _set_delete_request_status({'item_type': 'timeslot', 'item_id': str(timeslot_id), 'status': 'pending'}, 'approved')
        log_activity('delete', 'timeslot', f'Timeslot ID {timeslot_id} ({day_name})')
        msg = f'Operating day {day_name or timeslot_id} deleted successfully.'
        if is_ajax:
            return jsonify({'success': True, 'message': msg})
        flash(msg, 'success')
        return redirect(url_for('timeslot'))
    except Exception as err:
        msg = f'Error deleting timeslot: {err}'
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 500
        flash(msg, 'danger')
        return redirect(url_for('timeslot'))

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
            pc_ids = [r['professor_load_id'] for r in (supabase.table('professor_load').select('professor_load_id').eq('course_id', item_id).execute().data or [])]
            if pc_ids:
                try:
                    supabase.table('schedule').delete().in_('professor_load_id', pc_ids).execute()
                except Exception:
                    pass
            try:
                supabase.table('schedule').delete().eq('course_id', item_id).execute()
            except Exception:
                pass
            supabase.table('professor_load').delete().eq('course_id', item_id).execute()
            supabase.table('course').delete().eq('course_id', item_id).execute()
        elif item_type == 'professor':
            pc_ids = [r['professor_load_id'] for r in (supabase.table('professor_load').select('professor_load_id').eq('prof_id', item_id).execute().data or [])]
            if pc_ids:
                try:
                    supabase.table('schedule').delete().in_('professor_load_id', pc_ids).execute()
                except Exception:
                    pass
            try:
                supabase.table('schedule').delete().eq('prof_id', item_id).execute()
            except Exception:
                pass
            supabase.table('professor_load').delete().eq('prof_id', item_id).execute()
            supabase.table('professor').delete().eq('prof_id', item_id).execute()
        elif item_type == 'room':
            supabase.table('schedule').delete().eq('room_id', item_id).execute()
            supabase.table('room').delete().eq('room_id', item_id).execute()
        elif item_type == 'timeslot':
            supabase.table('timeslot').delete().eq('timeslot_id', item_id).execute()
        elif item_type == 'professor_load':
            supabase.table('professor_load').delete().eq('id', item_id).execute()
        elif item_type == 'professor_load_all':
            supabase.table('professor_load').delete().eq('prof_id', item_id).execute()
        elif item_type == 'schedule':
            supabase.table('schedule').update({'archive': True}).eq('schedule_id', item_id).execute()
        elif item_type == 'section_schedule':
            supabase.table('schedule').update({'archive': True}).eq('section', item_id).execute()
        elif item_type == 'all_schedules':
            program_id = _get_user_program_id()
            user_role = (session.get('role', '') or '').lower()
            if user_role not in ('super_admin', 'admin') and program_id:
                supabase.table('schedule').update({'archive': True}).eq('program_id', program_id).execute()
            else:
                supabase.table('schedule').update({'archive': True}).neq('schedule_id', 0).execute()

        _set_delete_request_status({'id': req_id}, 'approved')

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

        _set_delete_request_status({'id': req_id}, 'rejected')

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
@roles_required('admin', 'scheduler', 'viewer')
def mark_notifications_read():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User not logged in.'}), 401
    try:
        supabase.table('delete_requests').update({'is_read': True}).eq('user_id', str(user_id)).in_('status', ['approved', 'rejected']).eq('is_read', False).execute()
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
        _set_delete_request_status({'item_type': 'irregular_student', 'item_id': str(student_id), 'status': 'pending'}, 'approved')

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
    stud_prog_id = _find_program_id_by_name(student.get('program'))
    if stud_prog_id:
        program_courses = (supabase.table('course').select('*, program:program_id(id, program_name)').eq('program_id', stud_prog_id).execute().data) or []
    else:
        program_courses = (supabase.table('course').select('*, program:program_id(id, program_name)').execute().data) or []
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
        'schedule_id, section, day, class_start, class_end, room_id, professor_load_id, session_type, '
        'professor_load(professor_load_id:id, prof_id, course_id, course(course_id, course_name), professor(prof_id, first_name, last_name)), '
        'room(room_name)'
    ).eq('program_id', stud_prog_id or -1).eq('archive', False)
    if semester_filter:
        sched_query = sched_query.eq('semester', semester_filter)
    sched_rows = sched_query.execute().data or []

    all_schedule_entries = []
    for row in sched_rows:
        pc = _rel(row, 'professor_load') or {}
        c = _rel(pc, 'course') or _rel(row, 'course') or {}
        r = _rel(row, 'room') or {}
        p = _rel(pc, 'professor') or _rel(row, 'professor') or {}
        pf = p.get('first_name') or ''
        pl = p.get('last_name') or ''
        all_schedule_entries.append({
            'schedule_id': row.get('schedule_id'),
            'professor_load_id': row.get('professor_load_id'),
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
        'schedule(day, class_start, class_end, session_type, professor_load_id, professor_load(course_id, prof_id, course(course_name), professor(first_name, last_name)), course(course_name), room(room_name), professor(first_name, last_name))'
    ).eq('student_id', student_id).execute().data) or []

    assigned_by_course = {}
    total_assigned_units = 0
    assigned_course_ids = set()

    for row in assigned_rows:
        cid = row.get('course_id')
        assigned_course_ids.add(cid)
        sch = _rel(row, 'schedule') or {}
        pc = _rel(sch, 'professor_load') or {}
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
        'schedule(day, class_start, class_end, session_type, professor_load_id, professor_load(course_id, prof_id, course(course_name), professor(first_name, last_name)), course(course_name), room(room_name), professor(first_name, last_name))'
    ).eq('student_id', student_id).execute().data) or []

    entries = []
    for row in assigned_rows:
        sch = _rel(row, 'schedule') or {}
        pc = _rel(sch, 'professor_load') or {}
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
            'professor': f"{pf} {pl}".strip() or '',
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

    pc_res = supabase.table('professor_load').select('professor_load_id:id').eq('course_id', course_id).execute()
    c_pc_ids = [item['professor_load_id'] for item in (pc_res.data or []) if item.get('professor_load_id')]
    if c_pc_ids:
        res = supabase.table('schedule').select('*, professor_load(course_id, course(course_name))').in_('professor_load_id', c_pc_ids).eq('section', section).eq('archive', False).execute()
    else:
        res = None
    new_entries = []
    for row in (res.data or []):
        pc = _rel(row, 'professor_load') or {}
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
#-------------------------------------------------------SEMESTER_AND_SECTION_CONFIG_REMOVED----------------------------------------------------------------------------------------------
# Deprecated semester and section_config management removed (Change 1).
# Semesters are now standardized text attributes ("1st Semester", "2nd Semester") on courses and schedules.
# Section counts are computed directly from professor_load.

#-------------------------------------------------------TIMESLOT_MODULE----------------------------------------------------------------------------------------------
@app.route('/timeslot')
@roles_required('scheduler')
def timeslot():
    user_prog_id = _get_user_program_id()
    user_role = (session.get('role') or '').lower()
    can_edit_timeslot = user_role in ('scheduler', 'super_admin', 'admin')
    is_dean_or_admin = can_edit_timeslot

    semesters_list = _get_semesters(user_prog_id)
    req_sem_id = request.args.get('semester_id')
    selected_sem = None
    if req_sem_id:
        try:
            req_sem_id = int(req_sem_id)
            selected_sem = next((s for s in semesters_list if s.get('id') == req_sem_id), None)
        except (ValueError, TypeError):
            selected_sem = None

    if not selected_sem:
        selected_sem = _get_active_semester(user_prog_id)

    day_order = {'Monday': 1, 'Tuesday': 2, 'Wednesday': 3, 'Thursday': 4, 'Friday': 5, 'Saturday': 6, 'Sunday': 7}
    all_timeslots = []
    if selected_sem:
        sem_id = selected_sem.get('id')
        try:
            q = supabase.table('timeslot').select('*').eq('semester_id', sem_id)
            all_timeslots = q.execute().data or []
        except Exception:
            all_timeslots = []

    if not all_timeslots:
        try:
            all_timeslots = (supabase.table('timeslot').select('*').execute().data) or []
        except Exception:
            all_timeslots = []

    all_timeslots.sort(key=lambda t: day_order.get(t.get('day') or t.get('start_day') or '', 99))

    return render_template(
        'timeslot.html',
        active_page='timeslot',
        semester=selected_sem,
        semesters=semesters_list,
        timeslots=all_timeslots,
        is_dean_or_admin=is_dean_or_admin,
        can_edit_timeslot=can_edit_timeslot
    )

@app.route('/edit_timeslot/<int:timeslot_id>', methods=['POST'])
@roles_required('scheduler')
def edit_timeslot(timeslot_id):
    is_ajax = _is_ajax_request()
    data = request.get_json(silent=True) or request.form
    start_time = (data.get('start_time') or '').strip()
    end_time = (data.get('end_time') or '').strip()
    lunch_time = (data.get('lunch_time') or '').strip() or None

    try:
        payload = {}
        if start_time:
            payload['start_time'] = _to_time_string(start_time)
        if end_time:
            payload['end_time'] = _to_time_string(end_time)
        payload['lunch_time'] = _to_time_string(lunch_time) if lunch_time else None

        supabase.table('timeslot').update(payload).eq('timeslot_id', timeslot_id).execute()
        log_activity('edit', 'timeslot', f'Timeslot ID {timeslot_id}')
        msg = 'Timeslot operating hours updated successfully.'
        if is_ajax:
            return jsonify({'success': True, 'message': msg})
        flash(msg, 'success')
    except Exception as err:
        msg = f'Error updating timeslot: {err}'
        if is_ajax:
            return jsonify({'success': False, 'message': msg}), 500
        flash(msg, 'danger')

    return redirect(url_for('timeslot'))

@app.route('/initialize_timeslots', methods=['POST'])
@roles_required('scheduler')
def initialize_timeslots():
    sem_id = request.form.get('semester_id')
    if not sem_id:
        flash('Semester ID is required.', 'danger')
        return redirect(url_for('timeslot'))

    try:
        sem_id = int(sem_id)
        standard_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        for d in standard_days:
            try:
                existing = supabase.table('timeslot').select('timeslot_id').eq('semester_id', sem_id).eq('day', d).execute().data or []
                if not existing:
                    supabase.table('timeslot').insert({
                        'semester_id': sem_id,
                        'day': d,
                        'start_time': '07:00:00',
                        'end_time': '20:00:00',
                        'lunch_time': '12:00:00'
                    }).execute()
            except Exception as ins_err:
                logging.warning(f"Error inserting timeslot for {d}: {ins_err}")

        log_activity('create', 'timeslot', f'Initialized week for semester {sem_id}')
        flash('Standard Monday–Friday schedule initialized successfully.', 'success')
    except Exception as err:
        flash(f'Error initializing timeslots: {err}', 'danger')

    return redirect(url_for('timeslot', semester_id=sem_id))

@app.route('/check_schedule_exists')
@roles_required('scheduler')
def check_schedule_exists():
    year_level = (request.args.get('year_level') or '').strip()
    semester = (request.args.get('semester') or '').strip()
    major = (request.args.get('major') or '').strip()
    program_id = _get_user_program_id()
    user_role = (session.get('role') or 'scheduler').lower()

    if not semester:
        return jsonify({'exists': False})

    try:
        query = supabase.table('schedule').select('section, program_id, major').eq('semester', semester).eq('archive', False)

        if program_id:
            query = query.eq('program_id', program_id)

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
            'program': session.get('program', ''),
            'major': major,
            'total_entries': total_entries,
            'total_sections': total_sections,
            'view_url': view_url
        })
    except Exception as err:
        return jsonify({'exists': False, 'error': str(err)})

#------------------------------------------------------------Generate Schedule------------------------------------------------------------------------------------------
@app.route('/generate_schedule', methods=['GET', 'POST'])
@roles_required('scheduler')
def generate_schedule():
    generation_warnings = []
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
    user_role = (session.get('role') or 'scheduler').lower()
    is_admin = user_role in ('super_admin', 'admin')
    is_viewer = False
    department = _get_department()

    try:
        semester = (request.form.get('semester') or '').strip()
        if not semester:
            return "Error: Semester is required to generate a schedule."

        # Normalize semester aliases
        if semester in ('1st Semester', '1st', '1'):
            standard_semester = '1st Semester'
        elif semester in ('2nd Semester', '2nd', '2'):
            standard_semester = '2nd Semester'
        else:
            standard_semester = semester

        if standard_semester not in SEMESTER_CHOICES:
            flash(f"Invalid semester '{semester}'. Must be '1st Semester' or '2nd Semester'.", 'danger')
            return redirect(url_for('generate_schedule'))

        user_prog_id = _get_user_program_id()
        _ensure_course_semester_column()

        # Compute counts and validate strictly from professor_load
        calc_result = calculate_semester_section_counts(program_id=user_prog_id, semester=standard_semester, department=department)

        # Archive rule check: If active schedule has the SAME semester, block generation
        active_sched = calc_result.get('active_schedule', {})
        if active_sched.get('exists') and active_sched.get('same_semester'):
            flash(f"An active schedule for {standard_semester} already exists. Archive it first to generate a new one.", 'danger')
            return redirect(url_for('generate_schedule'))

        # If active schedule exists for DIFFERENT semester, soft-archive it
        if active_sched.get('exists') and not active_sched.get('same_semester'):
            try:
                arch_q = supabase.table('schedule').update({'archive': True}).eq('archive', False)
                if user_prog_id:
                    arch_q = arch_q.eq('program_id', user_prog_id)
                arch_q.execute()
            except Exception as e:
                logging.warning(f"Failed to soft-archive different semester active schedule: {e}")

        if not calc_result.get('valid'):
            for err in calc_result.get('errors', []):
                flash(f"Cannot generate schedule: {err}", 'danger')
            return redirect(url_for('generate_schedule'))

        # Fetch curriculum for all four year levels for this semester
        query = supabase.table('course').select('*, program:program_id(id, program_name)').eq('semester', standard_semester)
        if user_prog_id:
            query = query.eq('program_id', user_prog_id)
        elif program:
            resolved_pid = _find_program_id_by_name(program)
            if resolved_pid:
                query = query.eq('program_id', resolved_pid)

        all_courses = query.order('year_level').order('course_name').execute().data or []
        if not all_courses:
            all_c = (supabase.table('course').select('*, program:program_id(id, program_name)').execute().data) or []
            if user_prog_id:
                all_courses = [c for c in all_c if str(c.get('semester') or '').strip().lower() == standard_semester.lower() and c.get('program_id') == user_prog_id]
            else:
                all_courses = [c for c in all_c if str(c.get('semester') or '').strip().lower() == standard_semester.lower()]

        for c in all_courses:
            p_rel = _rel(c, 'program') or {}
            c['program_name'] = p_rel.get('program_name') or program or ''
            c['program'] = c['program_name']

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
            seen_cids = set()
            deduped = []
            for c in courses_by_year[yl]:
                cid = c.get('course_id') or c.get('course_name')
                if cid not in seen_cids:
                    seen_cids.add(cid)
                    deduped.append(c)
            courses_by_year[yl] = deduped

        # Build sections derived from professor_load
        all_sections = []
        for yl_info in calc_result.get('breakdown', []):
            yl = yl_info['year_level']
            is_spec = yl_info.get('is_specialized', False)

            if not is_spec:
                sec_names = yl_info.get('section_names', [])
                for s_name in sec_names:
                    all_sections.append({
                        'section': s_name,
                        'section_name': s_name,
                        'year_level': str(yl),
                        'student_count': 40,
                        'semester': standard_semester,
                        'major': None
                    })
            else:
                for grp in yl_info.get('specialization_groups', []):
                    spec_name = grp['specialization']
                    grp_sec_names = grp.get('section_names', [])
                    for s_name in grp_sec_names:
                        all_sections.append({
                            'section': s_name,
                            'section_name': s_name,
                            'year_level': str(yl),
                            'student_count': 40,
                            'semester': standard_semester,
                            'major': spec_name
                        })

        if not all_sections:
            all_sections = [{'section': '1A', 'section_name': '1A', 'year_level': '1', 'student_count': 40, 'semester': standard_semester, 'major': None}]

        session['generated_sections'] = all_sections

        # Fetch professor_load mappings
        try:
            pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, prof_id, sections, professor(first_name, last_name, program_id, program:program_id(id, program_name), time_designation, academic_ranking_id, academic_ranking(max_hours, has_cutoff))').execute()
            all_profs_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, program:program_id(id, program_name), time_designation, academic_ranking_id, academic_ranking(max_hours, has_cutoff)').execute()
        except Exception:
            try:
                pc_res = supabase.table('professor_load').select('professor_load_id:id, course_id, prof_id, sections, professor(first_name, last_name, program_id, academic_ranking_id, academic_ranking(max_hours, has_cutoff))').execute()
                all_profs_res = supabase.table('professor').select('prof_id, first_name, last_name, program_id, academic_ranking_id, academic_ranking(max_hours, has_cutoff)').execute()
            except Exception:
                pc_res = supabase.table('professor_load').select('*').execute()
                all_profs_res = supabase.table('professor').select('*').execute()

        pc_data = pc_res.data or []
        professor_load_map = {}
        professors_by_course = {}
        # Pre-build a prof_id → professor dict so the fallback join lookup is O(1)
        _all_profs_by_id = {ap.get('prof_id'): ap for ap in (all_profs_res.data or []) if ap.get('prof_id')}
        for row in pc_data:
            pcid = row.get('professor_load_id') or row.get('id') or 1
            cid = row.get('course_id')
            pid = row.get('prof_id')
            if pid and cid:
                professor_load_map[(pid, cid)] = pcid
            p = _rel(row, 'professor')
            if not p and pid:
                # O(1) lookup using pre-built dict instead of O(N) linear scan
                p = _all_profs_by_id.get(pid)
            if p:
                sec_val = int(row.get('sections') or 0)
                if sec_val == 0:
                    sec_val = 999
                professors_by_course.setdefault(cid, []).append({
                    'professor_load_id': pcid,
                    'course_id': cid,
                    'prof_id': pid,
                    'first_name': p.get('first_name'),
                    'last_name': p.get('last_name'),
                    'time_designation': int(p.get('time_designation') or 5),
                    'max_hours': int(_ranking_constraints(p)['max_hours']),
                    'sections': sec_val,
                    'quota': sec_val,
                })

        all_prof_data = all_profs_res.data or []
        all_professors_pool = []
        for p in all_prof_data:
            all_professors_pool.append({
                'prof_id': p.get('prof_id'),
                'first_name': p.get('first_name'),
                'last_name': p.get('last_name'),
                'time_designation': int(p.get('time_designation') or 5),
                'max_hours': int(_ranking_constraints(p)['max_hours']),
                'program_id': p.get('program_id'),
                'sections': 0,
                'quota': 999,
            })

        # Fetch rooms
        if user_prog_id:
            try:
                rooms_res = supabase.table('room').select('*').eq('program_id', user_prog_id).execute()
                all_rooms = rooms_res.data or []
                if not all_rooms:
                    rooms_res = supabase.table('room').select('*').execute()
                    all_rooms = rooms_res.data or []
            except Exception:
                rooms_res = supabase.table('room').select('*').execute()
                all_rooms = rooms_res.data or []
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

        # --- Pre-fetch professor cutoff data (avoids per-slot DB queries in _check_professor_cutoff_conflict) ---
        # Build day_cutoff_map: {day_name: cutoff_timedelta_or_str} from already-fetched timeslots
        _day_cutoff_map = {}
        for _ts in timeslots:
            _d = (_ts.get('day') or '').strip()
            _cutoff = _ts.get('professor_cutoff')
            if _d and _cutoff and _d not in _day_cutoff_map:
                _day_cutoff_map[_d] = _cutoff

        # Build prof_cutoff_map: {prof_id: has_cutoff (bool)}
        # We already fetched professor data in all_profs_res; the academic_ranking.has_cutoff is embedded
        _prof_cutoff_map = {}
        for _pr in (all_profs_res.data or []):
            _pid = _pr.get('prof_id')
            if _pid is None:
                continue
            _rank = _pr.get('academic_ranking') or {}
            if isinstance(_rank, list):
                _rank = _rank[0] if _rank else {}
            _prof_cutoff_map[_pid] = _rank.get('has_cutoff', True)
        # Fallback: fetch has_cutoff for any prof_id NOT yet in the map via a single batch query
        _unmapped_prof_ids = [
            p.get('prof_id') for p in all_professors_pool
            if p.get('prof_id') not in _prof_cutoff_map
        ]
        if _unmapped_prof_ids:
            try:
                _extra_res = supabase.table('professor').select('prof_id, academic_ranking(has_cutoff)').in_('prof_id', _unmapped_prof_ids).execute()
                for _pr in (_extra_res.data or []):
                    _pid = _pr.get('prof_id')
                    _rank = _pr.get('academic_ranking') or {}
                    if isinstance(_rank, list):
                        _rank = _rank[0] if _rank else {}
                    if _pid is not None:
                        _prof_cutoff_map[_pid] = _rank.get('has_cutoff', True)
            except Exception:
                pass

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
        prof_scheduled_days = {}
        professor_load_count = {}
        prof_section_count = {}
        preview_entries = []

        # Load existing bookings from DB to avoid collision across different programs in the SAME semester
        current_program_id = _get_user_program_id()
        existing_rows = (supabase.table('schedule').select('section, room_id, day, class_start, class_end, professor_load_id, professor_load(prof_id), semester, program_id, major').eq('archive', False).execute().data) or []
        for existing in existing_rows:
            # ONLY consider rows for the EXACT same semester that belong to a DIFFERENT program
            if existing.get('semester') != standard_semester:
                continue
            is_same = False
            ex_pid = existing.get('program_id')
            ex_prog = existing.get('program') or existing.get('program_name')
            if current_program_id and ex_pid:
                is_same = (ex_pid == current_program_id)
            elif program and ex_prog:
                is_same = (ex_prog.strip().lower() == program.strip().lower())
            elif current_program_id and ex_prog and program:
                is_same = (ex_prog.strip().lower() == program.strip().lower())

            if is_same:
                continue

            sec_n = existing.get('section')
            sec_m = existing.get('major')
            rid = existing.get('room_id')
            p_pc = _rel(existing, 'professor_load') or {}
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
                    prof_scheduled_days.setdefault(p_id, set()).add(d)

        total_sessions_required = 0
        total_sessions_scheduled = 0
        prof_tba_count = 0
        room_tba_count = 0

        def _can_prof_teach_on_day(prof_dict, check_day):
            pid = prof_dict.get('prof_id')
            if not pid:
                return True
            days_set = prof_scheduled_days.get(pid, set())
            if check_day in days_set:
                return True
            max_days = int(prof_dict.get('time_designation') or 5)
            return len(days_set) < max_days

        def _resolve_load_id(prof, c_id):
            if not prof:
                return None
            lid = prof.get('professor_load_id')
            if lid:
                return lid
            p_id = prof.get('prof_id')
            if not p_id:
                return None
            lid = professor_load_map.get((p_id, c_id))
            if not lid:
                try:
                    pc_chk = supabase.table('professor_load').select('id').eq('prof_id', int(p_id)).eq('course_id', int(c_id)).execute()
                    pc_chk_row = _first(pc_chk.data or [])
                    if pc_chk_row:
                        lid = pc_chk_row.get('id')
                except Exception:
                    pass
            if lid:
                professor_load_map[(p_id, c_id)] = lid
            return lid

        # Helper to schedule a single unpaired session (or half of a split paired session)
        def _schedule_single_session(session_type, duration, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
            nonlocal total_sessions_scheduled, assignment_step, generation_warnings
            if duration <= 0:
                return True

            course_id = course['course_id']
            if not assigned_prof:
                generation_warnings.append(f"No professor load for Section {section_name} - {course.get('course_name')}: session skipped")
                return False

            pk = assigned_prof.get('prof_id')
            assigned_professor_load_id = assigned_prof.get('professor_load_id')
            if not assigned_professor_load_id:
                assigned_professor_load_id = _resolve_load_id(assigned_prof, course_id)
            if not assigned_professor_load_id:
                generation_warnings.append(f"No valid professor_load row for {section_name} - {course.get('course_name')}: session skipped")
                return False

            # STRICT room filtering: ILP and Lecture MUST strictly use lecture rooms only!
            cand_rooms = lecture_rooms if session_type in ('Lecture', 'ILP') else lab_rooms

            passes = [
                {'strict_rules': True},
                {'strict_rules': False},
            ]

            for p_config in passes:
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
                        min_prof_day_h = prof_day_hours.get((pk, day), 0.0)
                        s -= int(min_prof_day_h * 15)
                        scored_days.append((s, day))
                scored_days.sort(key=lambda x: x[0], reverse=True)

                late_threshold = timedelta(hours=17)
                for _, day in scored_days:
                    day_slots = slot_groups[day]
                    if len(day_slots) < duration:
                        continue

                    start_indices = list(range(0, len(day_slots) - duration + 1))
                    if session_type == 'ILP':
                        start_indices.sort(key=lambda idx: -100 if day == 'Monday' and idx == 0 else -idx)

                    for start_index in start_indices:
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
                        if block_start < timedelta(hours=8) and session_type != 'ILP':
                            continue

                        if professor_hours.get(pk, 0.0) + duration > (assigned_prof.get('max_hours') or 40):
                            continue
                        if _has_conflict(day, block_start, block_end, professor_bookings.get(pk, [])):
                            continue
                        if p_config['strict_rules'] and not _can_prof_teach_on_day(assigned_prof, day):
                            continue
                        if _check_professor_cutoff_conflict(pk, day, block_start, block_end, session_type,
                                                             prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None:
                            continue

                        assigned_room = _select_least_used_room(
                            cand_rooms, day, block_start, block_end,
                            room_bookings, room_usage, room_last_used, room_order
                        )
                        if not assigned_room:
                            continue

                        # ASSIGNMENT SUCCESS
                        rk = assigned_room['room_id']
                        room_name = assigned_room.get('room_name') or ''
                        prof_full_name = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip()

                        preview_entries.append({
                            'professor_load_id': assigned_professor_load_id,
                            'course_id': course_id,
                            'course_name': course.get('course_name'),
                            'prof_id': pk,
                            'professor_name': prof_full_name,
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
                        room_bookings.setdefault(rk, []).append((day, block_start, block_end))
                        assignment_step += 1
                        room_usage[rk] = room_usage.get(rk, 0) + 1
                        room_last_used[rk] = assignment_step
                        professor_bookings.setdefault(pk, []).append((day, block_start, block_end))
                        professor_hours[pk] = professor_hours.get(pk, 0.0) + duration
                        prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + duration
                        prof_scheduled_days.setdefault(pk, set()).add(day)

                        courses_per_day[day] = courses_per_day.get(day, 0) + 1
                        days_tried[day] = days_tried.get(day, 0) + 1
                        if slot_is_late:
                            late_days.add(day)

                        total_sessions_scheduled += 1
                        return True

            generation_warnings.append(
                f"{session_type} for {course.get('course_name')} (Section {section_name}) could not be scheduled in a matching room without conflict and remains unscheduled"
            )
            return False

        # Helper to schedule a paired block (Lecture + Lab)
        def _schedule_paired_block(lec_dur, lab_dur, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used):
            nonlocal total_sessions_scheduled, assignment_step, generation_warnings
            total_dur = lec_dur + lab_dur
            course_id = course['course_id']
            if not assigned_prof:
                generation_warnings.append(f"No professor load for Section {section_name} - {course.get('course_name')}: paired block skipped")
                return False

            pk = assigned_prof.get('prof_id')
            assigned_professor_load_id = assigned_prof.get('professor_load_id')
            if not assigned_professor_load_id:
                assigned_professor_load_id = _resolve_load_id(assigned_prof, course_id)
            if not assigned_professor_load_id:
                generation_warnings.append(f"No valid professor_load row for {section_name} - {course.get('course_name')}: paired block skipped")
                return False

            passes = [
                {'strict_rules': True},
                {'strict_rules': False},
            ]

            for p_config in passes:
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
                        min_prof_day_h = prof_day_hours.get((pk, day), 0.0)
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
                        if lec_start < timedelta(hours=8):
                            continue

                        if professor_hours.get(pk, 0.0) + total_dur > (assigned_prof.get('max_hours') or 40):
                            continue
                        if _has_conflict(day, lec_start, lab_end, professor_bookings.get(pk, [])):
                            continue
                        if p_config['strict_rules'] and not _can_prof_teach_on_day(assigned_prof, day):
                            continue
                        if _check_professor_cutoff_conflict(pk, day, lec_start, lab_end, 'Lecture',
                                                             prof_cutoff_map=_prof_cutoff_map, day_cutoff_map=_day_cutoff_map) is not None:
                            continue

                        assigned_lec_room = _select_least_used_room(
                            lecture_rooms, day, lec_start, lec_end,
                            room_bookings, room_usage, room_last_used, room_order
                        )
                        if not assigned_lec_room:
                            continue
                        assigned_lab_room = _select_least_used_room(
                            lab_rooms, day, lab_start, lab_end,
                            room_bookings, room_usage, room_last_used, room_order
                        )
                        if not assigned_lab_room:
                            continue

                        # SUCCESSFUL PAIRED ASSIGNMENT
                        prof_name = f"{assigned_prof.get('first_name', '')} {assigned_prof.get('last_name', '')}".strip()
                        lec_rk = assigned_lec_room['room_id']
                        lab_rk = assigned_lab_room['room_id']

                        preview_entries.append({
                            'professor_load_id': assigned_professor_load_id,
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
                            'professor_load_id': assigned_professor_load_id,
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
                        professor_bookings.setdefault(pk, []).append((day, lab_start, lab_end))

                        professor_hours[pk] = professor_hours.get(pk, 0.0) + total_dur
                        prof_day_hours[(pk, day)] = prof_day_hours.get((pk, day), 0.0) + total_dur
                        prof_scheduled_days.setdefault(pk, set()).add(day)

                        courses_per_day[day] = courses_per_day.get(day, 0) + 1
                        days_tried[day] = days_tried.get(day, 0) + 1
                        if slot_is_late:
                            late_days.add(day)

                        total_sessions_scheduled += 2
                        return True

            # If contiguous 4-hour paired block could not be placed, split into separate Laboratory and Lecture sessions
            # Laboratory is scheduled first because lab rooms (7) are significantly scarcer than lecture rooms (12)
            logging.info(f"[SCHEDULER] Splitting paired session for {section_name} - {course.get('course_name')} ({lec_dur}h Lecture, {lab_dur}h Lab) into independent slots.")
            ok_lab = _schedule_single_session('Laboratory', lab_dur, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
            ok_lec = _schedule_single_session('Lecture', lec_dur, course, assigned_prof, section_name, yr, sec_major, sec_key, courses_per_day, late_days, days_tried, two_course_day_used)
            return ok_lec and ok_lab

        # Pre-assign (section, course) -> professor_load row strictly based on professor_load
        section_course_assignment = {}

        # Map each professor to any specialized track they teach in this year level
        prof_track_affinity = {}
        for c in all_courses:
            c_yl = int(c.get('year_level') or 1)
            c_spec = c.get('specialization') or c.get('major')
            if c_spec and str(c_spec).strip().lower() not in ('general', 'none', ''):
                for l in professors_by_course.get(c['course_id'], []):
                    prof_track_affinity[(l['prof_id'], c_yl)] = str(c_spec).strip()

        for course in all_courses:
            cid = course['course_id']
            yl = int(course.get('year_level') or 1)
            cmajor = course.get('specialization') or course.get('major')

            matching_secs = [
                s for s in all_sections
                if int(s.get('year_level') or 1) == yl and _major_matches(cmajor, s.get('major'))
            ]

            loads = professors_by_course.get(cid, [])
            if not loads:
                generation_warnings.append(f"No professor load for {course.get('course_name')}: no classes generated")
                continue

            is_general_course = (not cmajor or str(cmajor).strip().lower() in ('general', 'none', ''))

            if is_general_course and any(s.get('major') for s in matching_secs):
                # General course in specialized term: match professors to sections with their track affinity first
                assigned_sec_names = set()
                unassigned_loads = []

                for l in loads:
                    aff = prof_track_affinity.get((l['prof_id'], yl))
                    cnt = l.get('sections') or 1
                    placed_cnt = 0
                    if aff:
                        for s in matching_secs:
                            if placed_cnt >= cnt:
                                break
                            if s['section'] not in assigned_sec_names and _major_matches(aff, s.get('major')):
                                section_course_assignment[(s['section'], cid)] = l
                                assigned_sec_names.add(s['section'])
                                placed_cnt += 1
                    rem = cnt - placed_cnt
                    if rem > 0:
                        unassigned_loads.append((l, rem))

                rem_secs = [s for s in matching_secs if s['section'] not in assigned_sec_names]
                unassigned_loads.sort(key=lambda item: item[0].get('sections', 1))
                for s in reversed(rem_secs):
                    if unassigned_loads:
                        l, rem = unassigned_loads[0]
                        section_course_assignment[(s['section'], cid)] = l
                        assigned_sec_names.add(s['section'])
                        if rem == 1:
                            unassigned_loads.pop(0)
                        else:
                            unassigned_loads[0] = (l, rem - 1)

                if len(assigned_sec_names) < len(matching_secs):
                    for s in matching_secs:
                        if s['section'] not in assigned_sec_names:
                            generation_warnings.append(f"No professor load for Section {s['section']} - {course.get('course_name')}: no classes generated")
            else:
                sec_idx = 0
                for l in loads:
                    cnt = l.get('sections') or 1
                    for _ in range(cnt):
                        if sec_idx < len(matching_secs):
                            sec = matching_secs[sec_idx]
                            section_course_assignment[(sec['section'], cid)] = l
                            sec_idx += 1
                        else:
                            break

                if sec_idx < len(matching_secs):
                    for s in matching_secs[sec_idx:]:
                        generation_warnings.append(f"No professor load for Section {s['section']} - {course.get('course_name')}: no classes generated")

        # Main assignment loop across all sections in the batch
        for section in all_sections:
            section_name = section['section']
            yr = int(section.get('year_level') or 1)
            sec_major = section.get('major')
            sec_key = (section_name, sec_major)
            # Derive section courses directly from section_course_assignment so no pre-assigned course is dropped
            section_courses = [
                c for c in courses_by_year.get(yr, [])
                if (section_name, c.get('course_id')) in section_course_assignment
            ]
            # Prioritize 3-hour single lecture blocks, then by faculty constraint tightness
            section_courses.sort(
                key=lambda c: (
                    -int(c.get('lecture_hours') or 0) if int(c.get('lab_hours') or 0) == 0 else 0,
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
                assigned_prof = section_course_assignment.get((section_name, course['course_id']))
                subject_session_queue = _build_subject_session_queue(course)

                for session_item in subject_session_queue:
                    total_sessions_required += 1
                    if session_item.get('paired'):
                        _schedule_paired_block(
                            session_item['lec_duration'], session_item['lab_duration'],
                            course, assigned_prof, section_name, yr, sec_major, sec_key,
                            courses_per_day, late_days, days_tried, two_course_day_used
                        )
                    else:
                        _schedule_single_session(
                            session_item['session_type'], session_item['duration'],
                            course, assigned_prof, section_name, yr, sec_major, sec_key,
                            courses_per_day, late_days, days_tried, two_course_day_used
                        )

            logging.info(
                f"[SECTION FILL SUMMARY] Section {section_name}: "
                f"{total_sessions_scheduled} session(s) scheduled so far"
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

        # Comprehensive professor_load audit: compare expected vs placed sessions for every professor_load row
        courses_by_id = {c['course_id']: c for c in all_courses}
        sched_by_lid = Counter(e.get('professor_load_id') for e in preview_entries if e.get('professor_load_id'))

        unscheduled_loads = []
        for pl_row in pc_data:
            pcid = pl_row.get('professor_load_id') or pl_row.get('id')
            cid = pl_row.get('course_id')
            pid = pl_row.get('prof_id')
            c_info = courses_by_id.get(cid)
            if not c_info:
                continue
            if str(c_info.get('semester') or '').strip().lower() != standard_semester.lower():
                continue

            p_info = _rel(pl_row, 'professor') or _all_profs_by_id.get(pid) or {}
            prof_name = f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip() or f"Prof #{pid}"
            c_name = c_info.get('course_name') or f"Course #{cid}"

            sec_count = int(pl_row.get('sections') or 0)
            lec_h = int(c_info.get('lecture_hours') or 0)
            lab_h = int(c_info.get('lab_hours') or 0)
            ilp_h = int(float(c_info.get('ilp_hours') or 0))

            expected_per_sec = (1 if lec_h > 0 else 0) + (1 if lab_h > 0 else 0) + (1 if ilp_h > 0 else 0)
            total_expected_sessions = expected_per_sec * sec_count
            actual_sessions = sched_by_lid.get(pcid, 0)

            if actual_sessions < total_expected_sessions or (total_expected_sessions == 0 and sec_count > 0):
                assigned_secs = [
                    sec_name for (sec_name, sec_cid), l_item in section_course_assignment.items()
                    if sec_cid == cid and (l_item.get('professor_load_id') == pcid or (l_item.get('prof_id') == pid and l_item.get('course_id') == cid))
                ]
                sec_disp = ", ".join(sorted(set(assigned_secs))) if assigned_secs else "Unassigned (No matching section)"

                if total_expected_sessions == 0:
                    reason_desc = "Course has 0 hours defined in curriculum"
                elif not assigned_secs:
                    reason_desc = "No matching section found for curriculum year level or specialization track"
                elif actual_sessions == 0:
                    reason_desc = "No free room, professor timeslot conflict, or daily cutoff prevented placement"
                else:
                    reason_desc = f"Partial placement: {actual_sessions} placed, {total_expected_sessions - actual_sessions} session(s) blocked by room or faculty conflicts"

                unscheduled_loads.append({
                    'professor_load_id': pcid,
                    'prof_id': pid,
                    'professor': prof_name,
                    'course_id': cid,
                    'course': c_name,
                    'section': sec_disp,
                    'placed': actual_sessions,
                    'required': total_expected_sessions,
                    'reason': reason_desc
                })

        session['unscheduled_loads'] = unscheduled_loads
        if unscheduled_loads:
            for item in unscheduled_loads:
                generation_warnings.append(
                    f"Unscheduled Load: {item['professor']} - {item['course']} ({item['section']}): {item['placed']}/{item['required']} sessions placed. Reason: {item['reason']}"
                )

        for idx, entry in enumerate(preview_entries, start=1):
            entry['id'] = idx
            if not isinstance(entry.get('start'), str):
                entry['start'] = _format_time(entry['start'])
            if not isinstance(entry.get('end'), str):
                entry['end'] = _format_time(entry['end'])
            entry['time_range'] = f"{entry['day']} | {entry['start']} - {entry['end']}"

        _set_preview_for_user(preview_entries)
        flash('Schedule generated successfully across all 4 year levels.', 'success')

        if generation_warnings:
            for w in generation_warnings:
                flash(w, 'warning')

        preview_context = _build_preview_context(preview_entries)
        return render_template(
            'index.html',
            active_page='home',
            semesters=semesters,
            show_preview=bool(preview_entries),
            **preview_context
        )
    except Exception as err:
        logging.exception(f"Exception in generate_schedule: {err}")
        _clear_preview_generation_state()
        return f"Error: {err}", 500


@app.route('/preview_schedule')
@roles_required('scheduler')
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
@roles_required('scheduler')
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
        professor_load_id = data.get('professor_load_id')
        if not professor_load_id:
            return jsonify({'error': 'Professor & Course selection is required.'}), 400

        try:
            professor_load_id_int = int(professor_load_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid professor-course selection.'}), 400

        pc_res = supabase.table('professor_load').select(
            'professor_load_id, course_id, prof_id, course(course_id, course_name), professor(prof_id, first_name, last_name)'
        ).eq('professor_load_id', professor_load_id_int).execute()
        pc_row = _first(pc_res.data or [])
        if not pc_row:
            return jsonify({'error': 'Selected professor-course pairing is invalid.'}), 400

        course_id_int = pc_row.get('course_id')
        prof_id_int = pc_row.get('prof_id')
        c = _rel(pc_row, 'course') or {}
        course_name = c.get('course_name') or f"Course #{course_id_int}"
        p = _rel(pc_row, 'professor') or {}
        prof_name = f"{p.get('first_name','') or ''} {p.get('last_name','') or ''}".strip() or ''

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
                if other_prof_id is None and other.get('professor_load_id') == professor_load_id_int:
                    other_prof_id = prof_id_int

                if other_prof_id is not None and other_prof_id == prof_id_int:
                    if _has_conflict(day, start_time, end_time, [(other_day, other_start, other_end)]):
                        other_timeslot = other.get('time_range') or f"{other.get('start')} - {other.get('end')}"
                        return jsonify({
                            'error': f"Professor conflict: {prof_name} is already scheduled on {day} ({other_timeslot}) for Section {other.get('section')}."
                        }), 400

        # Apply updates to target preview entry
        target_entry['professor_load_id'] = professor_load_id_int
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
                'professor_load_id': professor_load_id_int,
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
@roles_required('scheduler')
def api_preview_entries():
    preview = _get_preview_for_user()
    return jsonify({'success': True, 'entries': preview or []})


@app.route('/confirm_preview', methods=['POST'])
@roles_required('scheduler')
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
        program_id = _find_program_id_by_name(prog_val) or session.get('program_id') or 1
        sections_in_preview = list({e.get('section') for e in preview if e.get('section')})

        # --- OPTIMIZATION: Bulk-fetch professor_load once; build lookup dicts ---
        # Replaces up to 3 DB queries per preview entry with a single upfront query.
        _pl_all = (supabase.table('professor_load').select('*').execute().data) or []
        _pl_by_id = {}
        _pl_by_prof_course = {}
        for _r in _pl_all:
            _rlid = _r.get('id') if _r.get('id') is not None else _r.get('professor_load_id')
            _rpid = _r.get('prof_id')
            _rcid = _r.get('course_id')
            if _rlid is not None:
                _pl_by_id[str(_rlid)] = _rlid
                _pl_by_id[_rlid] = _rlid
            if _rpid is not None and _rcid is not None and _rlid is not None:
                try:
                    _pl_by_prof_course[(int(_rpid), int(_rcid))] = _rlid
                except (ValueError, TypeError):
                    pass

        # Memoize program ID resolution to avoid repeated _get_programs() DB hits
        _prog_id_cache: dict = {prog_val: program_id}

        def _resolve_entry_program_id(entry_prog):
            ep = (entry_prog or prog_val or '').strip()
            if ep not in _prog_id_cache:
                _prog_id_cache[ep] = _find_program_id_by_name(ep) or program_id
            return _prog_id_cache[ep]

        # Prepare payload rows — all professor_load lookups use in-memory dicts (no DB calls)
        rows = []
        for entry in preview:
            entry_sem = (entry.get('semester') or sem_val).strip()
            entry_prog = entry.get('program') or prog_val or session.get('program', '')
            entry_program_id = _resolve_entry_program_id(entry_prog)
            professor_load_id = entry.get('professor_load_id')
            prof_id = entry.get('prof_id')
            course_id = entry.get('course_id')
            room_id = entry.get('room_id')

            # Resolve synthetic or missing professor_load_id using pre-fetched dicts
            resolved_load_id = None
            if professor_load_id:
                try:
                    raw_lid = int(professor_load_id)
                    if raw_lid >= 900000:
                        # Synthetic ID — look up real one by (prof, course)
                        if prof_id and course_id:
                            resolved_load_id = _pl_by_prof_course.get((int(prof_id), int(course_id)))
                    else:
                        if str(raw_lid) in _pl_by_id:
                            resolved_load_id = _pl_by_id[str(raw_lid)]
                        elif prof_id and course_id:
                            resolved_load_id = _pl_by_prof_course.get((int(prof_id), int(course_id)))
                        else:
                            resolved_load_id = None
                except (ValueError, TypeError):
                    resolved_load_id = None
            elif prof_id and course_id:
                try:
                    resolved_load_id = _pl_by_prof_course.get((int(prof_id), int(course_id)))
                except (ValueError, TypeError):
                    pass

            professor_load_id = resolved_load_id

            rows.append({
                'professor_load_id': int(professor_load_id) if professor_load_id not in (None, '', 0, '0') else None,
                'room_id': int(room_id) if room_id not in (None, '', 0, '0') else None,
                'day': entry.get('day') or 'Monday',
                'class_start': _to_time_string(entry.get('start')),
                'class_end': _to_time_string(entry.get('end')),
                'session_type': entry.get('session_type') or 'Lecture',
                'section': entry.get('section'),
                'semester': entry_sem,
                'major': entry.get('major'),
                'program_id': entry_program_id,
                'archive': False,
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
        new_confirm_batch_id = str(uuid.uuid4())
        now_confirm_utc = datetime.now(timezone.utc).isoformat()

        # Pre-tag currently active schedule records for this semester/program with the new batch ID and timestamp
        # so they retain their archive metadata whether archived by RPC or Python fallback
        try:
            active_tag_payload = {
                'archive_batch_id': new_confirm_batch_id,
                'archived_at': now_confirm_utc,
            }
            tag_query = supabase.table('schedule').update(active_tag_payload).eq('archive', False)
            if sem_val:
                tag_query = tag_query.eq('semester', sem_val)
            if prog_val and str(prog_val).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                tag_query = tag_query.eq('program_id', program_id)
            tag_query.execute()
        except Exception as tag_err:
            logging.debug(f"[confirm_preview] Active schedule pre-tagging skipped or warning ({tag_err})")

        if hasattr(supabase, 'rpc'):
            try:
                rpc_res = supabase.rpc('confirm_schedule_transaction', {
                    'p_semester': sem_val,
                    'p_program': prog_val,
                    'p_program_id': program_id,
                    'p_rows': rows,
                    'p_clear_scope': True,
                    'p_archived_by': archived_by,
                    'p_batch_id': new_confirm_batch_id
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
            if not program_id:
                program_id = session.get('program_id') or 1

            # Fallback: Soft-archive existing active records for this program by setting archive = True
            # with archive_batch_id and archived_at (avoiding non-existent columns)
            try:
                archive_meta_payload = {
                    'archive': True,
                    'archive_batch_id': new_confirm_batch_id,
                    'archived_at': now_confirm_utc,
                }
                old_update = supabase.table('schedule').update(archive_meta_payload).eq('archive', False)
                if sem_val:
                    old_update = old_update.eq('semester', sem_val)
                if prog_val and str(prog_val).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                    old_update = old_update.eq('program_id', program_id)
                old_update.execute()
            except Exception as arc_err:
                logging.warning(f"[confirm_preview] Fallback soft-archive with metadata warning ({arc_err}); retrying base archive=True")
                old_update = supabase.table('schedule').update({'archive': True}).eq('archive', False)
                if sem_val:
                    old_update = old_update.eq('semester', sem_val)
                if prog_val and str(prog_val).strip().lower() not in ('global / all programs', 'all', 'all programs', 'null', ''):
                    old_update = old_update.eq('program_id', program_id)
                old_update.execute()

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
@roles_required('scheduler')
def discard_preview():
    _clear_preview_for_user()
    return redirect(url_for('home'))


PHT_TZ = timezone(timedelta(hours=8))


def _format_pht_timestamp(raw_ts):
    """Convert UTC ISO timestamp to Philippine Time (UTC+8) formatted string, e.g. 'Oct 2, 2026, 12:33 AM'."""
    if not raw_ts:
        return 'Archived Schedule'
    try:
        dt = datetime.fromisoformat(str(raw_ts).replace('Z', '+00:00'))
        pht_dt = dt.astimezone(PHT_TZ)
        time_part = pht_dt.strftime('%I:%M %p').lstrip('0')
        return f"{pht_dt.strftime('%b')} {pht_dt.day}, {pht_dt.year}, {time_part}"
    except Exception:
        return str(raw_ts)


def _get_pht_date_str(raw_ts):
    """Extract YYYY-MM-DD in Philippine Time for date range comparison."""
    if not raw_ts:
        return ''
    try:
        dt = datetime.fromisoformat(str(raw_ts).replace('Z', '+00:00'))
        pht_dt = dt.astimezone(PHT_TZ)
        return pht_dt.strftime('%Y-%m-%d')
    except Exception:
        return ''


@app.route('/archive_schedule', methods=['POST'])
@roles_required('scheduler')
def archive_schedule():
    """Archive currently active schedule for the program so a new one can be generated."""
    user_prog_id = _get_user_program_id()
    user_program = session.get('program', '')
    user_role = (session.get('role') or 'scheduler').lower()
    redirect_target = request.form.get('redirect_to') or request.args.get('redirect_to') or url_for('schedules')

    if not user_prog_id and user_program:
        user_prog_id = _find_program_id_by_name(user_program)

    archived_by = session.get('username') or session.get('first_name') or 'Scheduler'
    if session.get('last_name'):
        archived_by = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()

    try:
        rpc_success = False
        count = 0
        new_batch_id = str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()

        # 1. Try atomic archive_active_schedule RPC
        if hasattr(supabase, 'rpc'):
            try:
                rpc_res = supabase.rpc('archive_active_schedule', {
                    'p_archived_by': archived_by,
                    'p_program_id': user_prog_id if user_role not in ('super_admin', 'admin') else None,
                    'p_reason': 'Manually archived'
                }).execute()
                if rpc_res and getattr(rpc_res, 'data', None):
                    d = rpc_res.data
                    if isinstance(d, dict) and d.get('success'):
                        rpc_success = True
                        count = int(d.get('archived_count', 0))
            except Exception as rpc_err:
                logging.warning(f"archive_active_schedule RPC fallback: {rpc_err}")
                rpc_success = False

        if not rpc_success:
            # 2. Python fallback: update active rows in schedule table directly
            count_q = supabase.table('schedule').select('schedule_id', count='exact').eq('archive', False)
            if user_prog_id and user_role not in ('super_admin', 'admin'):
                count_q = count_q.eq('program_id', user_prog_id)
            count_res = count_q.execute()
            count = count_res.count if hasattr(count_res, 'count') and count_res.count is not None else len(count_res.data or [])

            try:
                up_payload = {
                    'archive': True,
                    'archive_batch_id': new_batch_id,
                    'archived_at': now_utc,
                }
                up_query = supabase.table('schedule').update(up_payload).eq('archive', False)
                if user_prog_id and user_role not in ('super_admin', 'admin'):
                    up_query = up_query.eq('program_id', user_prog_id)
                up_query.execute()
            except Exception as up_err:
                logging.warning(f"Update schedule with archive_batch_id fallback ({up_err}); setting archive=True only")
                up_query = supabase.table('schedule').update({'archive': True}).eq('archive', False)
                if user_prog_id and user_role not in ('super_admin', 'admin'):
                    up_query = up_query.eq('program_id', user_prog_id)
                up_query.execute()

        log_activity('archive', 'schedule', f'Archived active schedule ({count} classes)')
        flash(f'Active schedule has been archived successfully ({count} classes archived).', 'success')
    except Exception as err:
        logging.exception(f"Error archiving schedule: {err}")
        flash(f'Error archiving schedule: {err}', 'danger')

    session.pop('active_semester', None)
    return redirect(redirect_target)


# ──────────────────────────────────────────────────────────────────────────────
# SCHEDULE ARCHIVE & RESTORATION (Based on schedule table & RPC)
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/schedule_archive')
@roles_required('admin', 'scheduler', 'viewer')
def schedule_archive():
    semester_filter = (request.args.get('semester') or '').strip()
    program_filter = (request.args.get('program') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

    # Empty filter values must be treated as "no filter"
    if semester_filter.lower() in ('', 'all', 'all semesters'):
        semester_filter = ''
    if program_filter.lower() in ('', 'all', 'all programs'):
        program_filter = ''

    user_role = (session.get('role') or 'scheduler').lower()
    user_program = session.get('program', '')

    effective_program = user_program if (user_role not in ('super_admin', 'admin') and user_program) else program_filter
    effective_program_id = _find_program_id_by_name(effective_program) if effective_program else None

    batches_list = []
    fetch_error = False
    error_message = None

    try:
        # 1. Primary: Database RPC aggregation if function is installed
        rpc_success = False
        if hasattr(supabase, 'rpc'):
            try:
                p_date_from = None
                p_date_to = None
                if date_from:
                    try:
                        p_date_from = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=PHT_TZ).astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass
                if date_to:
                    try:
                        p_date_to = (datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=PHT_TZ)).astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass

                rpc_params = {
                    'p_program': effective_program or None,
                    'p_semester': semester_filter or None,
                }
                if p_date_from or p_date_to:
                    rpc_params['p_date_from'] = p_date_from
                    rpc_params['p_date_to'] = p_date_to

                try:
                    res = supabase.rpc('get_schedule_archive_batches', rpc_params).execute()
                except Exception:
                    res = supabase.rpc('get_schedule_archive_batches', {
                        'p_program': effective_program or None,
                        'p_semester': semester_filter or None
                    }).execute()

                if res and isinstance(res.data, list):
                    rpc_success = True
                    for r in res.data:
                        bid = str(r.get('batch_id') or '')
                        if not bid or bid == 'None':
                            bid = 'legacy'
                        arch_at_raw = r.get('archived_at')
                        is_legacy = (bid == 'legacy' or not arch_at_raw)
                        arch_at_fmt = 'Legacy Archive' if is_legacy else _format_pht_timestamp(arch_at_raw)
                        date_str = '' if is_legacy else _get_pht_date_str(arch_at_raw)

                        # Enforce date filter in Python (AND logic)
                        if date_from and (is_legacy or not date_str or date_str < date_from):
                            continue
                        if date_to and (is_legacy or not date_str or date_str > date_to):
                            continue

                        secs = sorted(list(r.get('sections') or []))
                        batches_list.append({
                            'batch_id': bid,
                            'semester': r.get('semester') or '1st Semester',
                            'program': r.get('program') or '',
                            'archived_at_raw': arch_at_raw,
                            'archived_at_fmt': arch_at_fmt,
                            'archived_at_date': date_str,
                            'archived_by': r.get('archived_by') or ('System (Legacy)' if is_legacy else 'Scheduler'),
                            'archive_reason': r.get('archive_reason') or ('Legacy archive' if is_legacy else 'Archived schedule'),
                            'entry_count': int(r.get('entry_count') or 0),
                            'section_count': int(r.get('section_count') or len(secs)),
                            'sections_preview': ', '.join(secs[:6]) + ('...' if len(secs) > 6 else ''),
                            'is_legacy': is_legacy,
                        })
            except Exception as rpc_err:
                logging.debug(f"get_schedule_archive_batches RPC skipped ({rpc_err}), fallback.")
                rpc_success = False

        if not rpc_success:
            # 2. Query schedule table directly with 1000-row pagination (handles 5000+ rows)
            batches_map = {}
            all_rows = []
            page_size = 1000
            start = 0

            # Safe column list (Query only existing columns on schedule table)
            use_batch_cols = True
            while True:
                try:
                    cols = 'schedule_id, semester, program_id, program:program_id(program_name), section, major, archive, archive_batch_id, archived_at' if use_batch_cols else 'schedule_id, semester, program_id, program:program_id(program_name), section, major, archive'
                    query = supabase.table('schedule').select(cols).eq('archive', True)
                    if effective_program_id:
                        query = query.eq('program_id', effective_program_id)
                    if semester_filter:
                        query = query.eq('semester', semester_filter)
                    if hasattr(query, 'range'):
                        chunk_res = query.range(start, start + page_size - 1).execute()
                    else:
                        chunk_res = query.execute()
                    chunk_data = chunk_res.data or []
                    all_rows.extend(chunk_data)
                    if not hasattr(query, 'range') or len(chunk_data) < page_size:
                        break
                    start += page_size
                except Exception as col_err:
                    if use_batch_cols and ('archive_batch_id' in str(col_err) or '42703' in str(col_err) or 'PGRST204' in str(col_err)):
                        logging.warning(f"Archive metadata columns warning ({col_err}); querying base columns.")
                        use_batch_cols = False
                        start = 0
                        all_rows = []
                        continue
                    else:
                        raise col_err

            for r in all_rows:
                raw_batch_id = r.get('archive_batch_id')
                sem = r.get('semester') or '1st Semester'
                prog = (_rel(r, 'program') or {}).get('program_name') or ''

                if raw_batch_id and str(raw_batch_id).strip() and str(raw_batch_id).strip() != 'None':
                    bid = str(raw_batch_id).strip()
                    is_legacy = False
                    arch_at_raw = r.get('archived_at')
                    arch_at_fmt = _format_pht_timestamp(arch_at_raw)
                    date_str = _get_pht_date_str(arch_at_raw)
                    arch_by = 'Scheduler'
                    arch_reason = 'Archived schedule'
                else:
                    bid = 'legacy'
                    is_legacy = True
                    arch_at_raw = None
                    arch_at_fmt = 'Legacy Archive'
                    date_str = ''
                    arch_by = 'System (Legacy)'
                    arch_reason = 'Legacy archive'

                if bid not in batches_map:
                    batches_map[bid] = {
                        'batch_id': bid,
                        'semester': sem,
                        'program': prog,
                        'archived_at_raw': arch_at_raw,
                        'archived_at_fmt': arch_at_fmt,
                        'archived_at_date': date_str,
                        'archived_by': arch_by,
                        'archive_reason': arch_reason,
                        'entry_count': 0,
                        'sections_set': set(),
                        'programs_set': set(),
                        'semesters_set': set(),
                        'is_legacy': is_legacy,
                    }
                batches_map[bid]['entry_count'] += 1
                if r.get('section'):
                    batches_map[bid]['sections_set'].add(r.get('section'))
                if prog:
                    batches_map[bid]['programs_set'].add(prog)
                if sem:
                    batches_map[bid]['semesters_set'].add(sem)

            for b in batches_map.values():
                if b['is_legacy']:
                    if len(b['semesters_set']) == 1:
                        b['semester'] = list(b['semesters_set'])[0]
                    elif len(b['semesters_set']) > 1:
                        b['semester'] = 'Multiple Semesters'
                    if len(b['programs_set']) == 1:
                        b['program'] = list(b['programs_set'])[0]
                    elif len(b['programs_set']) > 1:
                        b['program'] = 'Multiple Programs'

                date_str = b.get('archived_at_date') or ''
                if date_from and (b['is_legacy'] or not date_str or date_str < date_from):
                    continue
                if date_to and (b['is_legacy'] or not date_str or date_str > date_to):
                    continue

                sorted_secs = sorted(list(b['sections_set']))
                b['section_count'] = len(sorted_secs)
                b['sections_preview'] = ', '.join(sorted_secs[:6]) + ('...' if len(sorted_secs) > 6 else '')
                batches_list.append(b)

        # Sort newest first by archive timestamp (legacy entries with None/empty string go to the end)
        batches_list.sort(key=lambda x: str(x.get('archived_at_raw') or ''), reverse=True)

        semester_options = ['1st Semester', '2nd Semester']
        program_options = ['BSIT']
        if user_program and user_program not in program_options:
            program_options.append(user_program)

    except Exception as err:
        logging.exception(f"Error retrieving schedule archive: {err}")
        fetch_error = True
        error_message = str(err)
        batches_list = []
        semester_options = ['1st Semester', '2nd Semester']
        program_options = ['BSIT']

    has_active_filters = bool(semester_filter or program_filter or date_from or date_to)

    return render_template(
        'schedule_archive.html',
        active_page='schedule_archive',
        archive_batches=batches_list,
        semester_options=semester_options,
        program_options=program_options,
        semester_filter=semester_filter,
        program_filter=program_filter,
        date_from=date_from,
        date_to=date_to,
        has_active_filters=has_active_filters,
        fetch_error=fetch_error,
        error_message=error_message,
    )


@app.route('/schedule_archive/<batch_id>')
@roles_required('admin', 'scheduler', 'viewer')
def view_schedule_archive_batch(batch_id):
    try:
        rows = []
        page_size = 1000
        start = 0

        while True:
            query = supabase.table('schedule').select(
                '*, program:program_id(program_name), professor_load(course_id, prof_id, course(course_name), professor(first_name, last_name)), room(room_name)'
            ).eq('archive', True)

            if batch_id == 'legacy':
                query = query.is_('archive_batch_id', 'null')
            elif '__' in batch_id:
                sem_part, prog_part = batch_id.split('__', 1)
                sem_name = sem_part.replace('_', ' ')
                query = query.eq('semester', sem_name)
                if prog_part and prog_part != 'all':
                    query = query.eq('program_id', _find_program_id_by_name(prog_part) or -1)
            else:
                query = query.eq('archive_batch_id', batch_id)

            if hasattr(query, 'range'):
                chunk_res = query.range(start, start + page_size - 1).execute()
            else:
                chunk_res = query.execute()
            chunk_data = chunk_res.data or []
            rows.extend(chunk_data)
            if not hasattr(query, 'range') or len(chunk_data) < page_size:
                break
            start += page_size

        if not rows and batch_id != 'legacy':
            try:
                fallback_q = supabase.table('schedule').select(
                    '*, program:program_id(program_name), professor_load(course_id, prof_id, course(course_name), professor(first_name, last_name)), room(room_name)'
                ).eq('archive', True).or_(f"archive_batch_id.eq.{batch_id},batch_id.eq.{batch_id}")
                if hasattr(fallback_q, 'range'):
                    rows = fallback_q.range(0, 999).execute().data or []
                else:
                    rows = fallback_q.execute().data or []
            except Exception:
                pass

        if not rows:
            flash('Archived schedule version not found.', 'warning')
            return redirect(url_for('schedule_archive'))

        first_row = rows[0]
        arch_at_raw = first_row.get('archived_at')
        arch_at_fmt = 'Legacy Archive' if batch_id == 'legacy' or not arch_at_raw else _format_pht_timestamp(arch_at_raw)

        prog_name = (_rel(first_row, 'program') or {}).get('program_name') or ''

        batch_info = {
            'batch_id': batch_id,
            'semester': first_row.get('semester') or '1st Semester',
            'program': prog_name or 'All Programs',
            'archived_at_fmt': arch_at_fmt,
            'archived_by': first_row.get('archived_by') or ('System (Legacy)' if batch_id == 'legacy' else 'Scheduler'),
            'archive_reason': first_row.get('archive_reason') or ('Legacy archive' if batch_id == 'legacy' else 'Archived schedule'),
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

            pc = _rel(r, 'professor_load') or {}
            c = _rel(r, 'course') or _rel(pc, 'course') or {}
            p = _rel(r, 'professor') or _rel(pc, 'professor') or {}
            rm = _rel(r, 'room') or {}
            pname = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or 'TBA'
            cname = c.get('course_name') or (f"Course #{r.get('course_id')}" if r.get('course_id') else 'TBA')
            s_fmt = _format_time(r.get('class_start'))
            e_fmt = _format_time(r.get('class_end'))

            sections_by_key[key]['entries'].append({
                'professor_load_id': r.get('professor_load_id'),
                'course_id': r.get('course_id') or pc.get('course_id'),
                'course_name': cname,
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
@app.route('/restore_schedule/<batch_id>', methods=['POST'])
@roles_required('scheduler')
def restore_schedule_archive(batch_id):
    user_role = _normalize_role(session.get('role', ''))
    if user_role != 'scheduler':
        flash('You do not have permission to restore archived schedules.', 'danger')
        return redirect(url_for('schedule_archive'))

    restored_by = session.get('username') or session.get('first_name') or 'Scheduler'
    if session.get('last_name'):
        restored_by = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()

    try:
        rpc_success = False
        res_data = None
        if hasattr(supabase, 'rpc') and batch_id != 'legacy' and '__' not in batch_id:
            try:
                rpc_res = supabase.rpc('restore_archived_schedule_batch', {
                    'p_batch_id': batch_id,
                    'p_restored_by': restored_by
                }).execute()
                if rpc_res and getattr(rpc_res, 'data', None):
                    data = rpc_res.data
                    if isinstance(data, dict) and data.get('success'):
                        res_data = data
                        rpc_success = True
                    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and data[0].get('success'):
                        res_data = data[0]
                        rpc_success = True
            except Exception as rpc_err:
                logging.warning(f"restore_archived_schedule_batch RPC fallback: {rpc_err}")
                rpc_success = False

        if not rpc_success:
            # Fallback restore logic directly in schedule table
            sample_q = supabase.table('schedule').select('semester, program_id, program:program_id(program_name)').eq('archive', True)
            if batch_id == 'legacy':
                sample_q = sample_q.is_('archive_batch_id', 'null')
            elif '__' in batch_id:
                sem_part, prog_part = batch_id.split('__', 1)
                sample_q = sample_q.eq('semester', sem_part.replace('_', ' '))
                if prog_part and prog_part != 'all':
                    sample_q = sample_q.eq('program_id', _find_program_id_by_name(prog_part) or -1)
            else:
                sample_q = sample_q.eq('archive_batch_id', batch_id)

            sample_rows = sample_q.limit(1).execute().data or []
            target_sem = (sample_rows[0].get('semester') if sample_rows else None) or '1st Semester'
            target_prog_id = sample_rows[0].get('program_id') if sample_rows else _get_user_program_id()

            # Rule: One active schedule per semester.
            # Archive ANY currently active schedule for this semester & program
            new_archive_batch = str(uuid.uuid4())
            now_utc = datetime.now(timezone.utc).isoformat()
            user_prog_id = _get_user_program_id()
            try:
                archive_curr_q = supabase.table('schedule').update({
                    'archive': True,
                    'archive_batch_id': new_archive_batch,
                    'archived_at': now_utc,
                }).eq('archive', False)
                if user_prog_id:
                    archive_curr_q = archive_curr_q.eq('program_id', user_prog_id)
                elif target_prog_id:
                    archive_curr_q = archive_curr_q.eq('program_id', target_prog_id)
                archive_curr_q.execute()
            except Exception as arc_err:
                logging.warning(f"Archive current schedule fallback ({arc_err}); setting archive=True only")
                archive_curr_q = supabase.table('schedule').update({'archive': True}).eq('archive', False)
                if user_prog_id:
                    archive_curr_q = archive_curr_q.eq('program_id', user_prog_id)
                elif target_prog_id:
                    archive_curr_q = archive_curr_q.eq('program_id', target_prog_id)
                archive_curr_q.execute()

            # Reactivate the specified batch (archive = False)
            restore_q = supabase.table('schedule').update({'archive': False})
            if batch_id == 'legacy':
                restore_q = restore_q.eq('archive', True).is_('archive_batch_id', 'null')
                if target_sem:
                    restore_q = restore_q.eq('semester', target_sem)
                if user_prog_id:
                    restore_q = restore_q.eq('program_id', user_prog_id)
                elif target_prog_id:
                    restore_q = restore_q.eq('program_id', target_prog_id)
                restore_res = restore_q.execute()
            elif '__' in batch_id:
                restore_q = restore_q.eq('semester', target_sem).eq('archive', True)
                if user_prog_id:
                    restore_q = restore_q.eq('program_id', user_prog_id)
                elif target_prog_id:
                    restore_q = restore_q.eq('program_id', target_prog_id)
                restore_res = restore_q.execute()
            else:
                try:
                    restore_q = restore_q.eq('archive_batch_id', batch_id).eq('archive', True)
                    restore_res = restore_q.execute()
                    if not getattr(restore_res, 'data', None):
                        # Fallback for mock/test objects that used batch_id
                        restore_res = supabase.table('schedule').update({'archive': False}).or_(f"archive_batch_id.eq.{batch_id},batch_id.eq.{batch_id}").execute()
                except Exception:
                    restore_res = supabase.table('schedule').update({'archive': False}).or_(f"archive_batch_id.eq.{batch_id},batch_id.eq.{batch_id}").execute()

            restored_count = len(restore_res.data or []) if restore_res and restore_res.data else 'all'
            res_data = {'semester': target_sem, 'restored_count': restored_count}

        target_semester = (res_data or {}).get('semester') or '1st Semester'
        count = (res_data or {}).get('restored_count') or 'all'

        session['active_semester'] = target_semester
        log_activity('restore', 'schedule', f'Restored schedule batch {batch_id} ({target_semester})')
        flash(f'Archived schedule version restored successfully! ({count} classes reactivated)', 'success')
        return redirect(url_for('schedules', semester=target_semester))

    except Exception as err:
        logging.exception(f"Error restoring schedule archive {batch_id}: {err}")
        flash(f"Error restoring schedule version: {err}", "error")
        return redirect(url_for('schedule_archive'))


@app.route('/delete_schedule_archive/<batch_id>', methods=['POST'])
@roles_required('scheduler')
def delete_schedule_archive(batch_id):
    user_role = _normalize_role(session.get('role', ''))
    if user_role != 'scheduler':
        flash('Only schedulers can delete archived schedule records.', 'danger')
        return redirect(url_for('schedule_archive'))

    try:
        del_q = supabase.table('schedule').delete().eq('archive', True)
        if batch_id == 'legacy':
            try:
                del_q = del_q.is_('archive_batch_id', 'null')
            except Exception:
                pass
            del_q.execute()
        elif '__' in batch_id:
            sem_part, prog_part = batch_id.split('__', 1)
            target_sem = sem_part.replace('_', ' ')
            target_prog = '' if prog_part == 'all' else prog_part
            del_q = del_q.eq('semester', target_sem)
            if target_prog:
                del_q = del_q.eq('program_id', _find_program_id_by_name(target_prog) or -1)
            del_q.execute()
        else:
            try:
                del_res = del_q.eq('archive_batch_id', batch_id).execute()
                if not getattr(del_res, 'data', None):
                    supabase.table('schedule').delete().eq('archive', True).or_(f"archive_batch_id.eq.{batch_id},batch_id.eq.{batch_id}").execute()
            except Exception:
                try:
                    supabase.table('schedule').delete().eq('archive', True).or_(f"archive_batch_id.eq.{batch_id},batch_id.eq.{batch_id}").execute()
                except Exception:
                    pass

        log_activity('delete', 'schedule', f'Deleted archived schedule batch {batch_id}')
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
    'academic_ranking',
    'professor',
    'room',
    'timeslot',
    'course',
    'professor_load',
    'schedule',
    'irregular_students',
    'irregular_student_schedule',
    'delete_requests',
    'activity_log',
]

BACKUP_TABLES_DELETE_ORDER = list(reversed(BACKUP_TABLES_INSERT_ORDER))

BACKUP_PKS = {
    'program_department': 'program_name',
    'users': 'id',
    'academic_ranking': 'academic_ranking_id',
    'professor': 'prof_id',
    'room': 'room_id',
    'timeslot': 'timeslot_id',
    'course': 'course_id',
    'professor_load': 'professor_load_id',
    'schedule': 'schedule_id',
    'irregular_students': 'student_id',
    'irregular_student_schedule': 'id',
    'delete_requests': 'id',
    'activity_log': 'id',
}

# Alias for backwards compatibility
BACKUP_TABLES = BACKUP_TABLES_INSERT_ORDER


def _fetch_all_table_data(table_name, page_size=1000):
    """Fetch all rows from a Supabase table using pagination to avoid row limits."""
    all_rows = []
    start = 0
    while True:
        try:
            res = supabase.table(table_name).select('*').range(start, start + page_size - 1).execute()
            rows = res.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size
        except Exception as e:
            logging.warning(f"Could not fetch data for table {table_name}: {e}")
            break
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
@roles_required('admin')
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
@roles_required('admin')
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
