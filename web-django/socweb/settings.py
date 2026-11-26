"""
Django settings for the socweb project (Yatfa Scout v2).

The project adopts the existing `social` Postgres database: every model is
unmanaged, so `migrate` can never issue DDL against it.

Configuration comes from the environment, or from the repo-root `.env` that
`lib/db.mjs` already reads. There are no hardcoded fallbacks for the two
secrets-bearing settings (`DATABASE_URL`, `SECRET_KEY`): a missing value is a
boot failure, mirroring `loadEnv()` in `lib/db.mjs`.
"""

from pathlib import Path

import environ

# web-django/
BASE_DIR = Path(__file__).resolve().parent.parent
# Repo root: the `.env` here is the one `lib/db.mjs` reads, so the Node app and
# this project always talk to the same database.
REPO_ROOT = BASE_DIR.parent

env = environ.Env(DEBUG=(bool, False))

# Which dotenv file to read. Defaults to the repo-root `.env`; overridable the
# way `lib/db.mjs` lets `SCOUT_DATA_DIR` override its data directory, so tests
# and alternate deployments can point somewhere else. Environment variables
# always win over the file.
ENV_FILE = Path(env.str('SCOUT_ENV_FILE', default=str(REPO_ROOT / '.env')))
if ENV_FILE.is_file():
    env.read_env(str(ENV_FILE))

# No default: boot fails when the secret key is not configured.
SECRET_KEY = env.str('SECRET_KEY')

DEBUG = env.bool('DEBUG')

# Loopback only, parity with `server.mjs`'s `.listen(PORT, '127.0.0.1')`.
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'social',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'socweb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'socweb.wsgi.application'

# No default: boot fails when DATABASE_URL is not configured.
DATABASES = {'default': env.db('DATABASE_URL')}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# The operator-facing UI is in Russian; code and comments stay English.
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

# A failed request must name its cause on the console the operator already
# watches. Django's own `DEFAULT_LOGGING` cannot: its `console` handler carries
# the `require_debug_true` filter, so with `DEBUG` off the traceback is
# discarded, and its only other route out of `django.request` is `mail_admins`,
# whose `AdminEmailHandler.emit` returns immediately when no `ADMINS` are set —
# which this project never sets, because an SMTP send is an outbound network
# call (knowledge scout-3, decision 5: no outbound network calls in v1). So
# with `DEBUG` off the console kept printing the `django.server` status line of
# a 500 and nothing named the exception. Failing loudly on the console is the
# project's convention anyway: `web/server.mjs` answers
# `json(res, 500, { error: e.message })` and `bin/soc.mjs` ends with
# `console.error('ошибка:', e.message)`.
#
# Four choices here are load-bearing:
#
#   1. `django.request`, not `django`. `django.utils.log.log_response` — the
#      function that logs a 500 with its traceback — writes to
#      `logging.getLogger('django.request')`. Configuring `django` instead
#      would also work for the traceback but would damage the request lines:
#      `logging.config._handle_existing_loggers` resets every existing child of
#      a newly configured logger (`setLevel(NOTSET)`, `handlers = []`,
#      `propagate = True`), so `django.server` would lose `DEFAULT_LOGGING`'s
#      own `ServerFormatter` and its lines would come back reformatted.
#   2. `ERROR`. `log_response` logs a 5xx at error and a 4xx at warning, so a
#      lower level would print a line per 404.
#   3. `propagate: False`. Without it a 500 is logged twice when `DEBUG` is on
#      (this handler plus `django`'s own console handler).
#   4. No handler on the response: the traceback goes to the console only. The
#      500 page stays Django's bare one, so nothing internal reaches a browser.
#
# What reaches the console is the exception, its message and the request path —
# not the local variables, settings or SQL that the `DEBUG=1` page dumps.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {'format': '[{asctime}] {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'},
    },
    'loggers': {
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# The invariant tests of this slice touch no database, and the models are
# unmanaged, so the runner must never create or drop one.
TEST_RUNNER = 'socweb.test_runner.NoDatabaseTestRunner'
