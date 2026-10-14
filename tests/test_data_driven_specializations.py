"""Data-driven year-level and specialization behavior.

Covers the acceptance checklist:
  * year levels come from the courses that exist for the chosen semester
  * adding a 4th-year 2nd-semester course makes 4th year appear
  * specialized courses at ANY year level produce grouped sections
  * an all-General year level produces no specialization grouping
  * a year level with 2 specializations produces 2 groups (not 3)
  * a mismatched section count blocks generation with a named error
"""

import pytest
import app as app_module


SPECS = [
    {'specialization_id': 1, 'name': 'Database Systems', 'code': 'DST', 'archive': False},
    {'specialization_id': 2, 'name': 'Web Systems', 'code': 'WST', 'archive': False},
    {'specialization_id': 3, 'name': 'Networking', 'code': 'NST', 'archive': False},
]


class MockTable:
    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        if col == 'semester':
            return MockTable([d for d in self._data if d.get('semester') == val])
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        class Resp:
            def __init__(self, d):
                self.data = d
        return Resp(list(self._data))


class MockSupabase:
    def __init__(self, courses, loads):
        self.courses = courses
        self.loads = loads

    def table(self, name):
        if name == 'course':
            return MockTable(self.courses)
        if name == 'professor_load':
            return MockTable(self.loads)
        return MockTable([])


def _install(monkeypatch, courses, loads):
    monkeypatch.setattr(app_module, 'supabase', MockSupabase(courses, loads))
    monkeypatch.setattr(app_module, '_get_specializations', lambda include_archived=True: SPECS)


def _course(cid, code, year, semester, spec_id=None, general=False):
    return {
        'course_id': cid,
        'course_code': code,
        'year_level': year,
        'semester': semester,
        'specialization_id': None if general else spec_id,
        'program_id': 1,
    }


def _load(lid, cid, sections):
    return {'id': lid, 'prof_id': lid, 'course_id': cid, 'sections': sections,
            'professor_name': f'Prof {lid}'}


def test_year_levels_come_from_courses(monkeypatch):
    """Only the year levels that have courses for the semester appear."""
    courses = [
        _course(1, 'Y1', 1, '2nd Semester', general=True),
        _course(3, 'Y3', 3, '2nd Semester', general=True),
    ]
    loads = [_load(1, 1, 2), _load(2, 3, 2)]
    _install(monkeypatch, courses, loads)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert res['valid'] is True
    assert [b['year_level'] for b in res['breakdown']] == [1, 3]


def test_adding_4th_year_2nd_sem_course_makes_year_4_appear(monkeypatch):
    courses = [
        _course(1, 'Y1', 1, '2nd Semester', general=True),
        _course(3, 'Y3', 3, '2nd Semester', general=True),
    ]
    loads = [_load(1, 1, 2), _load(2, 3, 2)]
    _install(monkeypatch, courses, loads)
    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert 4 not in [b['year_level'] for b in res['breakdown']]

    # Data change only: add a 4th-year 2nd-semester course + load.
    courses.append(_course(4, 'Y4', 4, '2nd Semester', general=True))
    loads.append(_load(3, 4, 2))
    _install(monkeypatch, courses, loads)
    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert 4 in [b['year_level'] for b in res['breakdown']]
    assert res['valid'] is True


def test_specialized_2nd_year_groups_sections(monkeypatch):
    """Specialization grouping is not limited to 3rd/4th year."""
    courses = [
        _course(10, 'Y2-DB1', 2, '2nd Semester', spec_id=1),
        _course(20, 'Y2-DB2', 2, '2nd Semester', spec_id=1),
        _course(30, 'Y2-WEB', 2, '2nd Semester', spec_id=2),
    ]
    loads = [_load(1, 10, 2), _load(2, 20, 2), _load(3, 30, 2)]
    _install(monkeypatch, courses, loads)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert res['valid'] is True
    bd = res['breakdown'][0]
    assert bd['year_level'] == 2
    assert bd['is_specialized'] is True
    groups = {g['specialization']: g['section_count'] for g in bd['specialization_groups']}
    assert groups == {'Database Systems': 2, 'Web Systems': 2}
    assert '2A-Database Systems' in bd['all_section_names']
    assert '2A-Web Systems' in bd['all_section_names']


def test_all_general_year_level_has_no_specialization_grouping(monkeypatch):
    courses = [
        _course(1, 'Y1-A', 1, '1st Semester', general=True),
        _course(2, 'Y1-B', 1, '1st Semester', general=True),
    ]
    loads = [_load(1, 1, 3), _load(2, 2, 3)]
    _install(monkeypatch, courses, loads)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='1st Semester')
    assert res['valid'] is True
    bd = res['breakdown'][0]
    assert bd['is_specialized'] is False
    assert 'specialization_groups' not in bd
    assert bd['section_names'] == ['1A', '1B', '1C']


def test_year_level_with_two_specializations_gets_two_groups(monkeypatch):
    courses = [
        _course(10, 'Y3-DB', 3, '2nd Semester', spec_id=1),
        _course(20, 'Y3-NET', 3, '2nd Semester', spec_id=3),
    ]
    loads = [_load(1, 10, 2), _load(2, 20, 2)]
    _install(monkeypatch, courses, loads)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert res['valid'] is True
    bd = res['breakdown'][0]
    names = sorted(g['specialization'] for g in bd['specialization_groups'])
    assert names == ['Database Systems', 'Networking']


def test_mismatched_section_count_blocks_with_named_error(monkeypatch):
    """Internal mismatch inside one specialization group blocks with a named error."""
    courses = [
        _course(10, 'Y3-DB1', 3, '2nd Semester', spec_id=1),
        _course(11, 'Y3-DB2', 3, '2nd Semester', spec_id=1),
    ]
    # Same group (Database Systems) but 2 vs 3 sections -> mismatch.
    loads = [_load(1, 10, 2), _load(2, 11, 3)]
    _install(monkeypatch, courses, loads)

    res = app_module.calculate_semester_section_counts(program_id=1, semester='2nd Semester')
    assert res['valid'] is False
    assert any(
        '3rd Year (Database Systems): mismatched section counts' in e
        and 'Y3-DB1 has 2 sections' in e
        and 'Y3-DB2 has 3 sections' in e
        for e in res['errors']
    )


def test_specialization_is_allowed_at_any_year(monkeypatch):
    """Server-side validation accepts a specialization for a 1st-year course,
    with no stage/cutoff gate."""
    monkeypatch.setattr(app_module, '_get_specializations',
                        lambda include_archived=True: SPECS)
    spec_id, err = app_module._resolve_course_specialization_id('Web Systems', 1, '1st Semester')
    assert err is None
    assert spec_id == 2
