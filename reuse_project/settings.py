
"""
Django settings for the REUSE project.

Environment-specific settings are read from environment variables.
Local development values are loaded from the .env file.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# --- Environment variables ---------------------------------------------------
# Shell environment variables take precedence over .env values.
def _load_env_file(path):
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")


# --- Security -----------------------------------------------------------------
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-secret-change-me",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "localhost,127.0.0.1",
    ).split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]


# --- Applications -------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]


# --- Middleware ---------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# --- URL and templates --------------------------------------------------------
ROOT_URLCONF = "reuse_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "reuse_project.wsgi.application"


# --- Database -----------------------------------------------------------------
# PostgreSQL is required for normal development.
# SQLite is available only when DB_ENGINE=sqlite is explicitly requested.
DB_ENGINE = os.environ.get("DB_ENGINE", "postgres").lower()

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "reuse"),
            "USER": os.environ.get("DB_USER", "reuse_user"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }


# --- Authentication -----------------------------------------------------------
AUTH_USER_MODEL = "core.User"
LOGIN_URL = "login"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Password rules are enforced by core.validators.
AUTH_PASSWORD_VALIDATORS = []


# --- Language and time --------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True


# --- Static and uploaded media ------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# --- Sessions and CSRF ---------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True


# --- Cache --------------------------------------------------------------------
# Suitable for local development. Use a shared cache for multi-worker deployment.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


# --- Email --------------------------------------------------------------------
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    "REUSE <no-reply@reuse.local>",
)

if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
else:
    # Development emails are printed in the terminal.
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# --- SMS ----------------------------------------------------------------------
# SMS requires a configured provider and API integration.
SMS_API_KEY = os.environ.get("SMS_API_KEY", "")
SMS_SENDER_NAME = os.environ.get("SMS_SENDER_NAME", "")


# --- REUSE business rules -----------------------------------------------------
RESET_CODE_TTL_SECONDS = 10 * 60
RESET_RESEND_COOLDOWN_SECONDS = 60
RESET_MAX_SENDS_PER_HOUR = 5
RESET_MAX_ATTEMPTS = 5

EMAIL_VERIFY_MAX_AGE_SECONDS = 3 * 24 * 60 * 60
LOGIN_MAX_FAILURES = 10
DUE_SOON_HOURS = 24
