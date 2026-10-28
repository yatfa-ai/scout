-- social-ops: схема. Идемпотентна: можно прогонять повторно.

CREATE TABLE IF NOT EXISTS people (
  id          text PRIMARY KEY,
  name        text NOT NULL,
  network     text NOT NULL CHECK (network IN ('linkedin','x','reddit')),
  profile     text,
  headline    text,
  followers   text,
  segment     text,          -- founder / eng-leader / engineer / researcher / vendor
  relevance   text,
  first_seen  date,
  status      text NOT NULL DEFAULT 'наблюдаем',
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Черновик: единица работы. Всё, что агент собирается отправить, проходит через эту таблицу.
-- waiting  -> ждёт Романа; approved -> Роман нажал «Отправляй» (относится ТОЛЬКО к этой строке);
-- sent -> отправлено; skipped -> Роман отказался; draft -> агент ещё пишет.
CREATE TABLE IF NOT EXISTS drafts (
  id            bigserial PRIMARY KEY,
  network       text NOT NULL CHECK (network IN ('linkedin','x','reddit')),
  kind          text NOT NULL CHECK (kind IN ('post','comment','reply','thread')),
  status        text NOT NULL DEFAULT 'waiting'
                CHECK (status IN ('draft','waiting','approved','sent','skipped')),
  target_person text REFERENCES people(id),
  target_url    text,        -- куда писать: ссылка на пост или комментарий
  target_note   text,        -- кто это, о чём пост, одной-двумя строками
  body          text NOT NULL,           -- текст на отправку; для thread — твиты через строку ---
  facts         text,        -- откуда факты и что подтверждено (ссылки на код/прод)
  question      text,        -- вопрос к Роману по этому тексту
  created_at    timestamptz NOT NULL DEFAULT now(),
  approved_at   timestamptz,
  sent_at       timestamptz,
  sent_url      text,        -- ссылка на то, что реально ушло
  skip_reason   text
);
CREATE INDEX IF NOT EXISTS drafts_status_idx ON drafts(status);

-- Сообщения: наши (out) и к нам (in).
CREATE TABLE IF NOT EXISTS interactions (
  id           text PRIMARY KEY,
  ts           text NOT NULL,          -- как есть: время бывает приблизительным (~)
  network      text NOT NULL CHECK (network IN ('linkedin','x','reddit')),
  direction    text NOT NULL CHECK (direction IN ('out','in')),
  kind         text NOT NULL,
  person       text,
  post_url     text,
  parent_url   text,
  in_reply_to  text REFERENCES interactions(id),
  text         text,
  draft_id     bigint REFERENCES drafts(id),
  draft_ref    text,                   -- прежняя ссылка на файл черновика (для истории)
  stats        jsonb,
  note         text
);

-- Прямые вопросы рынка: материал для инвесторов.
CREATE TABLE IF NOT EXISTS questions (
  id            text PRIMARY KEY,
  date          date NOT NULL,
  network       text NOT NULL,
  asker         text REFERENCES people(id),
  asker_role    text,
  to_us         boolean NOT NULL DEFAULT false,   -- спросили именно нас
  question      text NOT NULL,
  context       text,
  source        text REFERENCES interactions(id),
  url           text,
  topic         text,
  yatfa_answer  text,                  -- что у нас реально есть, со ссылкой на код/прод
  status        text
);

-- Журнал отправок (замена ledger.md): строка на каждую публикацию.
CREATE TABLE IF NOT EXISTS ledger (
  id          bigserial PRIMARY KEY,
  at          text NOT NULL,
  network     text NOT NULL,
  topic       text NOT NULL,
  url         text,
  outcome     text
);

-- Лиды: воронка знакомств под клиентов. Никаких массовых личок:
-- комментарий в посте человека → коннект с запиской → разговор после реакции.
CREATE TABLE IF NOT EXISTS leads (
  id          bigserial PRIMARY KEY,
  person      text NOT NULL REFERENCES people(id),
  stage       text NOT NULL DEFAULT 'candidate'
              CHECK (stage IN ('candidate','commented','connected','talking','client','dead')),
  source_url  text,
  pain        text,        -- их слова про боль, дословно или близко
  next_step   text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS leads_person_uniq ON leads(person);

-- В drafts добавляются виды: connect (заявка с запиской) и dm (личка после реакции).
ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_kind_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_kind_check
  CHECK (kind IN ('post','comment','reply','thread','connect','dm'));
