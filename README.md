# Yatfa Scout

A human-in-the-loop approval queue for social-media posts and replies. An agent (or a person)
drafts the texts, a human approves each one individually, and only approved texts are meant to
be sent. Scout keeps the queue, the send log, inbound replies, market questions and a small
lead funnel in Postgres.

Scout is the bookkeeping and the gate. It never talks to a social network itself.

## How it works

```
Postgres  <--  soc   (CLI: queue, drafts, send log; used by the agent)
    ^
    '--------- web   (localhost:4177: the approval screen for the human)
```

- **`soc`** reads the queue, files drafts and records outcomes. It publishes nothing:
  `soc sent <id>` refuses any draft that is not in status `approved`.
- **`web`** is a single screen for the approver: the queue with approve / edit / skip buttons,
  the send log, market questions, replies, leads and people.
- **Sending happens outside the app**, by the operator, and only for approved rows.

## Draft lifecycle

```
draft -> waiting -> approved -> sent
                \-> skipped
```

`waiting` → (approver clicks *Send*) → `approved` → (sender posts, then `soc sent`) → `sent`.
On the web screen, approving changes **exactly one row**; no other draft is touched. One text,
one approval.

## Quick start

Requires Node 20+ and a Postgres database.

```sh
npm install
cp .env.example .env            # set DATABASE_URL
node bin/soc.mjs migrate        # apply schema.sql (idempotent)
npm run web                     # http://127.0.0.1:4177
node bin/soc.mjs help           # CLI reference (incl. `pace`: last send and sends in 24h, for add-ons that pace themselves)
```

## Configuration

| Variable         | Default            | Purpose                                              |
|------------------|--------------------|------------------------------------------------------|
| `DATABASE_URL`   | read from `.env`   | Postgres connection string                           |
| `PORT`           | `4177`             | Port of the web screen (always bound to `127.0.0.1`) |
| `SCOUT_DATA_DIR` | `./data`           | Where `soc export` writes and `import` reads JSONL   |
| `SECRET_KEY`     | read from `.env`   | Django secret key (`web-django/` only), never committed |
| `DEBUG`          | off                | Django debug mode (`web-django/` only)               |
| `SCOUT_ENV_FILE` | `./.env`           | Which dotenv file `web-django/` reads                |

## Tests

```sh
npm test
```

Built-in `node:test`, no extra dependencies. The web tests and the `soc` CLI usage checks need no
database, and neither do the usage checks of the `lead` CLI, the `soc revise` help check or the
`soc add --status` refusals. The import tests, the `lead` funnel checks, the `soc revise` status
guard and the `soc add` row checks need a real Postgres: set `TEST_DATABASE_URL` (they create and
drop a scratch database) — without one those tests are skipped.

## web-django

The Django 5 rebuild (v2) lives in `web-django/` and adopts this same `social` database: every
model is unmanaged, so it issues no DDL and changes no data. What it serves today:

- **`/` and `/queue/` — the approval queue**, the only surface that writes `drafts`: approve,
  unapprove, edit and skip, each a form POST through the same guarded transitions as the Node
  web app (`web/server.mjs`, the parity reference). `/` redirects there, and for a visitor who
  is not logged in it ends at the admin login and comes back to the queue afterwards.
- **The admin over the six tables**, reachable from the queue's tab row: the send log («Ушло»),
  leads, market questions, the replies to us (`direction = in`) and people. Its changelists
  carry the content the Node screen puts in front of the operator, so each pane is readable
  without opening a detail page. `drafts` is listed there too, but view-only — the queue owns
  its writes. The four columns `schema.sql` gives a `DEFAULT now()` — `created_at` on `people`,
  `drafts` and `leads`, plus `leads.updated_at` — are the database's to fill, so adding a person
  or a lead needs no hand-typed timestamp, a correction leaves `created_at` exactly as it was,
  and advancing a lead bumps `updated_at` the way `bin/lead.mjs` does on both of its write
  paths — which is what makes the just-advanced lead the first row of a changelist ordered by
  `-updated_at`. All four stay changelist columns, where they are scanned; they are no longer
  on the detail pages.

**The Node app (`npm run web`, port 4177) remains the primary UI** until the acceptance-parity
slice of the rebuild is done.

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r web-django/requirements.txt
cp .env.example .env                 # set DATABASE_URL and SECRET_KEY, and DEBUG=1 for local work
cd web-django && python manage.py runserver 127.0.0.1:8000   # http://127.0.0.1:8000/
```

Settings read `DATABASE_URL`, `SECRET_KEY` and `DEBUG` from the environment, or from the
repo-root `.env` that `lib/db.mjs` already reads. `DATABASE_URL` and `SECRET_KEY` have no
fallbacks: a missing value is a boot failure, the same way `loadEnv()` throws. `DEBUG` is off
unless set — turn it on locally, because `runserver` serves the admin's CSS and JS only with
`DEBUG` on.

The admin needs a Django user, and the queue is staff-only too, so the same login gates both.
Django keeps its users, sessions and admin log in its own tables, which the `social` database
does not have yet. Creating them is a one-time

```sh
cd web-django && python manage.py migrate && python manage.py createsuperuser
```

This is safe and was verified on a copy of the schema: all six Scout models are unmanaged, so
`migrate` emits no DDL for them — it only adds Django's own `auth_*`, `django_*` tables, and
`people`, `drafts`, `interactions`, `questions`, `ledger` and `leads` come out byte-identical,
constraints, indexes and rows included. The schema of those six still changes only through
`schema.sql`.

Its tests:

```sh
cd web-django && python -m unittest discover -s tests -t .
```

They pin the environment contract and the fail-fast, the loopback-only `ALLOWED_HOSTS`, that
every model stays unmanaged, that no HTTP client is imported anywhere under `web-django/`, that
`.env` stays git-ignored with no secrets in tracked files, that `drafts` stays view-only in the
admin, that the admin's add forms ask for no machine timestamp while the three operator-supplied
ones stay required, and the front door: `/` goes to the queue, «Открыть сайт» in the admin goes
there too, and the queue's tab row goes to the right admin pane. The configuration half of all
that needs neither a database nor a configured variable. The half that renders pages — the
queue's transitions, the changelist columns, what an admin add or edit does to the timestamps and
to the leads ordering, where a URL actually lands — needs a real Postgres: those
checks build and drop a scratch database from `schema.sql`, the way the JS tests do, so set
`TEST_DATABASE_URL`. Without one they are skipped and say so, and the rest of the suite still
runs. `npm test` is untouched: `node --test` discovers JS tests only.

## Backup and restore

```sh
node bin/soc.mjs export         # people, interactions, questions, drafts, ledger, leads -> data/*.jsonl
node bin/import.mjs             # load people, drafts, interactions, questions, ledger, leads back (safe to re-run)
```

`data/` is git-ignored: it contains real contacts and message text, so it does not belong in
this repository. Keep backups somewhere private.

## Lead funnel

`people` holds contacts; `leads` holds one row per person (`leads.person` is unique) with a
stage, in order: `candidate` → `commented` (we commented, waiting for their move) → `connected`
(connection accepted) → `talking` (they wrote first) → `client` | `dead`. The web screen shows
them on the *Лиды* tab.

```sh
node bin/lead.mjs list                                      # leads by funnel stage
node bin/lead.mjs add --person <people-id> [--stage --url --pain --next]
node bin/lead.mjs stage <id> --stage <stage>                # move a lead to another stage
```

`add` is an upsert: calling it again updates the lead; without `--stage` the stage returns to
`candidate`, and `--pain` / `--next` overwrite only when given.

The funnel is built around inbound interest: comment on their post, wait for a reaction, send a
connection note if the thread goes quiet, message directly only after the connection is
accepted. Texts for the funnel go through the same draft queue as everything else, as the
draft kinds `connect` (connection request with a note) and `dm` (message after they reacted):
same `waiting → approved` cycle. Scout has no bulk-messaging feature.

## Design rules

- The approval screen works per draft: there is no bulk approve.
- The app makes no outbound calls to social networks.
- The web server binds to `127.0.0.1` only and has no authentication — keep it on localhost.
- Secrets and personal data are kept out of the repository (`.env` and `data/` are git-ignored).
- The schema changes only through `schema.sql`, which is idempotent.

## Extending Scout

Scout stays a queue and a record. Anything that connects to a platform lives in a separate add-on
outside this repository: it reads approved drafts from the same database and records the outcome
with `soc sent <id> --url <link>`. Each add-on follows the terms of the platforms it connects to and
is maintained by whoever runs it.

## Layout

```
bin/soc.mjs      agent console
bin/lead.mjs     lead funnel
bin/import.mjs   restore from JSONL
lib/db.mjs       shared Postgres access
lib/cli.mjs      flag parsing and error helpers shared by the CLIs
schema.sql       database schema
web/server.mjs   HTTP API + static page
web/index.html   approval screen
test/            node:test suites
web-django/      Django 5 rebuild (v2): unmanaged models over the same database + admin
```

The interface and CLI messages are currently in Russian.
