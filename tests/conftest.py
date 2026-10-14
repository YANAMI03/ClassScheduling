"""Shared pytest fixtures.

The RBAC layer keeps two process-global, in-memory caches (``_LIVE_USER_ROLE_UPDATES``
and ``_USER_ROLE_CACHE``) so an admin's role change can reach an already-open session
without a DB round-trip. Because they are module globals, they would otherwise leak
between tests (a fake DB returning a different role in one test would pin it for the
next). Reset them before every test so each test starts from a clean, DB-authoritative
state.
"""
import pytest

import app as app_module


@pytest.fixture(autouse=True)
def _reset_rbac_caches():
    app_module._LIVE_USER_ROLE_UPDATES.clear()
    with app_module._USER_ROLE_CACHE_LOCK:
        app_module._USER_ROLE_CACHE.clear()
    yield
    app_module._LIVE_USER_ROLE_UPDATES.clear()
    with app_module._USER_ROLE_CACHE_LOCK:
        app_module._USER_ROLE_CACHE.clear()
