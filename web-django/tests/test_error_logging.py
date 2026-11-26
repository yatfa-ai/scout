"""A failed request names its cause on the console, with `DEBUG` off.

In the run the README documents — `DEBUG` unset — an unhandled exception used
to be recorded nowhere: the browser got Django's bare «Server Error (500)»
page and the console only the `django.server` status line, so the failure was
visible as *something* and diagnosable as *nothing*. Django's
`DEFAULT_LOGGING` is the reason: its `console` handler carries the
`require_debug_true` filter, and its only other route out of `django.request`
is `mail_admins`, which returns immediately when no `ADMINS` are set — and
this project sets none, because an SMTP send is an outbound network call
(knowledge scout-3, decision 5). `socweb/settings.py` now configures
`django.request` onto an unfiltered console handler.

⚠️ **Asserting on the `LOGGING` dict cannot tell that fix from its mutants.**
Re-adding `require_debug_true`, naming a logger `log_response` never writes to,
or raising the level above ERROR each leave a `LOGGING` block that *looks*
right and logs nothing — and the failure mode is silence. So the examples here
provoke a real failure and read what the process actually wrote to its
console; the two configuration-shaped classes at the end exist only to pin the
*choices* a passing console cannot distinguish (which logger, which level) and
the collateral a wider logger would cause (`django.server`'s own format).

How the failure is provoked, in the probe-subprocess convention of
`tests/_probe.py`: `probe_env()` strips `DATABASE_URL`, `SECRET_KEY`, `DEBUG`
and `SCOUT_ENV_FILE` from the inherited environment and points
`SCOUT_ENV_FILE` at a path that cannot exist, so no developer `.env` can
decide the outcome. `DATABASE_URL` then points at a dead port and the request
carries a session cookie, which makes the session load reach for the database
inside the request — the absence of a database *is* the fixture, so these
examples need none, which also fits
`settings.TEST_RUNNER = 'socweb.test_runner.NoDatabaseTestRunner'`.
`Client(raise_request_exception=False)` lets the handler turn the exception
into a 500 the way a server does, instead of re-raising it into the test.

The console is read off the probe's **stderr** (where `logging.StreamHandler`
writes by default) and the measurements off its **stdout**, so the two cannot
be confused. Against an unpatched `socweb/settings.py` the captured console is
literally `''` and this module fails.
"""

import json
import re
import unittest

from ._probe import run_probe

# A port nothing listens on, so the first database touch inside the request
# raises. The probes assert the request really returned 500, so a port that
# unexpectedly answers fails loudly instead of passing vacuously.
DEAD_PORT = 59321
DEAD_DATABASE_URL = (
    f'postgres://scout83user:scout83-dsn-password-must-not-be-logged'
    f'@127.0.0.1:{DEAD_PORT}/social'
)
# Distinctive on purpose: the examples assert these never reach the console,
# which is the whole difference from the `DEBUG=1` page (acceptance 9).
DSN_PASSWORD = 'scout83-dsn-password-must-not-be-logged'
PROBE_SECRET = 'scout83-secret-key-must-not-be-logged'

# Staff-only, so the request fails inside the session/auth lookup rather than
# in a view — an uncaught exception from middleware is logged by the same
# `log_response` call, and the path is what the console line must name.
FAILING_PATH = '/queue/'
# Routed nowhere, so it is a 404: `log_response` logs a 4xx at warning level.
MISSING_PATH = '/scout83-no-such-path/'

# `django.utils.log.log_response` formats '%s: %s' % (reason_phrase, path).
SERVER_ERROR_LINE = f'Internal Server Error: {FAILING_PATH}'
TRACEBACK_HEADER = 'Traceback (most recent call last):'

# Django's bare 500 page is ~145 bytes; the `DEBUG=1` page is tens of KB.
BARE_ERROR_PAGE_LIMIT = 2000

# --- probes ----------------------------------------------------------------

# Shared by every probe: boot Django, and put `testserver` into ALLOWED_HOSTS
# (the settings deliberately keep it to the two loopback names).
PRELUDE = """
import json
import logging
import django
django.setup()
from django.conf import settings
from django.test.utils import setup_test_environment
setup_test_environment()
from django.test import Client

SESSION_COOKIE = 'scout83nonexistentsessionkey0000'


def handler_chain(name):
    # Every handler a record on `name` can reach, following propagation the
    # way `logging.Logger.callHandlers` does.
    chain = []
    logger = logging.getLogger(name)
    while logger is not None:
        for handler in logger.handlers:
            chain.append({
                'logger': logger.name,
                'handler': type(handler).__name__,
                'formatter': type(handler.formatter).__name__ if handler.formatter else None,
                'filters': sorted(type(f).__name__ for f in handler.filters),
            })
        if not logger.propagate:
            break
        logger = logger.parent
    return chain


def logger_facts(name):
    logger = logging.getLogger(name)
    return {
        'propagate': logger.propagate,
        'own_filters': sorted(type(f).__name__ for f in logger.filters),
        'passes_error': logger.isEnabledFor(logging.ERROR),
        'passes_warning': logger.isEnabledFor(logging.WARNING),
        'chain': handler_chain(name),
    }


def failing_request(path):
    client = Client(raise_request_exception=False)
    # A session key with no row behind it: loading it reaches the database,
    # which is the unreachable one, inside the request.
    client.cookies['sessionid'] = SESSION_COOKIE
    return client.get(path)
"""

# The whole claim of this slice, in the mode the README documents.
PROVOKE_A_SERVER_ERROR = PRELUDE + """
import os
response = failing_request(os.environ['PROBE_PATH'])
print(json.dumps({
    'status': response.status_code,
    'body': response.content.decode('utf-8', 'replace'),
    'debug': settings.DEBUG,
    'admins': list(settings.ADMINS),
    'request_logger': logger_facts('django.request'),
}))
"""

# A 404 is a warning, so it must leave the console untouched.
PROVOKE_A_MISSING_PAGE = PRELUDE + """
import os
client = Client(raise_request_exception=False)
response = client.get(os.environ['PROBE_PATH'])
print(json.dumps({'status': response.status_code, 'debug': settings.DEBUG}))
"""

# With `DEBUG` on, `django`'s own console handler also accepts the record, so
# a propagating logger would log the same 500 twice.
COUNT_THE_LINES_WITH_DEBUG_ON = PRELUDE + """
import os
response = failing_request(os.environ['PROBE_PATH'])
print(json.dumps({'status': response.status_code, 'debug': settings.DEBUG}))
"""

# Collateral check: configuring `django` instead of `django.request` would
# reset this logger (`logging.config._handle_existing_loggers` clears the
# handlers of existing children), and the stock request lines would lose
# Django's own `ServerFormatter`.
READ_THE_SERVER_LOGGER = PRELUDE + """
print(json.dumps({
    'server': logger_facts('django.server'),
    'request': logger_facts('django.request'),
}))
"""


def probe(script, **overrides):
    """Run a probe with the dead DSN; return its parsed stdout and its console."""
    result = run_probe(
        script, DATABASE_URL=DEAD_DATABASE_URL, SECRET_KEY=PROBE_SECRET, **overrides
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result.stderr


def stream_handlers(chain):
    return [entry for entry in chain if entry['handler'] == 'StreamHandler']


# --- the console, with DEBUG off -------------------------------------------


class ServerErrorReachesTheConsoleTest(unittest.TestCase):
    """An unhandled exception during a request writes its traceback to stderr."""

    @classmethod
    def setUpClass(cls):
        cls.measured, cls.console = probe(PROVOKE_A_SERVER_ERROR, PROBE_PATH=FAILING_PATH)

    def test_the_probe_ran_with_debug_off(self):
        # Without this the measurement proves nothing: Django's own console
        # handler logs everything when DEBUG is on.
        self.assertFalse(
            self.measured['debug'], 'the probe inherited DEBUG=1 from somewhere'
        )

    def test_no_admins_are_configured_so_mail_admins_cannot_be_the_route(self):
        # `AdminEmailHandler.emit` returns immediately with no ADMINS, so the
        # console is the only possible destination (knowledge scout-3,
        # decision 5: no outbound network calls).
        self.assertEqual(self.measured['admins'], [])

    def test_the_request_really_returned_a_server_error(self):
        # Positive control for the fixture: a port that unexpectedly answers
        # must fail here rather than let the console assertions pass on a
        # request that never failed.
        self.assertEqual(
            self.measured['status'],
            500,
            f'nothing failed: the request to {FAILING_PATH} was answered',
        )

    def test_the_console_carries_a_traceback(self):
        self.assertIn(
            TRACEBACK_HEADER,
            self.console,
            f'the console carries no traceback: {self.console!r}',
        )

    def test_the_traceback_names_the_exception_and_the_unreachable_port(self):
        self.assertIn('OperationalError', self.console)
        self.assertIn(
            str(DEAD_PORT),
            self.console,
            'the traceback must name what could not be reached, not just that '
            'something failed',
        )

    def test_the_console_line_names_the_failing_request_path(self):
        self.assertIn(
            SERVER_ERROR_LINE,
            self.console,
            'the operator must be able to tell which request failed',
        )

    def test_the_console_carries_no_secret(self):
        # The `DEBUG=1` page dumps settings and local variables; this route
        # deliberately carries only the exception, its message and the path.
        self.assertTrue(self.console, 'nothing was logged, so nothing is proven here')
        self.assertNotIn(DSN_PASSWORD, self.console)
        self.assertNotIn(PROBE_SECRET, self.console)
        self.assertNotIn('SECRET_KEY', self.console)

    def test_the_response_stays_the_bare_error_page(self):
        body = self.measured['body']
        self.assertIn('Server Error (500)', body)
        self.assertNotIn(TRACEBACK_HEADER, body)
        self.assertNotIn('OperationalError', body)
        self.assertLess(
            len(body),
            BARE_ERROR_PAGE_LIMIT,
            'the traceback belongs on the console, never in the response',
        )


class MissingPageStaysQuietTest(unittest.TestCase):
    """A 404 adds no line: its request line stays the only trace."""

    @classmethod
    def setUpClass(cls):
        cls.measured, cls.console = probe(PROVOKE_A_MISSING_PAGE, PROBE_PATH=MISSING_PATH)

    def test_the_request_really_was_a_404(self):
        self.assertEqual(self.measured['status'], 404)
        self.assertFalse(self.measured['debug'])

    def test_nothing_is_written_to_the_console(self):
        self.assertEqual(
            self.console,
            '',
            'the handler level must keep 404s off the console, or every '
            'missing favicon becomes a logged error',
        )


class ServerErrorIsLoggedOnceWithDebugOnTest(unittest.TestCase):
    """With `DEBUG` on, `django`'s console handler must not log it a second time."""

    @classmethod
    def setUpClass(cls):
        cls.measured, cls.console = probe(
            COUNT_THE_LINES_WITH_DEBUG_ON, PROBE_PATH=FAILING_PATH, DEBUG='1'
        )

    def test_the_probe_ran_with_debug_on(self):
        self.assertTrue(self.measured['debug'])
        self.assertEqual(self.measured['status'], 500)

    def test_the_server_error_is_logged_exactly_once(self):
        self.assertEqual(
            len(re.findall(re.escape(SERVER_ERROR_LINE), self.console)),
            1,
            'propagate must stay False, or a 500 is reported twice with '
            f'DEBUG on: {self.console!r}',
        )


# --- the choices a passing console cannot distinguish ----------------------


class RequestLoggerChainTest(unittest.TestCase):
    """`django.request` reaches an unfiltered stream handler, at ERROR."""

    @classmethod
    def setUpClass(cls):
        cls.measured, _console = probe(PROVOKE_A_SERVER_ERROR, PROBE_PATH=FAILING_PATH)
        cls.facts = cls.measured['request_logger']

    def test_the_logger_reaches_a_stream_handler(self):
        self.assertTrue(
            stream_handlers(self.facts['chain']),
            f'no console handler on the chain: {self.facts["chain"]}',
        )

    def test_no_debug_filter_stands_between_the_logger_and_the_console(self):
        # `DEFAULT_LOGGING`'s console handler carries `require_debug_true`,
        # which is exactly what discarded the traceback.
        filters = sorted(
            {name for entry in self.facts['chain'] for name in entry['filters']}
            | set(self.facts['own_filters'])
        )
        self.assertNotIn('RequireDebugTrue', filters)
        self.assertEqual(filters, [], f'an unexpected filter can silence it: {filters}')

    def test_the_level_passes_a_server_error_but_not_a_client_error(self):
        # `log_response` logs a 5xx at error and a 4xx at warning, so this is
        # the mechanical half of the quiet-404 example above.
        self.assertTrue(self.facts['passes_error'])
        self.assertFalse(
            self.facts['passes_warning'],
            'at WARNING the console gets a line per 404',
        )

    def test_the_logger_does_not_propagate(self):
        self.assertFalse(self.facts['propagate'])


class RequestLinesKeepDjangosOwnFormatTest(unittest.TestCase):
    """`django.server` is untouched, so the stock request lines are unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.measured, _console = probe(READ_THE_SERVER_LOGGER)

    def test_the_server_logger_keeps_its_own_formatter(self):
        # Configuring `django` instead of `django.request` resets every
        # existing child logger, and `django.server` would come back with no
        # handler of its own and reformatted lines.
        formatters = [
            entry['formatter'] for entry in self.measured['server']['chain']
        ]
        self.assertIn(
            'ServerFormatter',
            formatters,
            f'the request lines lost Django\'s own format: {formatters}',
        )

    def test_the_server_logger_still_keeps_its_records_to_itself(self):
        self.assertFalse(
            self.measured['server']['propagate'],
            'request lines would also reach the error console',
        )

    def test_the_two_loggers_are_configured_apart(self):
        # Sibling loggers, each with its own handler: neither can be the other's
        # route, which is why the request lines survive the error console.
        self.assertEqual(
            [entry['logger'] for entry in self.measured['server']['chain']],
            ['django.server'],
        )
        self.assertEqual(
            [entry['logger'] for entry in self.measured['request']['chain']],
            ['django.request'],
        )


if __name__ == '__main__':
    unittest.main()
