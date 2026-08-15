import app as app_module
import pytest
import logging


def test_get_department_success(monkeypatch):
    class FakeQuery:
        def __init__(self, data):
            self._data = data

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def single(self):
            return self

        def execute(self):
            class Response:
                def __init__(self, data):
                    self.data = data
            return Response(self._data)

    class FakeTable:
        def table(self, name):
            if name == 'program_department':
                return FakeQuery({'department_name': 'CICT'})
            return FakeQuery(None)

    monkeypatch.setattr(app_module, 'supabase', FakeTable())

    with app_module.app.test_request_context():
        app_module.session['program'] = 'BSIT'
        dept = app_module._get_department()
        assert dept == 'CICT'


def test_get_department_fallback_when_not_found(monkeypatch, caplog):
    class FakeQuery:
        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def single(self):
            return self

        def execute(self):
            class Response:
                data = None
            return Response()

    class FakeTable:
        def table(self, name):
            return FakeQuery()

    monkeypatch.setattr(app_module, 'supabase', FakeTable())

    with app_module.app.test_request_context():
        app_module.session['program'] = 'UNKNOWN_PROG'
        with caplog.at_level(logging.ERROR):
            dept = app_module._get_department()
            assert dept is None
            assert "Program 'UNKNOWN_PROG' does not exist in program_department table." in caplog.text


def test_get_department_fallback_from_user_table(monkeypatch):
    class FakeAuth:
        def get_user(self, token):
            class UserWrapper:
                user_metadata = {'program': 'BSBA'}
            class AuthResponse:
                user = UserWrapper()
            return AuthResponse()

    class FakeTable:
        auth = FakeAuth()
        def table(self, name):
            class Query:
                def __init__(self, tname):
                    self.tname = tname

                def select(self, *args, **kwargs):
                    return self

                def eq(self, *args, **kwargs):
                    return self

                def single(self):
                    return self

                def execute(self):
                    class Response:
                        pass
                    res = Response()
                    if self.tname == 'users':
                        res.data = {'program': 'BSBA'}
                    elif self.tname == 'program_department':
                        res.data = {'department_name': 'CMBT'}
                    else:
                        res.data = None
                    return res

            return Query(name)

    monkeypatch.setattr(app_module, 'supabase', FakeTable())

    with app_module.app.test_request_context():
        app_module.session['user_id'] = 42
        app_module.session['program'] = None
        dept = app_module._get_department()
        assert dept == 'CMBT'
        assert app_module.session['program'] == 'BSBA'
