#!/usr/bin/env python3
"""List DISTINCT program/department text values before introducing program_id FKs.

Does not change any schema. Prefers a direct Postgres connection so RLS cannot
hide rows. REST fallback (anon key) will usually return 0 rows.

Usage:
  python scripts/report_program_values.py
  python scripts/report_program_values.py --db-password "..."
  python scripts/report_program_values.py --conn-str "postgresql://..."
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

PROJECT_REF = "maaeqnmziwhocziwgjpo"

SOURCES = [
    ("professor", "department"),
    ("room", "department"),
    ("course", "program"),
    ("users", "program"),
    ("schedule", "program"),
    ("academic_ranking", "program"),
]


def parse_args():
    parser = argparse.ArgumentParser(description="Report distinct program text values.")
    parser.add_argument("--db-password", "-p")
    parser.add_argument("--conn-str", "-c")
    return parser.parse_args()


def connection_candidates(password=None, conn_str=None):
    if conn_str:
        return [conn_str]
    env_conn = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("SUPABASE_DB_URL")
    )
    if env_conn:
        return [env_conn]
    pwd = (
        password
        or os.environ.get("SUPABASE_DB_PASSWORD")
        or os.environ.get("POSTGRES_PASSWORD")
        or os.environ.get("DB_PASSWORD")
    )
    if not pwd:
        return []
    return [
        f"postgresql://postgres:{pwd}@db.{PROJECT_REF}.supabase.co:5432/postgres?sslmode=require",
        f"postgresql://postgres.{PROJECT_REF}:{pwd}@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require",
        f"postgresql://postgres.{PROJECT_REF}:{pwd}@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require",
    ]


def report_via_postgres(conn) -> None:
    with conn.cursor() as cur:
        print("SOURCE: Postgres (bypasses PostgREST RLS)\n")
        all_values = defaultdict(list)
        for table, column in SOURCES:
            cur.execute(
                f"""
                SELECT {column} AS value, COUNT(*) AS n
                FROM public.{table}
                GROUP BY {column}
                ORDER BY n DESC, value NULLS FIRST
                """
            )
            rows = cur.fetchall()
            print("=" * 64)
            print(f"{table}.{column}")
            if not rows:
                print("  (table empty)")
                continue
            for value, n in rows:
                label = "<NULL>" if value is None else repr(value)
                print(f"  count={n:5d}  {label}")
                if value is not None and str(value).strip() != "":
                    all_values[str(value)].append(f"{table}.{column} ({n})")

        print("\n" + "=" * 64)
        print("CROSS-TABLE UNION (non-empty strings)")
        for value in sorted(all_values, key=lambda v: v.lower()):
            print(f"  {value!r}")
            for src in all_values[value]:
                print(f"      {src}")

        print("\n" + "=" * 64)
        print("users.role distribution")
        cur.execute(
            """
            SELECT role, COUNT(*) AS n
            FROM public.users
            GROUP BY role
            ORDER BY n DESC, role NULLS FIRST
            """
        )
        for value, n in cur.fetchall():
            print(f"  count={n:5d}  {value!r}")

        print("\nusers.role IN ('instructor', 'Instructor', 'Viewer', 'viewer')")
        cur.execute(
            """
            SELECT id, email, username, first_name, last_name, program, role
            FROM public.users
            WHERE lower(coalesce(role, '')) IN ('instructor', 'viewer')
            ORDER BY lower(role), email NULLS LAST, username NULLS LAST
            """
        )
        flagged = cur.fetchall()
        print(f"  flagged rows: {len(flagged)}")
        for row in flagged:
            print(f"  {row}")

        print("\nprogram_department (legacy mapping table, if present)")
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'program_department'
            )
            """
        )
        if cur.fetchone()[0]:
            cur.execute("SELECT program_name, department_name FROM public.program_department ORDER BY 1, 2")
            rows = cur.fetchall()
            if not rows:
                print("  table exists but is empty")
            for program_name, department_name in rows:
                print(f"  program_name={program_name!r}  department_name={department_name!r}")
        else:
            print("  table does not exist")


def report_via_rest() -> None:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        print("REST fallback skipped: SUPABASE_URL / anon key missing.")
        return
    sb = create_client(url, key)
    print("SOURCE: PostgREST with anon key (RLS applies; empty results are not authoritative)\n")
    for table, column in SOURCES:
        print("=" * 64)
        print(f"{table}.{column}")
        try:
            cols = f"{column},role,id,email,username,first_name,last_name" if table == "users" else column
            resp = sb.table(table).select(cols).limit(1000).execute()
            rows = resp.data or []
            print(f"  visible rows: {len(rows)}")
            counts = Counter()
            for row in rows:
                counts[row.get(column)] += 1
            for value, n in sorted(counts.items(), key=lambda item: (-item[1], str(item[0]))):
                print(f"  count={n:5d}  {value!r}")
            if table == "users":
                instructors = [
                    row
                    for row in rows
                    if str(row.get("role") or "").lower() in ("instructor", "viewer")
                ]
                print(f"  flagged instructor/viewer rows visible: {len(instructors)}")
                for row in instructors:
                    print(
                        "   ",
                        {
                            k: row.get(k)
                            for k in (
                                "id",
                                "email",
                                "username",
                                "first_name",
                                "last_name",
                                "program",
                                "role",
                            )
                        },
                    )
        except Exception as exc:
            print(f"  ERROR: {exc}")


def main() -> int:
    args = parse_args()
    candidates = connection_candidates(args.db_password, args.conn_str)
    if candidates:
        try:
            import psycopg2
        except ImportError:
            print("psycopg2 is not installed; falling back to REST.")
            report_via_rest()
            return 0
        last_err = None
        for uri in candidates:
            host = uri.split("@")[-1] if "@" in uri else uri
            print(f"Connecting to {host} ...")
            try:
                conn = psycopg2.connect(uri, connect_timeout=10)
                try:
                    report_via_postgres(conn)
                finally:
                    conn.close()
                return 0
            except Exception as exc:
                last_err = exc
                print(f"  failed: {exc}")
        print(f"Postgres connection failed ({last_err}); falling back to REST.\n")
    else:
        print("No DATABASE_URL / SUPABASE_DB_PASSWORD found; REST fallback only.\n")
    report_via_rest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
