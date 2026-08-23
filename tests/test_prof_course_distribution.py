import math
import pytest
from scripts.populate_prof_course import (
    distribute_courses_with_multi_prof,
    group_courses_by_domain,
    get_course_domain_key,
    auto_create_professors,
    balance_and_assign_domain_circulant,
)


def test_circulant_distribution_exact_counts():
    # 6 courses in domain, 6 profs -> each prof gets 3, each course gets 3
    domain_courses = [{"course_id": i, "course_name": f"IM-{i}"} for i in range(1, 7)]
    domain_profs = [{"prof_id": i, "first_name": f"P{i}", "last_name": f"L{i}"} for i in range(1, 7)]

    assignments = balance_and_assign_domain_circulant(domain_courses, domain_profs, profs_per_course=3, max_load_per_prof=3)
    
    assert len(assignments) == 18

    # Each course has exactly 3 professors
    courses_count = {}
    for a in assignments:
        courses_count[a["course_id"]] = courses_count.get(a["course_id"], 0) + 1
    assert all(cnt == 3 for cnt in courses_count.values())
    assert len(courses_count) == 6

    # Each prof has exactly 3 courses
    profs_count = {}
    for a in assignments:
        profs_count[a["prof_id"]] = profs_count.get(a["prof_id"], 0) + 1
    assert all(cnt == 3 for cnt in profs_count.values())
    assert len(profs_count) == 6


def test_distribute_courses_with_multi_prof_all_42_courses():
    # 42 mock courses across all domains
    courses = [
        {"course_id": 1, "course_name": "CC-100", "major": None, "year_level": 1, "semester": "1st"},
        {"course_id": 2, "course_name": "CC-101", "major": None, "year_level": 1, "semester": "1st"},
        {"course_id": 3, "course_name": "IT-NET01", "major": "Networking", "year_level": 1, "semester": "1st"},
        {"course_id": 10, "course_name": "CC-102", "major": None, "year_level": 1, "semester": "2nd"},
        {"course_id": 11, "course_name": "IT-NET02", "major": "Networking", "year_level": 1, "semester": "2nd"},
        {"course_id": 12, "course_name": "IT-WS01", "major": "Web Development", "year_level": 1, "semester": "2nd"},
        {"course_id": 18, "course_name": "CC-103", "major": None, "year_level": 2, "semester": "1st"},
        {"course_id": 19, "course_name": "IT-PF01", "major": None, "year_level": 2, "semester": "1st"},
        {"course_id": 20, "course_name": "IT-WS02", "major": "Web Development", "year_level": 2, "semester": "1st"},
        {"course_id": 21, "course_name": "IT-MS01", "major": None, "year_level": 2, "semester": "1st"},
        {"course_id": 26, "course_name": "CC-104", "major": None, "year_level": 2, "semester": "2nd"},
        {"course_id": 27, "course_name": "CC-105", "major": None, "year_level": 2, "semester": "2nd"},
        {"course_id": 28, "course_name": "IT-PF02", "major": None, "year_level": 2, "semester": "2nd"},
        {"course_id": 29, "course_name": "IT-HCI01", "major": None, "year_level": 2, "semester": "2nd"},
        {"course_id": 30, "course_name": "IT-MS02", "major": None, "year_level": 2, "semester": "2nd"},
        {"course_id": 44, "course_name": "IT-IM01", "major": "Database Systems", "year_level": 3, "semester": "1st"},
        {"course_id": 45, "course_name": "IT-IPT01", "major": None, "year_level": 3, "semester": "1st"},
        {"course_id": 46, "course_name": "IT-SA01", "major": None, "year_level": 3, "semester": "1st"},
        {"course_id": 47, "course_name": "IT-IAS01", "major": None, "year_level": 3, "semester": "1st"},
        {"course_id": 48, "course_name": "IT-WS02", "major": "Web", "year_level": 3, "semester": "2nd"},
        {"course_id": 50, "course_name": "IT-IAS02", "major": "General", "year_level": 3, "semester": "2nd"},
        {"course_id": 51, "course_name": "IT-CAP01", "major": "General", "year_level": 3, "semester": "2nd"},
        {"course_id": 52, "course_name": "IT-WS03", "major": "Web Development", "year_level": 3, "semester": "2nd"},
        {"course_id": 53, "course_name": "IT-WS04", "major": "Web Development", "year_level": 3, "semester": "2nd"},
        {"course_id": 54, "course_name": "IT-WS05", "major": "Web Development", "year_level": 3, "semester": "2nd"},
        {"course_id": 55, "course_name": "IT-IM02", "major": "Database Systems", "year_level": 3, "semester": "2nd"},
        {"course_id": 56, "course_name": "IT-IM03", "major": "Database Systems", "year_level": 3, "semester": "2nd"},
        {"course_id": 57, "course_name": "IT-IM04", "major": "Database Systems", "year_level": 3, "semester": "2nd"},
        {"course_id": 58, "course_name": "IT-NET03", "major": "Networking", "year_level": 3, "semester": "2nd"},
        {"course_id": 59, "course_name": "IT-NET04", "major": "Networking", "year_level": 3, "semester": "2nd"},
        {"course_id": 60, "course_name": "IT-NET05", "major": "Networking", "year_level": 3, "semester": "2nd"},
        {"course_id": 61, "course_name": "IT-SIA01", "major": "General", "year_level": 4, "semester": "1st"},
        {"course_id": 62, "course_name": "IT-SP01", "major": "General", "year_level": 4, "semester": "1st"},
        {"course_id": 63, "course_name": "IT-CAP02", "major": "General", "year_level": 4, "semester": "1st"},
        {"course_id": 64, "course_name": "IT-SW01", "major": "General", "year_level": 4, "semester": "1st"},
        {"course_id": 65, "course_name": "IT-WS06", "major": "Web Development", "year_level": 4, "semester": "1st"},
        {"course_id": 66, "course_name": "IT-WS07", "major": "Web Development", "year_level": 4, "semester": "1st"},
        {"course_id": 67, "course_name": "IT-IM05", "major": "Database Systems", "year_level": 4, "semester": "1st"},
        {"course_id": 68, "course_name": "IT-IM06", "major": "Database Systems", "year_level": 4, "semester": "1st"},
        {"course_id": 69, "course_name": "IT-NET06", "major": "Networking", "year_level": 4, "semester": "1st"},
        {"course_id": 70, "course_name": "IT-NET07", "major": "Networking", "year_level": 4, "semester": "1st"},
        {"course_id": 72, "course_name": "IT-IPT02", "major": None, "year_level": 3, "semester": "1st"},
    ]
    
    # 42 professors
    profs = [
        {"prof_id": i, "first_name": f"First{i}", "last_name": f"Last{i}", "department": "CICT"}
        for i in range(1, 43)
    ]

    assignments = distribute_courses_with_multi_prof(courses, profs, profs_per_course=3, max_load_per_prof=3)
    
    # Total assignments = 42 * 3 = 126
    assert len(assignments) == 126

    # Every course has exactly 3 professors
    courses_count = {}
    for a in assignments:
        courses_count[a["course_id"]] = courses_count.get(a["course_id"], 0) + 1
    assert len(courses_count) == 42
    assert all(cnt == 3 for cnt in courses_count.values())

    # Every prof has exactly 3 courses
    profs_count = {}
    for a in assignments:
        profs_count[a["prof_id"]] = profs_count.get(a["prof_id"], 0) + 1
    assert len(profs_count) == 42
    assert all(cnt == 3 for cnt in profs_count.values())


def test_auto_create_professors_mock():
    class MockTable:
        def __init__(self):
            self.inserted = []

        def insert(self, rows):
            self.inserted.extend(rows)
            return self

        def execute(self):
            class Res:
                def __init__(self, data):
                    self.data = [{"prof_id": idx + 100, **r} for idx, r in enumerate(data)]
            return Res(self.inserted)

    class MockClient:
        def __init__(self):
            self.table_mock = MockTable()

        def table(self, name):
            if name == "professor":
                return self.table_mock
            raise ValueError(f"Unknown table {name}")

    mock_client = MockClient()
    existing_profs = [
        {"prof_id": 1, "first_name": "Alexander", "last_name": "Tan", "department": "CICT"}
    ]
    created = auto_create_professors(mock_client, "CICT", 24, existing_profs)
    assert len(created) == 24
    for p in created:
        assert p["department"] == "CICT"
        assert p["max_hours"] == 40
        assert p["prof_id"] >= 100
