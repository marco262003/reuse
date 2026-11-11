"""
Services behind ID registration and face login. Each engine sits behind a small interface
and is named in the settings (OCR_PROVIDER, FACE_PROVIDER), so it can be swapped without
touching the rules in registration.py and face_login.py.
"""
from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string


@lru_cache(maxsize=None)
def _load(path):
    return import_string(path)()


def ocr_provider():
    return _load(settings.OCR_PROVIDER)


def face_provider():
    return _load(settings.FACE_PROVIDER)


def face_provider_name():
    """The configured face engine's name, without loading its models."""
    return import_string(settings.FACE_PROVIDER).name


def id_template():
    return settings.ID_TEMPLATES[settings.ID_TEMPLATE]
