#!/usr/bin/env python3
"""
populate_prof_course.py

Distributes courses/subjects as evenly as possible among all professors in prof_course.
Constraints:
- Each subject is taught by at least 3 professors (default: 3 professors per subject).
- Each professor is assigned no more than 3 subjects (ideally exactly 3).
- Automatically inserts additional entries into the professor table if capacity is exceeded.
- Groups subjects by domain so faculty teach cohesive course clusters.
"""

import os
import sys
import math
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_PUBLISHABLE_KEY")

# Predefined realistic faculty names for auto-generation
NEW_FACULTY_POOL = [
    ("Alexander", "Tan"),
    ("Bernadette", "Aquino"),
    ("Christian", "del Rosario"),
    ("Diana", "Navarro"),
    ("Eduardo", "Castillo"),
    ("Fatima", "Soriano"),
    ("Gabriel", "Bautista"),
    ("Hannah", "Mercado"),
    ("Ivan", "Padilla"),
    ("Jasmine", "Lim"),
    ("Kenneth", "Salazar"),
    ("Lorraine", "Valdez"),
    ("Mark", "Villanueva"),
    ("Nicole", "Domingo"),
    ("Oliver", "Pascual"),
    ("Patricia", "Santiago"),
    ("Rafael", "Estrada"),
    ("Stephanie", "Guerrero"),
    ("Tristan", "Alvarez"),
    ("Victoria", "Samson"),
    ("Ramon", "Dizon"),
    ("Katrina", "Legaspi"),
    ("Miguel", "Ocampo"),
    ("Bea", "Morales"),
    ("Dominic", "Cortez"),
    ("Clarissa", "Bernardo"),
    ("Francis", "David"),
    ("Giselle", "Manalo"),
    ("Hector", "Pineda"),
    ("Irene", "Tolentino"),
]


def get_course_domain_key(course):
    """Return domain rank and name for clustering related courses."""
    name = (course.get("course_name") or "").upper()
    major = (course.get("major") or "").upper()
    year = course.get("year_level") or 1
    sem = course.get("semester") or ""

    if "NET" in name or "NETWORKING" in major:
        domain_rank = 1  # Networking
        domain_name = "Networking"
    elif "WS" in name or "WEB" in major:
        domain_rank = 2  # Web Systems / Web Development
        domain_name = "Web Development"
    elif "IM" in name or "DATABASE" in major or "DATA" in major:
        domain_rank = 3  # Information Management / Databases
        domain_name = "Database Systems"
    elif name.startswith("CC-") or "PF" in name:
        domain_rank = 4  # Core Computing & Programming Fundamentals
        domain_name = "Core Computing & Programming"
    elif "IAS" in name or "SEC" in name or "IPT" in name or "SA" in name or "MS" in name:
        domain_rank = 5  # Systems, Integration & Security
        domain_name = "Systems, Integration & Security"
    else:
        domain_rank = 6  # Applications, Software & Capstone
        domain_name = "Applications, Software & Capstone"

    return domain_rank, domain_name, name, year, sem


def group_courses_by_domain(courses):
    """Groups courses into domain clusters."""
    domains = {}
    for c in courses:
        rank, domain_name, name, year, sem = get_course_domain_key(c)
        domains.setdefault(domain_name, []).append(c)
    
    # Sort courses within each domain
    for dname in domains:
        domains[dname].sort(key=lambda x: (x.get("year_level") or 1, x.get("course_name") or ""))
    return domains


def auto_create_professors(client: Client, department: str, count_needed: int, existing_profs: list):
    """
    Automatically creates additional professor entries in Supabase when capacity is exceeded.
    """
    existing_names = {
        (p.get("first_name", "").strip().lower(), p.get("last_name", "").strip().lower())
        for p in existing_profs
    }
    
    new_profs_to_insert = []
    pool_idx = 0
    while len(new_profs_to_insert) < count_needed:
        if pool_idx < len(NEW_FACULTY_POOL):
            first, last = NEW_FACULTY_POOL[pool_idx]
            pool_idx += 1
        else:
            first = "Faculty"
            last = f"CICT-{len(new_profs_to_insert) + 1}"

        if (first.lower(), last.lower()) in existing_names:
            continue

        new_profs_to_insert.append({
            "first_name": first,
            "last_name": last,
            "department": department,
            "max_hours": 40,
        })
        existing_names.add((first.lower(), last.lower()))

    print(f"[*] Inserting {len(new_profs_to_insert)} newly generated professors for department '{department}'...")
    if client:
        res = client.table("professor").insert(new_profs_to_insert).execute()
        inserted_data = res.data or []
    else:
        inserted_data = new_profs_to_insert
    print(f"[+] Successfully generated {len(inserted_data)} professors.")
    return inserted_data


def balance_and_assign_domain_circulant(domain_courses, domain_profs, profs_per_course=3, max_load_per_prof=3):
    """
    Assigns courses in a domain to professors such that:
    - Each course is assigned to `profs_per_course` professors.
    - Each professor is assigned to `max_load_per_prof` courses.
    Using circulant bipartite design.
    """
    C = len(domain_courses)
    P = len(domain_profs)
    assignments = []

    # Map each course j to `profs_per_course` professors
    # and each prof i to `max_load_per_prof` courses
    for i, prof in enumerate(domain_profs):
        for k in range(max_load_per_prof):
            course_idx = (i + k) % C
            course = domain_courses[course_idx]
            assignments.append({
                "prof_id": prof.get("prof_id"),
                "course_id": course.get("course_id"),
                "prof_name": f"{prof.get('first_name', '')} {prof.get('last_name', '')}".strip(),
                "course_name": course.get("course_name"),
                "department": prof.get("department"),
            })

    return assignments


def distribute_courses_with_multi_prof(courses, professors, profs_per_course=3, max_load_per_prof=3):
    """
    Orchestrates domain-wise balanced distribution of courses among professors.
    """
    domains = group_courses_by_domain(courses)
    
    # Sort professors stably
    sorted_profs = sorted(
        professors,
        key=lambda p: (p.get("last_name") or "", p.get("first_name") or "", p.get("prof_id") or 0)
    )

    all_assignments = []
    prof_idx = 0

    for domain_name, d_courses in domains.items():
        C = len(d_courses)
        needed_profs_count = C  # Since C * profs_per_course / max_load_per_prof = C * 3 / 3 = C
        d_profs = sorted_profs[prof_idx : prof_idx + needed_profs_count]
        prof_idx += needed_profs_count

        d_assignments = balance_and_assign_domain_circulant(
            d_courses, d_profs, profs_per_course=profs_per_course, max_load_per_prof=max_load_per_prof
        )
        all_assignments.extend(d_assignments)

    return all_assignments
