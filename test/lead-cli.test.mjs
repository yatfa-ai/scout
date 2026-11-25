// Pins the operator contract of bin/lead.mjs — the lead funnel console: the
// `add` command binds the VALUE of --person (not the flag name), a missing
// --person is refused before any SQL runs, every failure prints the
// «ошибка: <message>» line with a non-zero exit instead of an unhandled
// rejection stack, and `list` / `stage` / the upsert leg keep their behaviour.
//
// The first property is the regression this suite exists for: `add` used to
// pass the literal string '--person' to the INSERT, so every lead insert
// violated the people foreign key and the lead funnel had no working creation
// path at all. The stored row is read back from the database instead of being
// trusted from stdout: what the funnel is for is the row, and the foreign key
// that made the old bug loud is a database-level backstop rather than a
// guarantee of this CLI — without it the literal would have been stored
// quietly.
//
// Runner — built-in node:test, no extra dependencies (the convention of
// test/soc-cli.test.mjs and test/import-ledger.test.mjs).
//
// Two harnesses, deliberately:
//   * the usage refusals run with a dummy DATABASE_URL (env wins over .env in
//     lib/db.mjs#loadEnv, so no developer .env is read). The dummy DSN is
//     deterministic in all worlds — no database yields ECONNREFUSED, a live one
//     rejects the `dummy` login — so a command that reached the database could
//     not produce these clean refusals; that is what pins «before any SQL».
//   * the funnel behaviour needs a real Postgres: a lead row that exists cannot
//     be proved without one. DSN search order: TEST_DATABASE_URL, then the
//     usual local ones; with no live Postgres those tests are skipped honestly.
//
// The funnel tests share one scratch database and run in file order (add ->
// list -> upsert -> stage -> refusals), so each reads the state the previous
// one left; the scratch database is created in before() and dropped in after().
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
const leadPath = path.join(repoRoot, 'bin', 'lead.mjs');
const socPath = path.join(repoRoot, 'bin', 'soc.mjs');

const PERSON_ID = 'p-lead-test';
const MISSING_PERSON_ID = 'p-does-not-exist';

// --- usage refusals: no database -------------------------------------------

const DUMMY_ENV = { ...process.env, DATABASE_URL: 'postgres://dummy@127.0.0.1/dummy' };

const leadWithoutDb = (...args) =>
  spawnSync(process.execPath, [leadPath, ...args], { env: DUMMY_ENV, timeout: 10_000 });

test('bare invocation prints the usage line on stdout and exits 0', () => {
  const r = leadWithoutDb();
  assert.equal(r.status, 0);
  assert.equal(r.stderr.toString(), '');
  const usage = r.stdout.toString();
  for (const name of ['list', 'add', 'stage', '--person']) {
    assert.ok(usage.includes(name), `usage should mention ${name}`);
  }
});

test('add without --person refuses with «ошибка: нужно: --person» on stderr and exits non-zero without reaching the database', () => {
  const r = leadWithoutDb('add', '--stage', 'talking');
  assert.notEqual(r.status, 0, 'a usage refusal must not exit 0');
  assert.equal(r.stderr.toString(), 'ошибка: нужно: --person\n');
  assert.equal(r.stdout.toString(), '');
});

test('stage without an id refuses with «ошибка: нужно: id лида» on stderr and exits non-zero without reaching the database', () => {
  const r = leadWithoutDb('stage');
  assert.notEqual(r.status, 0, 'a usage refusal must not exit 0');
  assert.equal(r.stderr.toString(), 'ошибка: нужно: id лида\n');
  assert.equal(r.stdout.toString(), '');
});

// --- funnel behaviour: real Postgres ---------------------------------------

const SCRATCH_DB = `scout_lead_cli_test_${process.pid}`;
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

// Run a CLI (lead/soc) against the scratch database. stdout and stderr are kept
// apart: which stream a message goes to is part of the contract above.
function runCli(script, args) {
  return new Promise((resolve, reject) => {
    const c = spawn(process.execPath, [script, ...args], {
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

const lead = args => runCli(leadPath, args);

const sql = async (text, params = []) => {
  const c = new pg.Client({ connectionString: dbUrl });
  try {
    await c.connect();
    return (await c.query(text, params)).rows;
  } finally {
    await c.end();
  }
};

const leadRows = () => sql('select id, person, stage, pain, next_step from leads order by id');

before(async () => {
  adminUrl = await probeDsn();
  if (!adminUrl) return;

  adminClient = await connectOnce(adminUrl);
  await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
  await adminClient.query(`create database ${SCRATCH_DB}`);
  const u = new URL(adminUrl);
  u.pathname = `/${SCRATCH_DB}`;
  dbUrl = u.toString();

  const migrated = await runCli(socPath, ['migrate']);
  assert.equal(migrated.code, 0, `soc migrate failed:\n${migrated.output}`);

  // leads.person references people(id), so the funnel needs a contact first.
  const seeded = await runCli(socPath,
    ['person', '--id', PERSON_ID, '--name', 'Тест', '--network', 'linkedin', '--segment', 'founder']);
  assert.equal(seeded.code, 0, `soc person failed:\n${seeded.output}`);
});

after(async () => {
  if (!adminUrl) return;
  if (adminClient) {
    await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
    await adminClient.end();
  }
});

const dbNeeded = 'no live local Postgres (set TEST_DATABASE_URL) — funnel checks skipped';

test('add stores the --person flag VALUE, not the literal flag name', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await lead(['add', '--person', PERSON_ID, '--stage', 'talking', '--pain', 'дорого', '--next', 'написать']);
  assert.equal(r.code, 0, `lead add failed:\n${r.output}`);

  const rows = await leadRows();
  assert.equal(rows.length, 1, 'add should have inserted exactly one lead');
  assert.equal(rows[0].person, PERSON_ID,
    `the lead must point at the person given on the command line, got ${JSON.stringify(rows[0].person)}`);
  assert.equal(rows[0].stage, 'talking');
  assert.equal(rows[0].pain, 'дорого');
  assert.equal(rows[0].next_step, 'написать');
  assert.equal(JSON.parse(r.stdout).person, PERSON_ID, 'the printed row should name the same person');
});

test('list shows the stored lead with its stage and the person it belongs to', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await lead(['list']);
  assert.equal(r.code, 0, `lead list failed:\n${r.output}`);
  const listed = JSON.parse(r.stdout);
  assert.equal(listed.length, 1);
  assert.equal(listed[0].stage, 'talking');
  assert.equal(listed[0].name, 'Тест', 'list joins people, so the contact name should come back');
});

test('add for the same person updates the existing lead instead of inserting a second one (upsert)', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const r = await lead(['add', '--person', PERSON_ID, '--stage', 'connected']);
  assert.equal(r.code, 0, `lead add failed:\n${r.output}`);

  const rows = await leadRows();
  assert.equal(rows.length, 1, 'leads.person is unique: the second add must not insert a row');
  assert.equal(rows[0].stage, 'connected', 'the second add should move the stage');
  assert.equal(rows[0].pain, 'дорого', 'omitted --pain must keep the stored value');
});

test('stage moves the lead to another stage', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const [existing] = await leadRows();
  const r = await lead(['stage', String(existing.id), '--stage', 'client']);
  assert.equal(r.code, 0, `lead stage failed:\n${r.output}`);
  assert.equal(JSON.parse(r.stdout).stage, 'client');

  const rows = await leadRows();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].stage, 'client');
});

test('add without --person touches no rows on a live database', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await leadRows();
  const r = await lead(['add', '--stage', 'talking']);
  assert.notEqual(r.code, 0, 'a usage refusal must not exit 0');
  assert.equal(r.stderr, 'ошибка: нужно: --person\n');
  assert.deepEqual(await leadRows(), before, 'the refused add must not change the leads table');
});

test('add for an unknown person prints one clean «ошибка:» line (no stack) and inserts nothing', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const before = await leadRows();
  const r = await lead(['add', '--person', MISSING_PERSON_ID, '--stage', 'talking']);
  assert.notEqual(r.code, 0, 'a foreign-key failure must not exit 0');
  assert.match(r.stderr, /^ошибка: .*leads_person_fkey/m,
    `the foreign-key failure should be reported as an «ошибка:» line, got:\n${r.stderr}`);
  assert.doesNotMatch(r.stderr, /^\s+at /m, 'the failure must not print a stack trace');
  assert.doesNotMatch(r.stderr, /unhandled|UnhandledPromiseRejection/i,
    'the failure must be caught, not surface as an unhandled rejection');
  assert.deepEqual(await leadRows(), before, 'the failed add must not change the leads table');
});
