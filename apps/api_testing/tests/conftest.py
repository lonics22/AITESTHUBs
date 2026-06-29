# -*- coding: utf-8 -*-
"""Pytest configuration for api_testing tests.

NOTE: For view tests requiring database access, run with:
    python manage.py test apps.api_testing.tests.test_ai_import_views

This conftest only configures Django for pytest-based tests that don't
require database access (e.g., unit tests for pure logic).
"""
import os
from django.conf import settings


def pytest_configure(config):
    """Configure Django before test collection (for non-DB tests)."""
    if not os.environ.get('DJANGO_SETTINGS_MODULE'):
        os.environ['DJANGO_SETTINGS_MODULE'] = 'backend.settings'
    if not os.environ.get('USE_SQLITE'):
        os.environ['USE_SQLITE'] = 'True'

    if not settings.configured:
        import django
        django.setup()
