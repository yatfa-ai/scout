// Pins the creation-status guard of `soc add` (bin/soc.mjs#commands.add): a draft may be
// created only in draft or waiting, and any other value of --status is refused with the
// value the operator typed on stderr, a non-zero exit and no row written at all.
//
// The regression this suite exists for: the INSERT used to bind the flag raw —
// `insert into drafts (network, kind, status, ...) values ($1,$2,$3,...)` with
// `flag('status', 'waiting')` as $3 and need() guarding only network/kind/body. So
// `soc add … --status approved` minted an approved draft outright, and `soc sent`'s own
// guard is provenance-blind (`d.status !== 'approved'`): the phantom row passed it and
// got a ledger row plus an interactions out-row — a recorded send that Roman never
// approved, while the web «Отправляй» button is the documented sole entry to approved.
// `--status sent` was worse in the other direction: it fabricated send history (a row in
// the «Ушло» feed with sent_at NULL, no sent_url, no ledger row) that `soc sent` then
// refuses to complete, because it is no longer approved.
//
// Nothing in the database enforces this: drafts.status is a plain CHECK over the five
// legal values, and a CHECK admits approved/sent/skipped at INSERT time exactly as it
// admits waiting — it cannot tell a gate-bypassing creation from a legitimate one. There
// is no trigger and no transition constraint, and this is the repo's only INSERT into
// drafts, so this guard is the only enforcement point for the path and is pinned here.
//
// draft stays legal on purpose: schema.sql names it a real creation status
// («draft -> агент ещё пишет»), so the guard constrains the flag rather than removing it.
//
// Runner — built-in node:test, no extra dependencies (the convention of
// test/soc-cli.test.mjs, test/soc-revise-guard.test.mjs and test/lead-cli.test.mjs).
//
// Two kinds of test here. The refusal fires before the first SQL query, so it is pinned
// with a dummy DATABASE_URL and runs everywhere (the guard-before-database property of
// test/soc-cli.test.mjs). What a created row actually stores, and that a refusal writes
// nothing, are claims about rows and need a real Postgres: DSN search order is
// TEST_DATABASE_URL, then the usual local ones; with no live Postgres those are skipped
// honestly.
//
// Run: npm test   (or: node --test test/)
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(testDir, '..');
const socPath = path.join(repoRoot, 'bin', 'soc.mjs');

// The statuses a draft must not be created in: the two that bypass or fabricate the send
// record, the third terminal one, plus values that probe the check itself — the guard
// compares the raw string, so a differently-cased legal value is not a legal value.
const REFUSED = ['approved', 'sent', 'skipped', 'выдумка', 'Approved', 'WAITING', ''];

// --- discoverability and the refusal: no database ---------------------------

const DUMMY_ENV = { ...process.env, DATABASE_URL: 'postgres://dummy@127.0.0.1/dummy' };

const socDummy = (...args) =>
  spawnSync(process.execPath, [socPath, ...args], { env: DUMMY_ENV, timeout: 10_000 });

test('help names the statuses add accepts, so the restriction is discoverable before it refuses', () => {
  const r = socDummy();
  assert.equal(r.status, 0);
  const line = r.stdout.toString().split('\n').find(l => l.trim().startsWith('add '));
  assert.ok(line, 'help should carry an add line');
  for (const status of ['draft', 'waiting']) {
    assert.ok(line.includes(status), `the add help line should name ${status}: ${line}`);
  }
});

test('the usage comment above commands.add names the statuses too, so the source agrees with the help', () => {
  const source = fs.readFileSync(socPath, 'utf8');
  const line = source.split('\n').find(l => l.trim().startsWith('// soc add '));
  assert.ok(line, 'commands.add should keep its usage comment');
  for (const status of ['draft', 'waiting']) {
    assert.ok(line.includes(status), `the add usage comment should name ${status}: ${line}`);
  }
});

for (const status of REFUSED) {
  const shown = status === '' ? '(empty)' : status;
  test(`add --status ${shown} is refused with the value on stderr and exits 3 without reaching the database`, () => {
    const r = socDummy('add', '--network', 'linkedin', '--kind', 'comment', '--body', 'проба', '--status', status);
    assert.equal(r.status, 3, `a refused add must exit 3, got ${r.status}:\n${r.stdout}${r.stderr}`);
    assert.equal(r.stdout.toString(), '', 'a refusal prints nothing on stdout');
    assert.equal(
      r.stderr.toString(),
      `статус «${status}» нельзя задать при создании — черновик создаётся в draft или waiting\n`,
      'the refusal should name the status the operator typed');
  });
}

test('the status guard runs before the --body guard, so a bad status is refused even with nothing to insert', () => {
  const r = socDummy('add', '--network', 'linkedin', '--kind', 'comment', '--status', 'approved');
  assert.equal(r.status, 3, 'the status refusal (exit 3) should win over the usage refusal (exit 2)');
  assert.match(r.stderr.toString(), /нельзя задать при создании/);
});

test('a trailing --status with no value is refused rather than falling back to waiting', () => {
  // makeFlag in lib/cli.mjs returns undefined (not the default) when --name is the last
  // token; the guard must refuse that rather than silently create a waiting draft.
  const r = socDummy('add', '--network', 'linkedin', '--kind', 'comment', '--body', 'проба', '--status');
  assert.equal(r.status, 3, `a valueless --status must be refused, got ${r.status}:\n${r.stdout}${r.stderr}`);
  assert.equal(r.stdout.toString(), '');
  assert.match(r.stderr.toString(), /нельзя задать при создании/);
});

// --- what gets stored, and what a refusal leaves behind: real Postgres -----

const SCRATCH_DB = `scout_soc_add_status_guard_test_${process.pid}`;
const CANDIDATE_DSN = [
  process.env.TEST_DATABASE_URL,
  'postgres://postgres:postgres@127.0.0.1:5432/postgres',
  'postgres://postgres@127.0.0.1:5432/postgres',
  'postgres:///postgres', // socket + peer authentication as the current user
].filter(Boolean);

let adminUrl = null;    // DSN we managed to connect through
let dbUrl = null;       // DSN of this run's scratch database
let adminClient = null; // kept open: create/drop the database

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

const draftCount = async () => Number((await sql('select count(*)::int as n from drafts'))[0].n);

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
});

after(async () => {
  if (!adminUrl) return;
  if (adminClient) {
    await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
    await adminClient.end();
  }
});

const dbNeeded = 'no live local Postgres (set TEST_DATABASE_URL) — row checks skipped';

test('add without --status stores the draft as waiting, exactly as before the guard', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await soc(['add', '--network', 'linkedin', '--kind', 'comment', '--body', 'без флага']);
  assert.equal(r.code, 0, `soc add failed:\n${r.output}`);
  const created = JSON.parse(r.stdout);
  assert.equal(created.status, 'waiting');

  const row = (await sql('select status from drafts where id = $1', [Number(created.id)]))[0];
  assert.equal(row.status, 'waiting', 'the stored status should be waiting, not just the printed one');
});

test('add --status draft stores the draft as draft, so the agent can still compose one', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await soc(['add', '--network', 'x', '--kind', 'post', '--body', 'ещё пишется', '--status', 'draft']);
  assert.equal(r.code, 0, `soc add failed:\n${r.output}`);
  const created = JSON.parse(r.stdout);
  assert.equal(created.status, 'draft');

  const row = (await sql('select status from drafts where id = $1', [Number(created.id)]))[0];
  assert.equal(row.status, 'draft');
});

test('every refused --status writes no row at all', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await draftCount();

  for (const status of REFUSED) {
    const r = await soc(['add', '--network', 'linkedin', '--kind', 'comment', '--body', `проба ${status}`, '--status', status]);
    assert.equal(r.code, 3, `add --status «${status}» must exit 3, got ${r.code}:\n${r.output}`);
  }

  assert.equal(await draftCount(), before, 'a refused add must leave the drafts table untouched');
});

test('after the refusals nothing is waiting to be sent: the approval gate has no unapproved entry', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  // Both reads Roman's send step works from: the console's approved list and the same
  // status filter the web queue lists by.
  const approved = await soc(['approved']);
  assert.equal(approved.code, 0, `soc approved failed:\n${approved.output}`);
  assert.deepEqual(JSON.parse(approved.stdout), [], 'no draft may reach approved without the web button');

  const queued = await soc(['queue', '--status', 'approved']);
  assert.equal(queued.code, 0, `soc queue failed:\n${queued.output}`);
  assert.deepEqual(JSON.parse(queued.stdout), []);

  const minted = await sql(`select id, status from drafts where status in ('approved', 'sent', 'skipped')`);
  assert.deepEqual(minted, [], 'add must not be able to mint an approved, sent or skipped row');
});
