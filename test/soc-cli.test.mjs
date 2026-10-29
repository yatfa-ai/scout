// Pins the operator contract of bin/soc.mjs — the console every draft flows
// through: command dispatch (bare invocation falls back to help; an unknown
// command is refused), the exit codes this suite pins (0 success, 2 usage
// refusals) and the guard-before-database property: every usage refusal below
// fires before the first SQL query.
//
// The harness runs with a dummy DATABASE_URL (env wins over .env in
// lib/db.mjs#loadEnv, so no developer .env is read). The dummy URL is
// deterministic in all worlds — no database yields ECONNREFUSED, a live one
// rejects the `dummy` login — so any command that reached the database could
// no longer produce these clean exit-2 usage refusals; it would end on the
// failed connection instead (measured: it can wedge in pool teardown).
//
// Run: node --test test/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const SOC = fileURLToPath(new URL('../bin/soc.mjs', import.meta.url));
const ENV = { ...process.env, DATABASE_URL: 'postgres://dummy@127.0.0.1/dummy' };

const soc = (...args) =>
  spawnSync(process.execPath, [SOC, ...args], { env: ENV, timeout: 10_000 });

const COMMAND_NAMES = [
  'migrate', 'queue', 'show', 'add', 'revise', 'approved',
  'sent', 'inbound', 'question', 'person', 'export',
];

test('bare invocation prints help (all command names) on stdout and exits 0', () => {
  const r = soc();
  assert.equal(r.status, 0);
  assert.equal(r.stderr.toString(), '');
  const help = r.stdout.toString();
  for (const name of COMMAND_NAMES) {
    assert.ok(help.includes(name), `help should mention the ${name} command`);
  }
});

test('unknown command prints «неизвестная команда: X» on stderr, help on stdout, exits 2', () => {
  const r = soc('frobnicate');
  assert.equal(r.status, 2);
  assert.match(r.stderr.toString(), /^неизвестная команда: frobnicate/m);
  assert.ok(r.stdout.toString().includes('soc <команда>'), 'help should still print on stdout');
});

test('soc sent without an id refuses with «нужно: id» on stderr and exits 2 without reaching the database', () => {
  const r = soc('sent');
  assert.equal(r.status, 2);
  assert.equal(r.stderr.toString(), 'нужно: id\n');
  assert.equal(r.stdout.toString(), '');
});

test('soc show without an id refuses with «нужно: id черновика» on stderr and exits 2 without reaching the database', () => {
  const r = soc('show');
  assert.equal(r.status, 2);
  assert.equal(r.stderr.toString(), 'нужно: id черновика\n');
  assert.equal(r.stdout.toString(), '');
});

test('soc add without --body or --body-file refuses with «нужно: --body или --body-file» and exits 2 without reaching the database', () => {
  const r = soc('add', '--network', 'linkedin', '--kind', 'comment');
  assert.equal(r.status, 2);
  assert.equal(r.stderr.toString(), 'нужно: --body или --body-file\n');
  assert.equal(r.stdout.toString(), '');
});

test('soc inbound without --network refuses with «нужно: --network» and exits 2 without reaching the database', () => {
  const r = soc('inbound');
  assert.equal(r.status, 2);
  assert.equal(r.stderr.toString(), 'нужно: --network\n');
  assert.equal(r.stdout.toString(), '');
});
