"""Django settings for EarthScape Climate Agency.

MongoDB (via PyMongo repositories) is the primary application database, so the
relational DATABASES setting stays empty and no ORM model, migration or Django admin
is used. Authentication is a custom JWT pipeline against MongoDB user records.
"""

from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from common.env import ENV, development_signing_key, allowed_origins, BACKEND_DIR, REPO_ROOT

BASE_DIR = BACKEND_DIR
PROJECT_ROOT = REPO_ROOT

SECRET_KEY = development_signing_key()
SIGNING_KEY_IS_EPHEMERAL = not ENV.exists("DJANGO_SECRET_KEY")
DEBUG = ENV.flag("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ENV.items("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

TESTING = False

INSTALLED_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "apps.authentication",
    "apps.users",
    "apps.datasets",
    "apps.ingestion",
    "apps.climate",
    "apps.processing",
    "apps.machine_learning",
    "apps.alerts",
    "apps.reports",
    "apps.support",
    "apps.monitoring",
]

MIDDLEWARE = [
    "common.middleware.SecurityHeadersMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "common.middleware.RequestContextMiddleware",
    "common.middleware.AuditTrailMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

# No relational database by design - see module docstring.
DATABASES: dict = {}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.authentication.jwt_auth.MongoJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.authentication.permissions.IsAuthenticatedMongo",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "common.pagination.MongoPageNumberPagination",
    "PAGE_SIZE": 25,
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_CLASSES": [
        "common.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "login": ENV.raw("THROTTLE_LOGIN", "10/min"),
        "register": ENV.raw("THROTTLE_REGISTER", "5/hour"),
        "password_reset": ENV.raw("THROTTLE_PASSWORD_RESET", "5/hour"),
        "feedback": ENV.raw("THROTTLE_FEEDBACK", "20/hour"),
        "ingest": ENV.raw("THROTTLE_INGEST", "30/hour"),
        "processing": ENV.raw("THROTTLE_PROCESSING", "60/hour"),
        "training": ENV.raw("THROTTLE_TRAINING", "10/hour"),
        "reports": ENV.raw("THROTTLE_REPORTS", "30/hour"),
        "burst": ENV.raw("THROTTLE_BURST", "300/hour"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EarthScape Climate Agency API",
    "DESCRIPTION": "Big Data climate intelligence, analytics and prediction platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
}

CORS_ALLOWED_ORIGINS = allowed_origins()
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = [
    "accept", "accept-encoding", "authorization", "content-type",
    "csrftoken", "origin", "user-agent", "x-correlation-id", "x-requested-with",
]
CORS_MAX_AGE = 600

CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

JWT = {
    "SIGNING_KEY": ENV.raw("JWT_SIGNING_KEY", SECRET_KEY),
    "ALGORITHM": ENV.raw("JWT_ALGORITHM", "HS256"),
    "ACCESS_MINUTES": ENV.integer("JWT_ACCESS_MINUTES", 15),
    "REFRESH_DAYS": ENV.integer("JWT_REFRESH_DAYS", 7),
    "ISSUER": ENV.raw("JWT_ISSUER", "earthscape-climate-agency"),
}

MONGODB = {
    "URI": ENV.raw("MONGODB_URI"),
    "DATABASE": ENV.raw("MONGODB_DATABASE", "earthscape_climate"),
}

LOGGING_LEVEL = ENV.raw("LOG_LEVEL", "INFO").upper()
LOG_DIR = PROJECT_ROOT / "artifacts" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {"()": "common.logging_config.StructuredFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
            "level": LOGGING_LEVEL,
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "structured",
            "filename": str(LOG_DIR / "backend.log"),
            "maxBytes": 5_000_000,
            "backupCount": ENV.integer("LOG_BACKUP_COUNT", 3),
            "level": LOGGING_LEVEL,
        },
    },
    "root": {"handlers": ["console", "file"], "level": LOGGING_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "pymongo": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

APP = {
    "ALLOW_SELF_REGISTRATION": ENV.flag("ALLOW_SELF_REGISTRATION", False),
    "DEFAULT_ROLE": ENV.raw("DEFAULT_REGISTRATION_ROLE", "viewer"),
    "MAX_UPLOAD_BYTES": ENV.integer("MAX_UPLOAD_BYTES", 200 * 1024 * 1024),
    "MAX_UPLOAD_ROWS": ENV.integer("MAX_UPLOAD_ROWS", 2_000_000),
    "PREVIEW_ROWS": ENV.integer("UPLOAD_PREVIEW_ROWS", 20),
    "PASSWORD_MIN_LENGTH": ENV.integer("PASSWORD_MIN_LENGTH", 10),
    "LOGIN_MAX_FAILURES": ENV.integer("LOGIN_MAX_FAILURES", 8),
    "LOGIN_LOCKOUT_MINUTES": ENV.integer("LOGIN_LOCKOUT_MINUTES", 15),
    "BCRYPT_ROUNDS": ENV.integer("BCRYPT_ROUNDS", 12),
    "OBSERVATIONS_RETENTION_DAYS": ENV.integer("OBSERVATIONS_RETENTION_DAYS", 0),
}

if not MONGODB["URI"]:
    # Boot is still allowed so the interface and health checks work; every data route
    # returns an explicit configuration error instead of a fake success.
    import warnings

    warnings.warn(
        "MONGODB_URI is not configured. The API will start, but data operations will fail "
        "with a clear 503 configuration response.",
        RuntimeWarning,
    )

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list at least one host.")
