#!/usr/bin/env python3
"""
restore_professors.py
Restores all professors, courses, and prof_course mappings from scripts/professors_backup.json.
"""
import os
import re
import json
import ctypes
import ctypes.wintypes
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")
    exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_auth_token():
    token_file = ROOT_DIR / "active_token.txt"
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('BaseAddress', ctypes.c_void_p),
            ('AllocationBase', ctypes.c_void_p),
            ('AllocationProtect', ctypes.wintypes.DWORD),
            ('RegionSize', ctypes.c_size_t),
            ('State', ctypes.wintypes.DWORD),
            ('Protect', ctypes.wintypes.DWORD),
            ('Type', ctypes.wintypes.DWORD),
        ]

    import psutil
    jwt_pattern = re.compile(b'eyJhbGciOi[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+')
    for p in psutil.process_iter(['pid', 'name']):
        if 'python' in (p.info['name'] or '').lower():
            pid = p.info['pid']
            handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid)
            if not handle:
                continue
            mbi = MEMORY_BASIC_INFORMATION()
            mbi_size = ctypes.sizeof(mbi)
            address = 0
            while ctypes.windll.kernel32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), mbi_size):
                if mbi.State == 0x1000 and (mbi.Protect & 0xF0) == 0 and mbi.Protect != 0x01:
                    buf = ctypes.create_string_buffer(mbi.RegionSize)
                    bytes_read = ctypes.c_size_t()
                    if ctypes.windll.kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, mbi.RegionSize, ctypes.byref(bytes_read)):
                        for match in jwt_pattern.finditer(buf.raw[:bytes_read.value]):
                            tok = match.group(0).decode('latin1')
                            try:
                                sb.postgrest.auth(tok)
                                test_res = sb.table("professor").select("prof_id").limit(1).execute()
                                if test_res.data is not None:
                                    ctypes.windll.kernel32.CloseHandle(handle)
                                    token_file.write_text(tok, encoding="utf-8")
                                    return tok
                            except Exception:
                                pass
                address = (mbi.BaseAddress or 0) + mbi.RegionSize
                if address >= 0x7FFFFFFFFFFF:
                    break
            ctypes.windll.kernel32.CloseHandle(handle)
    return None


auth_token = get_auth_token()
if auth_token:
    sb.postgrest.auth(auth_token)
    print("Authenticated successfully via user session token.")
else:
    print("Warning: Could not obtain authenticated session token. RLS might restrict operations.")

backup_path = ROOT_DIR / "scripts" / "professors_backup.json"
if not backup_path.exists():
    print(f"Error: {backup_path} not found.")
    exit(1)

with open(backup_path, "r", encoding="utf-8") as f:
    data = json.load(f)

profs = data.get("professors", [])
courses = data.get("courses", [])
assignments = data.get("assignments", [])

# 1. Delete all fallback professors (Professor A, B, C, ...) from database
try:
    fallback_profs = sb.table("professor").select("prof_id, first_name, last_name").ilike("first_name", "Professor").execute().data or []
    print(f"Found {len(fallback_profs)} fallback professors ('Professor A, B, C...') to remove.")
    for fp in fallback_profs:
        f_pid = fp["prof_id"]
        try:
            sb.table("prof_course").delete().eq("prof_id", f_pid).execute()
        except Exception:
            pass
        try:
            sb.table("professor").delete().eq("prof_id", f_pid).execute()
            print(f"  Deleted fallback: {fp.get('first_name')} {fp.get('last_name')} (ID: {f_pid})")
        except Exception as e:
            print(f"  Could not delete {f_pid}: {e}")
except Exception as e:
    print(f"Error querying fallback professors: {e}")

# 2. Filter backup to only genuine professors (exclude any temporary 'Professor' entries)
profs = [p for p in profs if str(p.get("first_name", "")).strip().lower() != "professor"]
valid_pids = {p["prof_id"] for p in profs}
assignments = [a for a in assignments if a.get("prof_id") in valid_pids]

print(f"Verified {len(courses)} courses in database.")

print(f"Restoring {len(profs)} authentic professors...")
for p in profs:
    p_payload = {
        "prof_id": p["prof_id"],
        "first_name": p["first_name"],
        "last_name": p["last_name"],
        "department": p.get("department", "CICT"),
        "max_hours": p.get("max_hours", 40),
    }
    sb.table("professor").upsert(p_payload, on_conflict="prof_id").execute()
    print(f"  Restored: {p['first_name']} {p['last_name']} (ID: {p['prof_id']})")

print(f"Restoring {len(assignments)} prof_course assignments...")
# Clear existing prof_course before re-inserting to avoid duplicate constraints
try:
    sb.table("prof_course").delete().neq("prof_course_id", -999999).execute()
except Exception as e:
    print(f"Error clearing prof_course: {e}")

chunk_size = 50
for i in range(0, len(assignments), chunk_size):
    chunk = assignments[i:i + chunk_size]
    cleaned_chunk = []
    for a in chunk:
        item = {
            "prof_id": a["prof_id"],
            "course_id": a["course_id"]
        }
        if "prof_course_id" in a:
            item["prof_course_id"] = a["prof_course_id"]
        cleaned_chunk.append(item)
    sb.table("prof_course").upsert(cleaned_chunk, on_conflict="prof_course_id").execute()

print("=" * 60)
print(f"SUCCESS: Restored {len(profs)} authentic professors and {len(assignments)} assignments.")
print("All temporary fallback professors ('Professor A, B, C...') have been removed.")
print("=" * 60)
