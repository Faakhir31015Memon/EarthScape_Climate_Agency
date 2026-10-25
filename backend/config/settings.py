"""Django settings for EarthScape Climate Agency.

There is deliberately no relational ``DATABASES`` entry and no ``django.contrib.auth``:
application records live in MongoDB Atlas and are accessed through PyMongo
repositories (``backend/common/repositories.py``). Authentication, sessions and
permissions are implemented against the ``users`` collection.
"""
from __future__ import annotations

import sys
from pathlib import Path

from config import env

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
# ``ml/`` and ``data-pipeline/`` are packages inside ``backend/``, so the BASE_DIR entry
# above already lets management commands, the dev server and any worker resolve
# ``import ml.*`` the same way.

env.validate_startup()

SECRET_KEY = env.get_str("DJANGO_SECRET_KEY")
DEBUG = env.get_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env.get_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "0.0.0.0"])

FRONTEND_ORIGINS = env.get_list("FRONTEND_ORIGIN", ["http://localhost:5173", "http://127.0.0.1:5173"])

INSTALLED_APPS = [
    "rest_framework",
    "drf_spectacular",
]

# No ORM: the platform is a document database application.
DATABASES: dict = {}

MIDDLEWARE = [
    "common.middleware.CORSAllowListMiddleware",
    "common.middleware.CorrelationIdMiddleware",
    "common.middleware.SecurityHeadersMiddleware",
    "common.middleware.RateLimitMiddleware",
    "common.middleware.LocalWriteFlushMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Only /api/docs renders a template (the OpenAPI reference). Every API response stays
# pure JSON because DEFAULT_RENDERER_CLASSES is JSONRenderer-only; without a registered
# engine drf_spectacular's Swagger view raises InvalidTemplateEngineError and 500s.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "common.api.MongoJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "common.api.error_response",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_FILTER_BACKENDS": [],
    "DEFAULT_THROTTLE_CLASSES": [],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EarthScape Climate Agency API",
    "DESCRIPTION": (
        "Big Data climate intelligence, analytics and prediction platform. "
        "MongoDB Atlas is the primary application database; Hadoop/Spark provide "
        "distributed processing; scikit-learn/XGBoost provide forecasts."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

# ------------------------------------------------------------------ security
CORS_ALLOWED_ORIGINS = FRONTEND_ORIGINS
CORS_ALLOW_CREDENTIALS = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------- paths
MEDIA_ROOT = Path(env.get_str("LOCAL_DATA_ROOT", str(BASE_DIR / ".data")))
UPLOAD_ROOT = MEDIA_ROOT / "uploads"
EXPORT_ROOT = MEDIA_ROOT / "exports"
REPORT_ROOT = MEDIA_ROOT / "reports"
MODEL_STORE = Path(env.get_str("MODEL_ARTIFACT_DIR", str(MEDIA_ROOT / "ml-artifacts")))
PROCESSING_ROOT = Path(env.get_str("PROCESSING_SCRATCH_DIR", str(MEDIA_ROOT / "processing")))
DATA_LAKE_ROOT = Path(env.get_str("DATA_LAKE_LOCAL_ROOT", str(BASE_DIR / ".data-lake")))
for _directory in (UPLOAD_ROOT, EXPORT_ROOT, REPORT_ROOT, MODEL_STORE, PROCESSING_ROOT, DATA_LAKE_ROOT):
    _directory.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------- logging
LOG_LEVEL = env.get_str("LOG_LEVEL", "INFO").upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "common.logging_formatter.StructuredFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structured",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# ------------------------------------------------------------- upload limits
MAX_UPLOAD_BYTES = env.get_int("MAX_UPLOAD_BYTES", 52428800)
MAX_UPLOAD_ROWS = env.get_int("MAX_UPLOAD_ROWS", 500000)
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = min(10_485_760, MAX_UPLOAD_BYTES)
ALLOWED_UPLOAD_EXTENSIONS = env.get_list(
    "ALLOWED_UPLOAD_EXTENSIONS",
    [".csv", ".json", ".txt", ".nc", ".parquet",
     ".xls", ".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm", ".ods"],
)

# ----------------------------------------------------------- runtime defaults
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
STATIC_URL = "/static/"
APPEND_SLASH = False
