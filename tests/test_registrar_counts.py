import pytest
import app as app_module

class MockQuery:
    def __init__(self, data):
        self._data = data
        self._filters = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        filtered = []
        for row in self._data:
            match = True
            for k, v in self._filters.items():
                if str(row.get(k)) != str(v):
                    match = False
                    break
            if match:
                filtered.append(row)
        class Resp:
            data = filtered
        return Resp()

class MockSupabase:
    def __init__(self, courses, loads, schedules=None):
        self.courses = courses
        self.loads = loads
        self.schedules = schedules or []

    def table(self, table_name):
        if table_name == 'course':
            return MockQuery(self.courses)
        elif table_name == 'professor_load':
            return MockQuery(self.loads)
        elif table_name == 'schedule':
            return MockQuery(self.schedules)
        return MockQuery([])


def test_multiple_professors_sum_sections_per_course(monkeypatch):
    """Change 2: total_sections = SUM(sections) across professor_load rows (Prof A 2 + Prof B 1 = 3)."""
    courses = [
        {'course_id': 101, 'course_code': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 102, 'course_code': 'CC-102', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
    ]
    # Course 101: Prof 1 has 2 sections, Prof 2 has 1 section => total = 3
    # Course 102: Prof 3 has 3 sections => total = 3
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 101, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 101, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 102, 'sections': 3},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads))

    result = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert result['valid'] is True
    assert len(result['errors']) == 0
    bd = result['breakdown'][0]
    assert bd['year_level'] == 1
    assert bd['section_count'] == 3
    assert bd['section_names'] == ['1A', '1B', '1C']


def test_course_with_no_load_blocks_generation(monkeypatch):
    """Change 2: A course with no professor_load rows counts as a mismatch (0 sections) and blocks generation."""
    courses = [
        {'course_id': 101, 'course_code': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 102, 'course_code': 'CC-102', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
    ]
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 101, 'sections': 2},
        # Course 102 has NO professor_load rows
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads))

    result = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert result['valid'] is False
    assert any('CC-102 has no assigned load' in err for err in result['errors'])


def test_mismatched_section_counts_blocks_generation(monkeypatch):
    """Change 2: All courses in same year level + semester must have same section count."""
    courses = [
        {'course_id': 101, 'course_code': 'CC-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
        {'course_id': 102, 'course_code': 'MATH-101', 'year_level': 1, 'semester': '1st Semester', 'program_id': 1},
    ]
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 101, 'sections': 3},
        {'id': 2, 'prof_id': 2, 'course_id': 102, 'sections': 4},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads))

    result = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert result['valid'] is False
    assert any('mismatched section counts' in err for err in result['errors'])
    assert any('CC-101 has 3 sections' in err for err in result['errors'])
    assert any('MATH-101 has 4 sections' in err for err in result['errors'])


def test_specialization_groups_and_naming(monkeypatch):
    """Change 3: Specialized courses group and name sections per specialization (e.g. 3A-Networking)."""
    # 3rd year 2nd sem is specialized
    courses = [
        {'course_id': 301, 'course_code': 'IT-DB1', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 302, 'course_code': 'IT-WEB1', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 303, 'course_code': 'IT-NET1', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
    ]
    loads = [
        {'id': 1, 'prof_id': 1, 'course_id': 301, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 302, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 303, 'sections': 1},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads))

    result = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert result['valid'] is True
    bd = result['breakdown'][0]
    assert bd['is_specialized'] is True
    grp_map = {g['specialization']: g for g in bd['specialization_groups']}
    assert grp_map['Database Systems']['section_count'] == 2
    assert grp_map['Database Systems']['section_names'] == ['3A-Database Systems', '3B-Database Systems']
    assert grp_map['Web Systems']['section_count'] == 1
    assert grp_map['Web Systems']['section_names'] == ['3A-Web Systems']
    assert grp_map['Networking']['section_count'] == 1
    assert grp_map['Networking']['section_names'] == ['3A-Networking']


def test_general_courses_must_match_sum_of_specialization_groups(monkeypatch):
    """Change 3: General course total_sections must equal sum of section counts across all specialization groups."""
    # Database (2) + Web (1) + Networking (1) = 4 required for General
    courses = [
        {'course_id': 301, 'course_code': 'IT-DB1', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Database Systems', 'program_id': 1},
        {'course_id': 302, 'course_code': 'IT-WEB1', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Web Systems', 'program_id': 1},
        {'course_id': 303, 'course_code': 'IT-NET1', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'Networking', 'program_id': 1},
        {'course_id': 304, 'course_code': 'GE-ETHICS', 'year_level': 3, 'semester': '2nd Semester', 'specialization': 'General', 'program_id': 1},
    ]
    # Case A: GE-ETHICS has 3 sections (mismatch: 3 != 4) -> should block
    loads_mismatch = [
        {'id': 1, 'prof_id': 1, 'course_id': 301, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 302, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 303, 'sections': 1},
        {'id': 4, 'prof_id': 4, 'course_id': 304, 'sections': 3},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads_mismatch))

    result = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert result['valid'] is False
    assert any('requires 4 sections' in err for err in result['errors'])

    # Case B: GE-ETHICS has 4 sections (matches 2 + 1 + 1 = 4) -> should pass
    loads_match = [
        {'id': 1, 'prof_id': 1, 'course_id': 301, 'sections': 2},
        {'id': 2, 'prof_id': 2, 'course_id': 302, 'sections': 1},
        {'id': 3, 'prof_id': 3, 'course_id': 303, 'sections': 1},
        {'id': 4, 'prof_id': 4, 'course_id': 304, 'sections': 4},
    ]
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads_match))

    result2 = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert result2['valid'] is True
    assert len(result2['errors']) == 0
