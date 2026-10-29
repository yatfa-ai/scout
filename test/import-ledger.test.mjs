// Пины контракт восстановления ledger в bin/import.mjs. До фикса журнал отправок
// восстанавливался из легаси sales-md/social/ledger.md с `delete from ledger` —
// повторный запуск стирал всё, что записал `soc sent`, а экспорт (bin/soc.mjs export)
// писал data/ledger.jsonl, который не читал никто.
//
// Runner — встроенный node:test, ноль новых зависимостей (соглашение test/web-server.test.mjs).
//
// Здесь нужна настоящая база: поведение восстановления без Postgres не проверишь.
// Порядок поиска: TEST_DATABASE_URL, затем типовые локальные DSN. Если живого
// Postgres нет, DB-тесты честно пропускаются (skip), статический тест выполняется всюду.

import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.join(testDir, '..');
const importPath = path.join(repoRoot, 'bin', 'import.mjs');
const socPath = path.join(repoRoot, 'bin', 'soc.mjs');

// Тот же каталог, что жёстко прописан в import.mjs и soc.mjs export (root из lib/db.mjs) —
// dummy-DSN только чтобы модуль загрузился: все соединения теста идут с явными строками.
process.env.DATABASE_URL ||= 'postgres://dummy@127.0.0.1/dummy';
const { root } = await import('../lib/db.mjs');
const salesMdDir = path.join(root, '..', 'sales-md');
const seedDir = path.join(salesMdDir, 'social', 'data');

const SCRATCH_DB = `scout_import_ledger_test_${process.pid}`;
const CANDIDATE_DSN = [
  process.env.TEST_DATABASE_URL,
  'postgres://postgres:postgres@127.0.0.1:5432/postgres',
  'postgres://postgres@127.0.0.1:5432/postgres',
  'postgres:///postgres', // сокет + peer-аутентификация под текущим пользователем
].filter(Boolean);

let adminUrl = null;      // DSN, через который удалось подключиться
let dbUrl = null;         // DSN черновой базы этого прогона
let adminClient = null;   // держим открытым: create/drop базы
let seedDirPreExisted = false;

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
    } catch { /* пробуем следующий */ }
  }
  return null;
}

// Запуск CLI (soc/import) с DATABASE_URL черновой базы.
function runCli(script, args) {
  return new Promise((resolve, reject) => {
    const c = spawn(process.execPath, [script, ...args], {
      env: { ...process.env, DATABASE_URL: dbUrl },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    c.stdout.on('data', d => { out += d; });
    c.stderr.on('data', d => { out += d; });
    c.on('exit', code => resolve({ code, output: out }));
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

const ledgerRows = () => sql('select id, at, network, topic, url, outcome from ledger order by id');

// Живой сценарий публикации: approved-черновик -> soc sent -> строка в ledger (+ interaction).
async function publishOne(note) {
  const added = await runCli(socPath, ['add', '--network', 'linkedin', '--kind', 'comment',
    '--url', 'https://example.com/post', '--note', note, '--body', 'текст черновика', '--status', 'approved']);
  assert.equal(added.code, 0, `soc add упал:\n${added.output}`);
  const draftId = JSON.parse(added.output).id;
  const sent = await runCli(socPath, ['sent', String(draftId), '--url', 'https://example.com/sent', '--outcome', 'ответ получен']);
  assert.equal(sent.code, 0, `soc sent упал:\n${sent.output}`);
  return JSON.parse(sent.output);
}

before(async () => {
  // Статический тест не требует базы — но before() нужен и ему, поэтому молча выходим.
  adminUrl = await probeDsn();
  if (!adminUrl) return;

  adminClient = await connectOnce(adminUrl);
  await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
  await adminClient.query(`create database ${SCRATCH_DB}`);
  dbUrl = new URL(adminUrl);
  dbUrl.pathname = `/${SCRATCH_DB}`;
  dbUrl = dbUrl.toString();

  const migrated = await runCli(socPath, ['migrate']);
  assert.equal(migrated.code, 0, `soc migrate упал:\n${migrated.output}`);

  seedDirPreExisted = fs.existsSync(seedDir);
  if (!seedDirPreExisted) fs.mkdirSync(seedDir, { recursive: true });

  // Базовые строки справочных таблиц: import читает все четыре файла, пустой jsonl он
  // не переживает, поэтому сеем через сам soc и выгружаем штатным export.
  for (const [script, args] of [
    [socPath, ['person', '--id', 'p-test', '--name', 'Тест', '--network', 'linkedin', '--segment', 'founder']],
    [socPath, ['inbound', '--network', 'linkedin', '--person', 'p-test', '--text', 'входящее', '--url', 'https://example.com/in']],
    [socPath, ['question', '--network', 'linkedin', '--asker', 'p-test', '--question', 'вопрос?', '--to-us', 'true', '--topic', 'тест']],
  ]) {
    const r = await runCli(script, args);
    assert.equal(r.code, 0, `${path.basename(script)} ${args[0]} упал:\n${r.output}`);
  }
});

after(async () => {
  if (!adminUrl) return;
  if (!seedDirPreExisted) {
    // Весь sales-md в этом прогоне создали мы — убираем целиком.
    fs.rmSync(salesMdDir, { recursive: true, force: true });
  }
  if (adminClient) {
    await adminClient.query(`drop database if exists ${SCRATCH_DB} with (force)`);
    await adminClient.end();
  }
});

const dbNeeded = 'живой локальный Postgres недоступен (задайте TEST_DATABASE_URL) — поведенческие проверки пропущены';
// skip-опция node:test вычисляется при регистрации модуля — до before(), поэтому
// пропуск живой базы проверяется в теле теста (t.skip), а не в опциях.

test('bin/import.mjs больше не читает ledger.md и не содержит delete from ledger', () => {
  const src = fs.readFileSync(importPath, 'utf8');
  assert.ok(!src.includes('ledger.md'), 'в import.mjs осталась ссылка на ledger.md');
  assert.ok(!/delete\s+from\s+ledger/i.test(src), 'в import.mjs остался разрушительный delete from ledger');
});

test('восстановление сохраняет журнал отправок: export -> чистая база -> import возвращает те же строки с теми же id', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  await publishOne('первая публикация');
  const before = await ledgerRows();
  assert.equal(before.length, 1);

  const exported = await runCli(socPath, ['export', '--dir', seedDir]);
  assert.equal(exported.code, 0, `soc export упал:\n${exported.output}`);

  // Потеря данных: таблицы пусты (restore-в-пустую), схема и последовательности сброшены.
  await sql('truncate people, drafts, interactions, questions, ledger restart identity cascade');

  const imported = await runCli(importPath, []);
  assert.equal(imported.code, 0, `import упал:\n${imported.output}`);

  const restored = await ledgerRows();
  assert.deepEqual(restored, before, 'восстановленный ledger отличается от экспортированного');
});

test('после восстановления в пустую базу soc sent вставляет без коллизии PK: id = max(id) + 1', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  // Предыдущий тест оставил базу после restore: строка id=1 вставлена явным id,
  // а truncate сбросил последовательность в 1 — без setval этот sent упал бы на PK.
  await publishOne('вторая публикация');
  const rows = await ledgerRows();
  assert.equal(rows.length, 2);
  assert.equal(String(rows[1].id), '2',
    `новая строка ledger должна получить id 2 (= max(id) + 1), получено ${rows[1]?.id}`);
});

test('живая строка ledger, которой нет в устаревшей выгрузке, переживает повторный import', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  // id=2 из прошлого теста появился ПОСЛЕ последнего export — в ledger.jsonl его нет.
  const before = await ledgerRows();
  const imported = await runCli(importPath, []);
  assert.equal(imported.code, 0, `import упал:\n${imported.output}`);
  const afterRows = await ledgerRows();
  assert.deepEqual(afterRows, before, 'повторный import изменил содержимое ledger');
});

test('двойной запуск import оставляет ledger без изменений (идемпотентность)', { timeout: 60_000 }, async t => {
  if (!dbUrl) return t.skip(dbNeeded);
  const first = await runCli(importPath, []);
  assert.equal(first.code, 0, `первый import упал:\n${first.output}`);
  const snapshot = await ledgerRows();
  const second = await runCli(importPath, []);
  assert.equal(second.code, 0, `второй import упал:\n${second.output}`);
  assert.deepEqual(await ledgerRows(), snapshot, 'второй запуск import изменил содержимое ledger');
});
