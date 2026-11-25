// Pins the APPROVAL GATE of web/server.mjs — the four POST routes in its `routes`
// table (/api/approve, /api/unapprove, /api/edit, /api/skip). Each of them carries its
// own status precondition inside its SQL `where` clause, and that condition is the only
// enforcement point for the project's core rule, stated in the file's own header: the
// "send" button moves exactly ONE drafts row to approved, and publishing happens only
// for approved rows.
//
// Three properties are pinned here:
//
//   1. the per-route status precondition — approve only from `waiting`, unapprove only
//      from `approved`, edit and skip only from `waiting` or `approved`;
//   2. the no-change envelope — a refused write is NOT an error: `one()` returns null,
//      so the server answers HTTP 200 with { error: 'ничего не изменилось (статус не
//      тот)' }, and the row must be byte-for-byte unchanged;
//   3. exactly one row — the targeted draft changes and nothing else does: no sibling
//      row is swept along, and no row is inserted or deleted.
//
// Why here rather than relied upon: nothing in the database enforces any of this.
// drafts.status is a plain CHECK over the five legal values with no trigger and no
// transition constraint (schema.sql), so a dropped `and status = …` is silently legal
// SQL that changes what the operator's button does. The CLI's writes to drafts.status
// are already pinned (test/soc-add-status-guard.test.mjs, test/soc-revise-guard.test.mjs
// — whose header notes that "POST /api/edit … sits on a different surface"); this suite
// is that surface. test/web-server.test.mjs covers the same five endpoints for
// transport only — registration, content type, the 404 envelope, the readBody parse
// contract — running against a dummy DSN where every POST is a 500 from pg, so no
// example there asserts what a route does to a row. None of that is re-pinned here.
//
// Runner — built-in node:test, no extra dependencies (the convention of every suite in
// this directory).
//
// The gate needs a real Postgres: a refusal on a stored status cannot be proved without
// a stored row, and "the row is untouched" is a claim about the row rather than about
// the response. DSN search order: TEST_DATABASE_URL, then the usual local ones; with no
// live Postgres these tests skip honestly, the convention of
// test/import-ledger.test.mjs and the two soc guard suites.
//
// One draft per status is seeded in before(), plus a second waiting draft used as the
// sibling that must not move. afterEach() restores every seeded row to its baseline, so
// the examples do not depend on each other's order. `approved` and `skipped` are set
// with SQL because the console has no command that produces them (approving is this
// surface's job); `sent` is reached the real way — SQL-approve, then `soc sent` — so the
// ledger and interactions rows a second send would duplicate genuinely exist.
//
// Run: npm test   (or: node --test test/)
import test, { before, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(testDir, '..');
const socPath = path.join(repoRoot, 'bin', 'soc.mjs');
const serverPath = path.join(repoRoot, 'web', 'server.mjs');

// The gate's own answer to a write its status precondition refused. Verbatim from
// web/server.mjs: a refusal is a 200, not an error status.
const NO_CHANGE_ENVELOPE = { error: 'ничего не изменилось (статус не тот)' };

const SCRATCH_DB = `scout_web_gate_test_${process.pid}`;
const CANDIDATE_DSN = [
  process.env.TEST_DATABASE_URL,
  'postgres://postgres:postgres@127.0.0.1:5432/postgres',
  'postgres://postgres@127.0.0.1:5432/postgres',
  'postgres:///postgres', // socket + peer authentication as the current user
].filter(Boolean);

let adminUrl = null;    // DSN we managed to connect through
let dbUrl = null;       // DSN of this run's scratch database
let adminClient = null; // kept open: create/drop the database
let child = null;       // the web server under test
let baseUrl = null;

const ids = {};         // status -> draft id, filled by before()
let baseline = [];      // the seeded rows as before() left them, restored after each test

const SENT_URL = 'https://linkedin.example/posts/sent-one';
const NEW_BODY = 'переписанный оператором текст';
const SKIP_REASON = 'не сейчас — тон не тот';

// --- harness ----------------------------------------------------------------

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

const sql = async (text, params = []) => {
  const c = new pg.Client({ connectionString: dbUrl });
  try {
    await c.connect();
    return (await c.query(text, params)).rows;
  } finally {
    await c.end();
  }
};

// Run soc against the scratch database.
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

async function addDraft(status, body) {
  const r = await soc(['add', '--network', 'linkedin', '--kind', 'comment', '--body', body, '--status', status]);
  assert.equal(r.code, 0, `soc add failed:\n${r.output}`);
  return Number(JSON.parse(r.stdout).id);
}

// Wait for the boot line «scout: http://127.0.0.1:<port>»; fail if the process dies
// first (EADDRINUSE, for instance) — the caller then retries once on another port.
// Copied from test/web-server.test.mjs, which pins the same server's transport.
function boot(port) {
  return new Promise((resolve, reject) => {
    const c = spawn(process.execPath, [serverPath], {
      env: { ...process.env, PORT: String(port), DATABASE_URL: dbUrl },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    let settled = false;
    const fail = err => {
      if (settled) return;
      settled = true;
      c.kill();
      reject(err);
    };
    const timer = setTimeout(
      () => fail(new Error(`web/server.mjs printed no boot line within 10s:\n${out}`)),
      10_000,
    );
    c.stdout.on('data', d => {
      out += d;
      if (settled) return;
      if (out.includes(`http://127.0.0.1:${port}`)) {
        settled = true;
        clearTimeout(timer);
        resolve(c);
      }
    });
    c.stderr.on('data', d => { out += d; });
    c.on('exit', (code, signal) =>
      fail(new Error(`web/server.mjs exited before the boot line (code=${code}, signal=${signal}):\n${out}`)));
  });
}

// AbortSignal keeps every example finite: a route that never answers fails the test
// instead of hanging the suite.
const post = async (urlPath, body) => {
  const res = await fetch(`${baseUrl}${urlPath}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(5_000),
  });
  return { status: res.status, json: JSON.parse(await res.text()) };
};

// Every column the four routes can write, plus the ones a mutant could write by
// accident: a deep-equal over this shape is what "the row is untouched" means.
const DRAFT_COLUMNS = 'id, network, kind, status, body, approved_at, sent_at, sent_url, skip_reason';
const draft = async id => (await sql(`select ${DRAFT_COLUMNS} from drafts where id = $1`, [id]))[0];
const allDrafts = () => sql(`select ${DRAFT_COLUMNS} from drafts order by id`);
const ledgerCount = async () => Number((await sql('select count(*)::int as n from ledger'))[0].n);

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

  // `add` can only create draft or waiting rows.
  ids.waiting = await addDraft('waiting', 'ожидает утверждения');
  ids.sibling = await addDraft('waiting', 'второй, ждёт своей очереди');
  ids.draft = await addDraft('draft', 'ещё пишется');

  ids.approved = await addDraft('waiting', 'утверждено');
  await sql(`update drafts set status = 'approved', approved_at = now() where id = $1`, [ids.approved]);

  ids.skipped = await addDraft('waiting', 'отклонено');
  await sql(`update drafts set status = 'skipped', skip_reason = 'прошлая причина' where id = $1`, [ids.skipped]);

  // The sent row is reached the real way, so its ledger and interactions rows exist.
  ids.sent = await addDraft('waiting', 'уже отправлено');
  await sql(`update drafts set status = 'approved', approved_at = now() where id = $1`, [ids.sent]);
  const sent = await soc(['sent', String(ids.sent), '--url', SENT_URL]);
  assert.equal(sent.code, 0, `soc sent failed:\n${sent.output}`);

  baseline = await allDrafts();

  let port = 40000 + Math.floor(Math.random() * 20000);
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      child = await boot(port);
      break;
    } catch (e) {
      child = null;
      if (attempt === 2 || !String(e.message).includes('EADDRINUSE')) throw e;
      port += 1; // one retry on EADDRINUSE
    }
  }
  baseUrl = `http://127.0.0.1:${port}`;
});

// Put every seeded row back as before() left it, so the examples are order-independent.
afterEach(async () => {
  if (!dbUrl || baseline.length === 0) return;
  for (const row of baseline) {
    await sql(
      `update drafts set status = $2, body = $3, approved_at = $4, sent_at = $5, sent_url = $6, skip_reason = $7
       where id = $1`,
      [row.id, row.status, row.body, row.approved_at, row.sent_at, row.sent_url, row.skip_reason],
    );
  }
});

after(async () => {
  child?.kill();
  if (!adminUrl) return;
  if (adminClient) {
    await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
    await adminClient.end();
  }
});

const dbNeeded = 'no live local Postgres (set TEST_DATABASE_URL) — approval-gate checks skipped';

// --- the gate ---------------------------------------------------------------

// drafts.id is bigserial, and pg serializes bigint as a string, so the success envelope
// carries id as "1" rather than 1. The web UI consumes this value, so it is pinned as
// the string it actually is.
const succeeded = (res, id, status) => {
  assert.equal(res.status, 200, `expected the gate to answer 200, got ${res.status}: ${JSON.stringify(res.json)}`);
  assert.deepEqual(res.json, { id: String(id), status });
};

const refused = (res, where) => {
  assert.equal(res.status, 200, `${where}: a refused write is a 200, not an error status`);
  assert.deepEqual(res.json, NO_CHANGE_ENVELOPE, `${where}: the gate must answer its no-change envelope`);
};

test('approve moves a waiting draft to approved and stamps approved_at', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await draft(ids.waiting);
  assert.equal(before.status, 'waiting');
  assert.equal(before.approved_at, null);

  succeeded(await post('/api/approve', { id: ids.waiting }), ids.waiting, 'approved');

  const row = await draft(ids.waiting);
  assert.equal(row.status, 'approved');
  assert.ok(row.approved_at instanceof Date, `approved_at must be stamped, got ${row.approved_at}`);
  assert.equal(row.body, before.body, 'approving must not touch the text the operator approved');
});

test('approve refuses every status but waiting, answering the no-change envelope and leaving the row untouched',
  { timeout: 60_000 }, async t => {
    if (!dbUrl) return t.skip(dbNeeded);
    for (const status of ['draft', 'skipped', 'sent', 'approved']) {
      const id = ids[status];
      const before = await draft(id);
      assert.equal(before.status, status, `the seeded row should be ${status} before the probe`);

      refused(await post('/api/approve', { id }), `approve on ${status}`);
      assert.deepEqual(await draft(id), before, `approve on a ${status} draft must change no column`);
    }
  });

test('a sent draft cannot be re-approved through the gate, and soc sent still refuses it afterwards',
  { timeout: 60_000 }, async t => {
    if (!dbUrl) return t.skip(dbNeeded);
    const before = await draft(ids.sent);
    const ledgerBefore = await ledgerCount();
    assert.equal(before.status, 'sent');
    assert.equal(ledgerBefore, 1, 'the seeded send should have written exactly one ledger row');

    refused(await post('/api/approve', { id: ids.sent }), 'approve on sent');
    assert.deepEqual(await draft(ids.sent), before, 'the refused approve must not change the row');

    // The second send would need the row back in approved; the gate never puts it there.
    const resend = await soc(['sent', String(ids.sent), '--url', SENT_URL]);
    assert.notEqual(resend.code, 0, 'soc sent must still refuse an already sent draft');
    assert.equal(await ledgerCount(), ledgerBefore,
      'no second ledger row may be produced from one approval');
  });

test('edit de-approves: the body is rewritten, the status returns to waiting and approved_at is cleared',
  { timeout: 60_000 }, async t => {
    if (!dbUrl) return t.skip(dbNeeded);
    const before = await draft(ids.approved);
    assert.ok(before.approved_at, 'the seeded row should be approved before the edit');

    succeeded(await post('/api/edit', { id: ids.approved, body: NEW_BODY }), ids.approved, 'waiting');

    const row = await draft(ids.approved);
    assert.equal(row.body, NEW_BODY);
    // The operator must not be able to approve text A and have text B sent.
    assert.equal(row.status, 'waiting', 'an edited approval must go back to the queue');
    assert.equal(row.approved_at, null, 'the old approval must not survive the edit');
  });

test('edit and skip both refuse a sent draft: the send record can be neither rewritten nor hidden',
  { timeout: 60_000 }, async t => {
    if (!dbUrl) return t.skip(dbNeeded);
    const before = await draft(ids.sent);

    refused(await post('/api/edit', { id: ids.sent, body: NEW_BODY }), 'edit on sent');
    assert.deepEqual(await draft(ids.sent), before, 'a refused edit must change no column');

    refused(await post('/api/skip', { id: ids.sent, reason: SKIP_REASON }), 'skip on sent');
    assert.deepEqual(await draft(ids.sent), before, 'a refused skip must change no column');
  });

test('unapprove returns an approved draft to waiting and refuses every other status',
  { timeout: 60_000 }, async t => {
    if (!dbUrl) return t.skip(dbNeeded);
    succeeded(await post('/api/unapprove', { id: ids.approved }), ids.approved, 'waiting');
    const row = await draft(ids.approved);
    assert.equal(row.status, 'waiting');
    assert.equal(row.approved_at, null, 'unapprove must clear the approval stamp');

    for (const status of ['draft', 'skipped', 'sent']) {
      const id = ids[status];
      const before = await draft(id);
      refused(await post('/api/unapprove', { id }), `unapprove on ${status}`);
      assert.deepEqual(await draft(id), before, `unapprove on a ${status} draft must change no column`);
    }
  });

test('skip hides a waiting draft and stores the reason it was given', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  succeeded(await post('/api/skip', { id: ids.waiting, reason: SKIP_REASON }), ids.waiting, 'skipped');
  const row = await draft(ids.waiting);
  assert.equal(row.status, 'skipped');
  assert.equal(row.skip_reason, SKIP_REASON, 'the reason the operator gave must be stored, not dropped');

  // Omitted reason is stored as null rather than as an empty string.
  succeeded(await post('/api/skip', { id: ids.sibling }), ids.sibling, 'skipped');
  assert.equal((await draft(ids.sibling)).skip_reason, null);
});

test('approve changes exactly one row: a sibling waiting draft is not swept along and no row is added or removed',
  { timeout: 60_000 }, async t => {
    if (!dbUrl) return t.skip(dbNeeded);
    const before = await allDrafts();
    assert.ok(before.length >= 2, 'the snapshot needs at least the target and its sibling');
    assert.equal(before.find(r => Number(r.id) === ids.sibling).status, 'waiting',
      'the sibling must be waiting too, so a mass approve would move it as well');

    succeeded(await post('/api/approve', { id: ids.waiting }), ids.waiting, 'approved');

    const snapshot = await allDrafts();
    assert.equal(snapshot.length, before.length, 'the approve must not insert or delete rows');
    assert.deepEqual(snapshot.map(r => String(r.id)), before.map(r => String(r.id)),
      'the set of drafts must be the same');

    for (const [i, row] of snapshot.entries()) {
      const was = before[i];
      if (Number(row.id) === ids.waiting) {
        assert.equal(row.status, 'approved');
        assert.ok(row.approved_at instanceof Date);
        continue;
      }
      assert.deepEqual(row, was, `draft ${row.id} must be untouched by an approve of draft ${ids.waiting}`);
    }
  });
