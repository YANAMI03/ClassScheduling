#!/usr/bin/env python3
"""
scripts/apply_migration.py

Applies the SQL schema migration directly to the Supabase PostgreSQL database
using psycopg2, replacing prof_id and course_id on public.schedule with prof_course_id.

Usage:
  python scripts/apply_migration.py --db-password "YOUR_DB_PASSWORD"
  or
  python scripts/apply_migration.py --conn-str "postgresql://postgres:[PASSWORD]@db.maaeqnmziwhocziwgjpo.supabase.co:5432/postgres"
  or
  Set DATABASE_URL or SUPABASE_DB_PASSWORD in .env and run:
  python scripts/apply_migration.py
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

MIGRATION_FILE = ROOT_DIR / "migrations" / "20260910_replace_schedule_prof_course_fk.sql"
PROJECT_REF = "maaeqnmziwhocziwgjpo"

def parse_args():
    parser = argparse.ArgumentParser(description="Apply SQL migration to Supabase PostgreSQL.")
    parser.add_argument("--db-password", "-p", help="PostgreSQL database password for user 'postgres'")
    parser.add_argument("--conn-str", "-c", help="Full PostgreSQL connection URI")
    parser.add_argument("--check-only", action="store_true", help="Check database schema without applying")
    return parser.parse_args()

def get_connection_candidates(password=None, conn_str=None):
    if conn_str:
        return [conn_str]

    env_conn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("SUPABASE_DB_URL")
    if env_conn:
        return [env_conn]

    pwd = password or os.environ.get("SUPABASE_DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASSWORD")
    if not pwd:
        return []

    # Common Supabase connection formats
    return [
        f"postgresql://postgres:{pwd}@db.{PROJECT_REF}.supabase.co:5432/postgres?sslmode=require",
        f"postgresql://postgres.{PROJECT_REF}:{pwd}@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require",
        f"postgresql://postgres.{PROJECT_REF}:{pwd}@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require",
        f"postgresql://postgres:{pwd}@db.{PROJECT_REF}.supabase.co:6543/postgres?sslmode=require",
    ]

def check_schema_via_postgrest():
    """Checks the live schema using Supabase PostgREST API."""
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
        if not url or not key:
            return None
        client = create_client(url, key)
        res = client.table("schedule").select("prof_course_id").limit(1).execute()
        return True
    except Exception as e:
        err_msg = str(e)
        if "prof_course_id does not exist" in err_msg or "42703" in err_msg:
            return False
        # If permission or other error, return None
        return None

def main():
    args = parse_args()

    print("=" * 70)
    print(" Supabase Migration: Replace prof_id / course_id with prof_course_id")
    print("=" * 70)

    # 1. Check current PostgREST status
    print("\n[1/3] Checking current Supabase schedule table schema...")
    has_column = check_schema_via_postgrest()
    if has_column is True:
        print("  -> Column 'schedule.prof_course_id' ALREADY EXISTS on Supabase!")
        print("  -> Migration is already active on the live database.")
        return 0
    elif has_column is False:
        print("  -> Verified: 'schedule.prof_course_id' does NOT yet exist on Supabase.")
        print("  -> Schema change is needed.")
    else:
        print("  -> Note: PostgREST check returned indeterminate status or table is empty.")

    if args.check_only:
        return 0

    # 2. Check for migration SQL file
    if not MIGRATION_FILE.exists():
        print(f"\n[ERROR] Migration file not found: {MIGRATION_FILE}")
        return 1

    sql_content = MIGRATION_FILE.read_text(encoding="utf-8")
    print(f"\n[2/3] Migration script loaded: {MIGRATION_FILE.name} ({len(sql_content)} bytes)")

    # 3. Connect and execute via psycopg2
    try:
        import psycopg2
    except ImportError:
        print("\n[ERROR] psycopg2 is not installed. Install via: pip install psycopg2-binary")
        return 1

    candidates = get_connection_candidates(password=args.db_password, conn_str=args.conn_str)

    if not candidates:
        print("\n" + "!" * 70)
        print("ACTION REQUIRED TO RUN MIGRATION DIRECTLY:")
        print("No database password or connection string was found.")
        print("You have two simple options to execute the migration:\n")
        print("OPTION 1: Supabase Dashboard SQL Editor (Recommended - 30 seconds)")
        print("  1. Open: https://supabase.com/dashboard/project/maaeqnmziwhocziwgjpo/sql/new")
        print(f"  2. Paste the contents of:\n     {MIGRATION_FILE}")
        print("  3. Click 'Run'.\n")
        print("OPTION 2: Run via CLI with your Supabase DB Password")
        print("  Run this command:")
        print(f'     python scripts/apply_migration.py --db-password "YOUR_SUPABASE_DB_PASSWORD"')
        print("  or add to your .env file:")
        print('     SUPABASE_DB_PASSWORD=your_password_here')
        print("!" * 70 + "\n")
        return 2

    print("\n[3/3] Connecting to Supabase PostgreSQL...")
    conn = None
    last_err = None
    for uri in candidates:
        sanitized_uri = uri.split("@")[-1] if "@" in uri else uri
        print(f"  -> Attempting connection to: {sanitized_uri} ...")
        try:
            conn = psycopg2.connect(uri, connect_timeout=10)
            print("  -> Connected successfully!")
            break
        except Exception as e:
            last_err = e
            print(f"  -> Connection attempt failed: {e}")

    if not conn:
        print(f"\n[ERROR] Could not establish connection to Supabase database. Last error: {last_err}")
        print("Please verify your database password or execute the SQL via the Supabase Dashboard:")
        print(f"https://supabase.com/dashboard/project/{PROJECT_REF}/sql/new")
        return 1

    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            print("\n  -> Executing migration script...")
            cur.execute(sql_content)
            conn.commit()
            print("  -> Migration executed and committed successfully!")

            # Verify in Postgres
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'public' AND table_name = 'schedule'
                ORDER BY ordinal_position;
            """)
            cols = [f"{r[0]} ({r[1]})" for r in cur.fetchall()]
            print(f"\n  -> Updated schedule table columns:\n     {', '.join(cols)}")

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Migration failed and was rolled back: {e}")
        return 1
    finally:
        conn.close()

    # Final check via PostgREST
    print("\n[VERIFICATION] Verifying schema via Supabase API...")
    if check_schema_via_postgrest():
        print("  -> SUCCESS: 'schedule.prof_course_id' is live and recognized by Supabase API!")
    else:
        print("  -> Note: PostgREST schema cache may take a few seconds to reload.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
