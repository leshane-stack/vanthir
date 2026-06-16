"""Vanthir settings — Railway/Postgres/WhiteNoise ready."""
import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-in-prod")
DEBUG = os.environ.get("DEBUG", "1") == "1"

# ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS read from env (comma-separated) and fall
# back to the real production domains + localhost, so the app is correct out of
# the box and self-documents the hosts it serves. Set the env vars on Railway to
# override; RAILWAY_PUBLIC_DOMAIN (below) is always appended so the
# *.up.railway.app host keeps working regardless.
_DEFAULT_ALLOWED_HOSTS = "vanthir.com,www.vanthir.com,localhost,127.0.0.1"
_DEFAULT_CSRF_ORIGINS = "https://vanthir.com,https://www.vanthir.com"

ALLOWED_HOSTS = [
    h for h in os.environ.get("ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS).split(",") if h
]
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", _DEFAULT_CSRF_ORIGINS).split(",") if o
]
# Railway provides the public domain in this env var.
_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
if _railway_domain:
    ALLOWED_HOSTS.append(_railway_domain)
    # Railway's platform healthcheck probes with Host: healthcheck.railway.app —
    # must be allowed or the deploy healthcheck 400s once ALLOWED_HOSTS is
    # restrictive (i.e. not "*").
    ALLOWED_HOSTS.append("healthcheck.railway.app")
    CSRF_TRUSTED_ORIGINS.append(f"https://{_railway_domain}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "properties",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "vanthir.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "vanthir.wsgi.application"

# Postgres on Railway via DATABASE_URL; sqlite locally if unset.
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
        ssl_require=not DEBUG,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Production hardening (active when DEBUG=0).
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    # Railway's healthcheck hits /healthz over plain HTTP; without this it would
    # be 301'd to HTTPS and the healthcheck (and deploy) would fail.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
