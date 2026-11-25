// Pins the status guard of `soc revise` (bin/soc.mjs#commands.revise): the text of a
// draft may be rewritten only while it is waiting or approved, and a draft that has
// already been sent, skipped, or is still being written is refused with its actual
// status on stderr and a non-zero exit, leaving the row untouched.
//
// The regression this suite exists for: revise used to run
// `update drafts set body = $2, status = 'waiting', approved_at = null where id = $1`
// with no status condition — the only unguarded drafts-status write in the repo. A
// revise on a SENT draft therefore succeeded: status flipped back to waiting and
// approved_at was nulled while sent_at, sent_url, the draft's ledger row and its
// interactions out-row all remained. The draft could then be approved again and
// `soc sent` would pass its own approved-guard a second time, writing a SECOND ledger
// row (bare bigserial PK, nothing dedupes) and — on another date — a second
// interactions out-row, breaking the project's one approval = one send rule.
//
// Nothing in the database enforces this: drafts.status is a plain CHECK over the five
// legal values with no trigger and no transition constraint, and the web guard
// (POST /api/edit, `and status in ('waiting','approved')`) sits on a different surface
// and cannot cover the console. This guard is the only enforcement point for this path,
// so it is pinned here rather than relied upon.
//
// Runner — built-in node:test, no extra dependencies (the convention of
// test/soc-cli.test.mjs, test/import-ledger.test.mjs and test/lead-cli.test.mjs).
//
// The guard needs a real Postgres: it loads the draft before deciding, so a refusal on
// a stored status cannot be proved without a stored row, and «the row is untouched» is
// a claim about the row rather than about stdout. DSN search order: TEST_DATABASE_URL,
// then the usual local ones; with no live Postgres those tests are skipped honestly.
// The help-text test needs no database and runs everywhere.
//
// Every status is seeded in before(), so the tests do not depend on each other's order.
// `approved` and `skipped` are set with SQL because the console has no command that
// produces them (approving is the web surface's job); `sent` is reached the real way —
// SQL-approve, then `soc sent` — so the ledger and interactions rows a resurrect would
// duplicate genuinely exist.
//
// Run: npm test   (or: node --test test/)
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(testDir, '..');
const socPath = path.join(repoRoot, 'bin', 'soc.mjs');

// --- help text: no database -------------------------------------------------

const DUMMY_ENV = { ...process.env, DATABASE_URL: 'postgres://dummy@127.0.0.1/dummy' };

test('help names the statuses revise accepts, so the restriction is discoverable before it refuses', () => {
  const r = spawnSync(process.execPath, [socPath], { env: DUMMY_ENV, timeout: 10_000 });
  assert.equal(r.status, 0);
  const help = r.stdout.toString();
  const line = help.split('\n').find(l => l.includes('revise'));
  assert.ok(line, 'help should carry a revise line');
  for (const status of ['waiting', 'approved']) {
    assert.ok(line.includes(status), `the revise help line should name ${status}: ${line}`);
  }
});

// --- the guard: real Postgres -----------------------------------------------

const SCRATCH_DB = `scout_soc_revise_guard_test_${process.pid}`;
const CANDIDATE_DSN = [
  process.env.TEST_DATABASE_URL,
  'postgres://postgres:postgres@127.0.0.1:5432/postgres',
  'postgres://postgres@127.0.0.1:5432/postgres',
  'postgres:///postgres', // socket + peer authentication as the current user
].filter(Boolean);

let adminUrl = null;    // DSN we managed to connect through
let dbUrl = null;       // DSN of this run's scratch database
let adminClient = null; // kept open: create/drop the database

const ids = {}; // status -> draft id, filled by before()

const SENT_URL = 'https://linkedin.example/posts/sent-one';
const NEW_BODY = 'переписанный текст';

function connectOnce(connectionString) {
  const c = new pg.Client({ connectionString });
  return c.connect().then(() => c).catch(e => { c.end().catch(() => {}); throw e; });
}

async function probeDsn() {
  for (const dsn of CANDIDATE_DSN) {
    try {
      const c = await connectOnce(dsn);
      await c.end();
      return dsn;
    } catch { /* try the next one */ }
  }
  return null;
}

// Run soc against the scratch database. stdout and stderr are kept apart: which stream
// a message goes to is part of the contract above.
function soc(args) {
  return new Promise((resolve, reject) => {
    const c = spawn(process.execPath, [socPath, ...args], {
      env: { ...process.env, DATABASE_URL: dbUrl },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    c.stdout.on('data', d => { stdout += d; });
    c.stderr.on('data', d => { stderr += d; });
    c.on('exit', code => resolve({ code, stdout, stderr, output: stdout + stderr }));
    c.on('error', reject);
  });
}

const sql = async (text, params = []) => {
  const c = new pg.Client({ connectionString: dbUrl });
  try {
    await c.connect();
    return (await c.query(text, params)).rows;
  } finally {
    await c.end();
  }
};

const draft = async id =>
  (await sql('select id, status, body, approved_at, sent_at, sent_url from drafts where id = $1', [id]))[0];

const counts = async () => ({
  ledger: Number((await sql('select count(*)::int as n from ledger'))[0].n),
  interactions: Number((await sql(`select count(*)::int as n from interactions where direction = 'out'`))[0].n),
});

// Create a draft through the console and return its id.
async function addDraft(status, body) {
  const args = ['add', '--network', 'linkedin', '--kind', 'comment', '--body', body, '--status', status];
  const r = await soc(args);
  assert.equal(r.code, 0, `soc add failed:\n${r.output}`);
  return Number(JSON.parse(r.stdout).id);
}

before(async () => {
  adminUrl = await probeDsn();
  if (!adminUrl) return;

  adminClient = await connectOnce(adminUrl);
  await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
  await adminClient.query(`create database ${SCRATCH_DB}`);
  const u = new URL(adminUrl);
  u.pathname = `/${SCRATCH_DB}`;
  dbUrl = u.toString();

  const migrated = await soc(['migrate']);
  assert.equal(migrated.code, 0, `soc migrate failed:\n${migrated.output}`);

  // One draft per status. `add` can only create draft or waiting rows.
  ids.waiting = await addDraft('waiting', 'ожидает утверждения');
  ids.draft = await addDraft('draft', 'ещё пишется');

  // Approving is the web surface's job, so these two statuses are set directly.
  ids.approved = await addDraft('waiting', 'утверждено');
  await sql(`update drafts set status = 'approved', approved_at = now() where id = $1`, [ids.approved]);

  ids.skipped = await addDraft('waiting', 'отклонено');
  await sql(`update drafts set status = 'skipped', skip_reason = 'не сейчас' where id = $1`, [ids.skipped]);

  // The sent row is reached the real way, so its ledger and interactions rows exist.
  ids.sent = await addDraft('waiting', 'уже отправлено');
  await sql(`update drafts set status = 'approved', approved_at = now() where id = $1`, [ids.sent]);
  const sent = await soc(['sent', String(ids.sent), '--url', SENT_URL]);
  assert.equal(sent.code, 0, `soc sent failed:\n${sent.output}`);
});

after(async () => {
  if (!adminUrl) return;
  if (adminClient) {
    await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
    await adminClient.end();
  }
});

const dbNeeded = 'no live local Postgres (set TEST_DATABASE_URL) — guard checks skipped';

test('revise rewrites the body of a waiting draft and leaves it waiting', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await soc(['revise', String(ids.waiting), '--body', NEW_BODY]);
  assert.equal(r.code, 0, `soc revise failed:\n${r.output}`);
  assert.equal(JSON.parse(r.stdout).status, 'waiting');

  const row = await draft(ids.waiting);
  assert.equal(row.body, NEW_BODY, 'the stored body should be the new text');
  assert.equal(row.status, 'waiting');
});

test('revise rewrites an approved draft, returns it to waiting and clears approved_at', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await draft(ids.approved);
  assert.ok(before.approved_at, 'the seeded row should be approved before the revise');

  const r = await soc(['revise', String(ids.approved), '--body', NEW_BODY]);
  assert.equal(r.code, 0, `soc revise failed:\n${r.output}`);

  const row = await draft(ids.approved);
  assert.equal(row.body, NEW_BODY);
  assert.equal(row.status, 'waiting', 'an edited approval must go back to the queue');
  assert.equal(row.approved_at, null, 'the old approval must not survive the edit');
});

test('revise on a sent draft is refused with its status on stderr, exits 3 and changes no column of the row', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await draft(ids.sent);
  assert.equal(before.status, 'sent', 'the seeded row should be sent before the revise');

  const r = await soc(['revise', String(ids.sent), '--body', NEW_BODY]);
  assert.equal(r.code, 3, `a refused revise must exit 3, got ${r.code}:\n${r.output}`);
  assert.equal(r.stdout, '', 'a refusal prints nothing on stdout');
  assert.match(r.stderr, /sent/, 'the refusal should name the status the draft is actually in');
  assert.doesNotMatch(r.stderr, /нет такого черновика/,
    'an existing draft in the wrong status must not be reported as missing');

  assert.deepEqual(await draft(ids.sent), before, 'the refused revise must not change the row');
});

test('a refused revise leaves the sent draft out of the approval queue, so no second send can be recorded from it', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await counts();
  assert.equal(before.ledger, 1, 'the seeded send should have written exactly one ledger row');
  assert.equal(before.interactions, 1, 'the seeded send should have written exactly one out-interaction');

  const refused = await soc(['revise', String(ids.sent), '--body', NEW_BODY]);
  assert.notEqual(refused.code, 0);

  const queue = await soc(['approved']);
  assert.equal(queue.code, 0, `soc approved failed:\n${queue.output}`);
  const approved = JSON.parse(queue.stdout);
  assert.ok(!approved.some(d => Number(d.id) === ids.sent),
    'a sent draft must not reappear among the drafts waiting to be sent');

  // The second send would need the row back in approved; it never gets there.
  const resend = await soc(['sent', String(ids.sent), '--url', SENT_URL]);
  assert.notEqual(resend.code, 0, 'soc sent must still refuse an already sent draft');

  assert.deepEqual(await counts(), before,
    'no second ledger row and no second out-interaction may be produced from one approval');
});

test('revise on a skipped draft is refused and changes no column of the row', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await draft(ids.skipped);

  const r = await soc(['revise', String(ids.skipped), '--body', NEW_BODY]);
  assert.equal(r.code, 3, `a refused revise must exit 3, got ${r.code}:\n${r.output}`);
  assert.match(r.stderr, /skipped/, 'the refusal should name the status the draft is actually in');
  assert.deepEqual(await draft(ids.skipped), before, 'the refused revise must not change the row');
});

test('revise on a draft still being written is refused and changes no column of the row', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await draft(ids.draft);

  const r = await soc(['revise', String(ids.draft), '--body', NEW_BODY]);
  assert.equal(r.code, 3, `a refused revise must exit 3, got ${r.code}:\n${r.output}`);
  assert.match(r.stderr, /draft/, 'the refusal should name the status the draft is actually in');
  assert.deepEqual(await draft(ids.draft), before, 'the refused revise must not change the row');
});

test('revise on an id no draft has prints «нет такого черновика» on stdout and exits 0', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await soc(['revise', '999999', '--body', NEW_BODY]);
  assert.equal(r.code, 0, `a missing draft is not an error:\n${r.output}`);
  assert.equal(r.stdout, 'нет такого черновика\n');
  assert.equal(r.stderr, '', 'the missing-id case must not take the wrong-status branch');
});
