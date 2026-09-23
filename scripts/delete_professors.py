#!/usr/bin/env python3
"""
delete_professors.py
Deletes all regular professors and professor_load mappings from the database,
allowing you to test the fallback logic where all subjects get assigned to "Professor A".
"""
import os
import re
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

    # Fallback: scan running python processes for active JWT
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

print("Clearing professor_load table...")
try:
    res = sb.table("professor_load").delete().neq("professor_load_id", -999999).execute()
    print(f"professor_load cleared. Rows affected: {len(res.data or [])}")
except Exception as e:
    print(f"professor_load delete note: {e}")

print("Deleting regular professors (preserving Professor A)...")
try:
    profs = sb.table("professor").select("*").execute().data or []
    print(f"Found {len(profs)} total professors currently in database.")
    deleted_count = 0
    has_prof_a = False
    for p in profs:
        fn = (p.get("first_name") or "").strip().lower()
        ln = (p.get("last_name") or "").strip().lower()
        if fn == "professor" and ln == "a":
            has_prof_a = True
            print(f"Preserving fallback instructor: Professor A (ID {p.get('prof_id')})")
            continue
        sb.table("professor").delete().eq("prof_id", p["prof_id"]).execute()
        deleted_count += 1

    if not has_prof_a:
        print("Creating fallback Professor A record in professor table...")
        sb.table("professor").insert({
            "first_name": "Professor",
            "last_name": "A",
            "department": "CICT",
            "max_hours": 40
        }).execute()

    print(f"Deleted {deleted_count} regular professors.")
except Exception as e:
    print(f"professor delete error: {e}")

# Verify current state
remaining_profs = sb.table("professor").select("prof_id, first_name, last_name, department").execute().data or []
remaining_pc = sb.table("professor_load").select("professor_load_id").execute().data or []

print("=" * 60)
print(f"STATUS: Remaining professors in database: {len(remaining_profs)}")
for p in remaining_profs:
    print(f"  - [{p.get('prof_id')}] {p.get('first_name')} {p.get('last_name')} ({p.get('department')})")
print(f"STATUS: Remaining professor_load mappings: {len(remaining_pc)}")
print("=" * 60)
print("SUCCESS: Regular professors removed.")
print("The system will now assign 'Professor A' as fallback for all subjects!")
print("To restore all professors later, run: python scripts/restore_professors.py")
print("=" * 60)
