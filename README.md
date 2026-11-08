# Yatfa Scout

A human-in-the-loop approval queue for social-media outreach. An agent (or a person) drafts
every text, a human approves each one individually, and only then is it sent. Scout keeps the
queue, the send log, inbound replies, market questions and a small lead funnel in Postgres.

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
- **Sending happens outside the app** (by hand or by a separate script/browser agent) and only
  for approved rows.

## Draft lifecycle

```
draft -> waiting -> approved -> sent
                \-> skipped
```

`waiting` → (approver clicks *Send*) → `approved` → (sender posts, then `soc sent`) → `sent`.
Approving changes **exactly one row**; no other draft is touched. One text, one approval — this
rule lives in the data layer, not just in the UI.

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
`node bin/lead.mjs` (`list`, `add`, `stage`). The goal is inbound interest: comment on their
post, wait for a reaction, send a connection note if the thread goes quiet, message directly
only after the connection is accepted. There are no bulk messages by design.

## Design rules

- One draft, one approval. No batching, no bulk actions.
- The app makes no outbound calls to social networks.
- The web server binds to `127.0.0.1` only.
- Secrets and personal data never enter the repository (`.env` and `data/` are git-ignored).
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
