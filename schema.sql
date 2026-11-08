-- Yatfa Scout schema. Idempotent: safe to run repeatedly.

CREATE TABLE IF NOT EXISTS people (
  id          text PRIMARY KEY,
  name        text NOT NULL,
  network     text NOT NULL CHECK (network IN ('linkedin','x','reddit')),
  profile     text,
  headline    text,
  followers   text,
  segment     text,          -- free-form audience segment, e.g. founder / eng-leader / engineer / researcher / vendor
  relevance   text,
  first_seen  date,
  status      text NOT NULL DEFAULT 'наблюдаем',
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- A draft is the unit of work: everything the agent intends to send passes through this table.
-- waiting  -> awaits the approver; approved -> the approver clicked "send" (applies to THIS row only);
-- sent -> posted; skipped -> the approver declined; draft -> the agent is still writing.
CREATE TABLE IF NOT EXISTS drafts (
  id            bigserial PRIMARY KEY,
  network       text NOT NULL CHECK (network IN ('linkedin','x','reddit')),
  kind          text NOT NULL CHECK (kind IN ('post','comment','reply','thread')),
  status        text NOT NULL DEFAULT 'waiting'
                CHECK (status IN ('draft','waiting','approved','sent','skipped')),
  target_person text REFERENCES people(id),
  target_url    text,        -- where to post: link to the post or comment
  target_note   text,        -- who this is and what the post is about, one or two lines
  body          text NOT NULL,           -- text to send; for a thread, tweets separated by a --- line
  facts         text,        -- where the claims come from and what is verified (links to code / production)
  question      text,        -- question for the approver about this text
  created_at    timestamptz NOT NULL DEFAULT now(),
  approved_at   timestamptz,
  sent_at       timestamptz,
  sent_url      text,        -- link to what actually went out
  skip_reason   text
);
CREATE INDEX IF NOT EXISTS drafts_status_idx ON drafts(status);

-- Draft kinds added later: connect (connection request with a note) and dm (message after they reacted).
ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_kind_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_kind_check
  CHECK (kind IN ('post','comment','reply','thread','connect','dm'));

-- Messages: ours (out) and addressed to us (in).
CREATE TABLE IF NOT EXISTS interactions (
  id           text PRIMARY KEY,
  ts           text NOT NULL,          -- kept as-is: timestamps are sometimes approximate (~)
  network      text NOT NULL CHECK (network IN ('linkedin','x','reddit')),
  direction    text NOT NULL CHECK (direction IN ('out','in')),
  kind         text NOT NULL,
  person       text,
  post_url     text,
  parent_url   text,
  in_reply_to  text REFERENCES interactions(id),
  text         text,
  draft_id     bigint REFERENCES drafts(id),
  draft_ref    text,                   -- legacy reference to a draft file (history only)
  stats        jsonb,
  note         text
);

-- Direct questions from the market: raw material for positioning.
CREATE TABLE IF NOT EXISTS questions (
  id            text PRIMARY KEY,
  date          date NOT NULL,
  network       text NOT NULL,
  asker         text REFERENCES people(id),
  asker_role    text,
  to_us         boolean NOT NULL DEFAULT false,   -- asked us directly
  question      text NOT NULL,
  context       text,
  source        text REFERENCES interactions(id),
  url           text,
  topic         text,
  yatfa_answer  text,                  -- what we actually have, with a link to code / production
  status        text
);

-- Send log: one row per publication.
CREATE TABLE IF NOT EXISTS ledger (
  id          bigserial PRIMARY KEY,
  at          text NOT NULL,
  network     text NOT NULL,
  topic       text NOT NULL,
  url         text,
  outcome     text
);

-- Leads: an acquaintance funnel. No bulk messaging:
-- comment on their post -> connection request with a note -> conversation after they react.
CREATE TABLE IF NOT EXISTS leads (
  id          bigserial PRIMARY KEY,
  person      text NOT NULL REFERENCES people(id),
  stage       text NOT NULL DEFAULT 'candidate'
              CHECK (stage IN ('candidate','commented','connected','talking','client','dead')),
  source_url  text,
  pain        text,        -- their words about the problem, verbatim or close to it
  next_step   text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS leads_person_uniq ON leads(person);
