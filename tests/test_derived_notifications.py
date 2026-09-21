import pytest
from unittest.mock import MagicMock, patch
import app as app_module


def test_backup_tables_exclude_scheduler_notifications():
    """Ensure scheduler_notifications is completely removed from backup definitions."""
    assert 'scheduler_notifications' not in app_module.BACKUP_TABLES_INSERT_ORDER
    assert 'scheduler_notifications' not in app_module.BACKUP_PKS


def test_get_request_context_derives_notifications(monkeypatch):
    """Ensure notifications are derived correctly from delete_requests."""
    sample_requests = [
        {
            'id': 101,
            'user_id': 'user-123',
            'item_details': 'Course CS101',
            'status': 'approved',
            'is_read': False,
            'created_at': '2026-09-21T10:00:00Z',
            'updated_at': '2026-09-21T10:05:00Z'
        },
        {
            'id': 102,
            'user_id': 'user-123',
            'item_details': 'Room 304',
            'status': 'rejected',
            'is_read': True,
            'created_at': '2026-09-21T09:00:00Z',
            'updated_at': '2026-09-21T09:10:00Z'
        }
    ]

    mock_table = MagicMock()
    # Mock count query
    mock_count_res = MagicMock()
    mock_count_res.count = 1
    # Mock select query
    mock_select_res = MagicMock()
    mock_select_res.data = sample_requests

    query_mock = MagicMock()
    query_mock.select.return_value = query_mock
    query_mock.eq.return_value = query_mock
    query_mock.in_.return_value = query_mock
    query_mock.order.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.execute.side_effect = [mock_count_res, mock_select_res]

    mock_supabase = MagicMock()
    mock_supabase.table.return_value = query_mock
    monkeypatch.setattr(app_module, 'supabase', mock_supabase)

    app = app_module.app
    with app.test_request_context():
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 'user-123'
                sess['role'] = 'Scheduler'

            with app.test_request_context():
                with client.session_transaction() as sess:
                    pass
                from flask import session
                session['user_id'] = 'user-123'
                session['role'] = 'Scheduler'
                ctx = app_module.inject_pending_requests()
                assert ctx['unread_notifications_count'] == 1
                notifs = ctx['scheduler_notifications']
                assert len(notifs) == 2
                assert notifs[0]['message'] == 'Admin approved the deletion of Course CS101.'
                assert notifs[0]['status'] == 'approved'
                assert notifs[0]['is_read'] is False
                assert notifs[1]['message'] == 'Admin rejected the deletion of Room 304.'
                assert notifs[1]['status'] == 'rejected'
                assert notifs[1]['is_read'] is True


def test_mark_notifications_read_updates_delete_requests(monkeypatch):
    """Ensure /notifications/mark_read marks delete_requests as read."""
    mock_table = MagicMock()
    mock_query = MagicMock()
    mock_query.update.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.in_.return_value = mock_query
    mock_query.execute.return_value = MagicMock()
    mock_table.return_value = mock_query

    mock_supabase = MagicMock()
    mock_supabase.table = mock_table
    monkeypatch.setattr(app_module, 'supabase', mock_supabase)

    app = app_module.app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user_id'] = 'user-123'
            sess['role'] = 'Scheduler'

        res = client.post('/notifications/mark_read')
        assert res.status_code == 200
        json_data = res.get_json()
        assert json_data['success'] is True

        mock_supabase.table.assert_called_with('delete_requests')
        mock_query.update.assert_called_with({'is_read': True})


def test_set_delete_request_status_helper(monkeypatch):
    """Ensure _set_delete_request_status updates delete_requests correctly."""
    mock_query = MagicMock()
    mock_query.update.return_value = mock_query
    mock_query.eq.return_value = mock_query
    mock_query.execute.return_value = MagicMock()

    mock_supabase = MagicMock()
    mock_supabase.table.return_value = mock_query
    monkeypatch.setattr(app_module, 'supabase', mock_supabase)

    app_module._set_delete_request_status({'id': 50}, 'approved')
    mock_supabase.table.assert_called_with('delete_requests')
    call_args = mock_query.update.call_args[0][0]
    assert call_args['status'] == 'approved'
    assert call_args['is_read'] is False
    assert 'updated_at' in call_args
