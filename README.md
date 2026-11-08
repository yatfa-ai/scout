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
node bin/soc.mjs help           # CLI reference
```

## Configuration

| Variable         | Default            | Purpose                                              |
|------------------|--------------------|------------------------------------------------------|
| `DATABASE_URL`   | read from `.env`   | Postgres connection string                           |
| `PORT`           | `4177`             | Port of the web screen (always bound to `127.0.0.1`) |
| `SCOUT_DATA_DIR` | `./data`           | Where `soc export` writes and `import` reads JSONL   |

## Backup and restore

```sh
node bin/soc.mjs export         # people, interactions, questions, drafts, ledger -> data/*.jsonl
node bin/import.mjs             # load people, interactions, questions, ledger back (safe to re-run)
```

`data/` is git-ignored: it contains real contacts and message text, so it does not belong in
this repository. Keep backups somewhere private.

## Lead funnel

`people` holds contacts; `leads` holds one row per person with a stage
`candidate → commented → connected → talking → client | dead`. Manage it with
`node bin/lead.mjs` (run it without arguments for usage). The funnel is built around inbound
interest: comment on their post, wait for a reaction, send a connection note if the thread goes
quiet, message directly only after the connection is accepted. Scout has no bulk-messaging
feature.

## Design rules

- The approval screen works per draft: there is no bulk approve.
- The app makes no outbound calls to social networks.
- The web server binds to `127.0.0.1` only and has no authentication — keep it on localhost.
- Secrets and personal data are kept out of the repository (`.env` and `data/` are git-ignored).
- The schema changes only through `schema.sql`, which is idempotent.

## Layout

```
bin/soc.mjs      agent console
bin/lead.mjs     lead funnel
bin/import.mjs   restore from JSONL
lib/db.mjs       shared Postgres access
schema.sql       database schema
web/server.mjs   HTTP API + static page
web/index.html   approval screen
```

The interface and CLI messages are currently in Russian.
