import pytest
from unittest.mock import MagicMock, patch
from app import app, get_school_year_options, calculate_semester_section_counts


def test_get_school_year_options():
    options = get_school_year_options()
    assert len(options) == 3
    for sy in options:
        parts = sy.split('-')
        assert len(parts) == 2
        y1, y2 = int(parts[0]), int(parts[1])
        assert y2 == y1 + 1


def test_generation_block_rule_same_scope():
    with app.test_request_context():
        with patch('app.supabase') as mock_sb, \
             patch('app._check_has_professor_loads', return_value=True):

            def fake_table(name):
                mock_t = MagicMock()
                mock_t.select.return_value = mock_t
                mock_t.eq.return_value = mock_t
                mock_t.is_.return_value = mock_t
                mock_t.in_.return_value = mock_t
                mock_t.or_.return_value = mock_t
                mock_t.limit.return_value = mock_t

                if name == 'course':
                    mock_t.execute.return_value.data = [
                        {'course_id': 1, 'course_code': 'IT101', 'year_level': 1, 'units': 3, 'specialization': None, 'program_id': 1}
                    ]
                elif name == 'professor_load':
                    mock_t.execute.return_value.data = [
                        {'id': 1, 'course_id': 1, 'sections': 2, 'professor_name': 'Dr. Smith'}
                    ]
                elif name == 'schedule':
                    mock_t.execute.return_value.data = [{
                        'schedule_id': 100,
                        'school_year': '2026-2027',
                        'is_used': False,
                        'semester': '1st Semester',
                        'program_id': 1,
                        'program': 'BSIT'
                    }]
                else:
                    mock_t.execute.return_value.data = []
                return mock_t

            mock_sb.table.side_effect = fake_table

            # 1. Same school year and same semester -> Blocked!
            res_same = calculate_semester_section_counts(program_id=1, semester='1st Semester', school_year='2026-2027')
            assert res_same['active_schedule']['same_scope'] is True
            assert any('Archive it first' in e for e in res_same['errors'])

            # 2. Different semester (2nd Semester) with same school year -> NOT blocked!
            res_diff_sem = calculate_semester_section_counts(program_id=1, semester='2nd Semester', school_year='2026-2027')
            assert res_diff_sem['active_schedule']['same_scope'] is False
            assert not any('Archive it first' in e for e in res_diff_sem['errors'])

            # 3. Different school year (2027-2028) with same semester -> NOT blocked!
            res_diff_sy = calculate_semester_section_counts(program_id=1, semester='1st Semester', school_year='2027-2028')
            assert res_diff_sy['active_schedule']['same_scope'] is False
            assert not any('Archive it first' in e for e in res_diff_sy['errors'])


def test_api_toggle_schedule_used():
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'testchair'
        sess['role'] = 'chair'
        sess['program_id'] = 1
        sess['program'] = 'BSIT'

    with patch('app.supabase') as mock_sb:
        mock_t = MagicMock()
        mock_t.select.return_value = mock_t
        mock_t.eq.return_value = mock_t
        mock_t.or_.return_value = mock_t
        mock_t.is_.return_value = mock_t
        mock_t.execute.return_value.data = [{
            'schedule_id': 1,
            'school_year': '2026-2027',
            'is_used': False,
            'professor_load': {'id': 1, 'course': {'course_id': 1, 'program_id': 1, 'semester': '1st Semester'}}
        }]
        mock_sb.table.return_value = mock_t

        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = {'success': True, 'is_used': True, 'batch_id': 'batch-uuid-123'}
        mock_sb.rpc.return_value = mock_rpc

        resp = client.post('/api/schedule/toggle_used', json={
            'batch_id': 'batch-uuid-123',
            'is_used': True
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert data['is_used'] is True
        assert data['school_year'] == '2026-2027'
        assert data['semester'] == '1st Semester'
