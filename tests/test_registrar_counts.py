import importlib
import math

app_module = importlib.import_module('app')


def test_generate_mock_registrar_data_structure_and_proportional_scaling():
    data = app_module._generate_mock_registrar_data()

    assert 'years' in data
    assert len(data['years']) == 4
    for y_str in ('1', '2', '3', '4'):
        assert y_str in data['years']
        y_info = data['years'][y_str]
        students = y_info['student_count']
        sections = y_info['section_count']
        assert 300 <= students <= 400
        assert sections == math.ceil(students / 30)

    assert data['total_students'] == sum(v['student_count'] for v in data['years'].values())
    assert data['total_sections'] == sum(v['section_count'] for v in data['years'].values())


def test_proportional_scaling_formula_examples():
    # Formula rule: math.ceil(students / 30)
    assert math.ceil(300 / 30) == 10
    assert math.ceil(350 / 30) == 12
    assert math.ceil(400 / 30) == 14


def test_get_registrar_counts_for_year_specific_and_all():
    y1 = app_module._get_registrar_counts_for_year('1')
    assert 'student_count' in y1
    assert 'section_count' in y1
    assert 300 <= y1['student_count'] <= 400
    assert y1['section_count'] == math.ceil(y1['student_count'] / 30)

    all_data = app_module._get_registrar_counts_for_year('all')
    assert 'total_students' in all_data or 'student_count' in all_data
    assert 'years' in all_data


def test_generate_mock_registrar_data_second_semester_3rd_year_majors():
    data = app_module._generate_mock_registrar_data(semester='2nd Semester')

    assert 'years' in data
    assert '3' in data['years']
    y3 = data['years']['3']
    assert 'majors' in y3
    assert 'database' in y3['majors']
    assert 'web' in y3['majors']
    assert 'networking' in y3['majors']

    db_cnt = y3['majors']['database']
    web_cnt = y3['majors']['web']
    net_cnt = y3['majors']['networking']
    assert db_cnt >= 1
    assert web_cnt >= 1
    assert net_cnt >= 1
    assert db_cnt + web_cnt + net_cnt == y3['section_count']

    # 4th year is 0 in 2nd semester
    assert data['years']['4']['student_count'] == 0
    assert data['years']['4']['section_count'] == 0


