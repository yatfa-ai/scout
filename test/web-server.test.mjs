// Пины HTTP-контракта web/server.mjs — поведенческого эталона, который Django-версия
// (v2) обязана повторить 1:1. Первый тест-сьют репозитория: runner — встроенный
// node:test, ноль новых зависимостей.
//
// DB-free by design: сервер стартует как child process с фиктивным DATABASE_URL
// на эфемерном порту, и любой маршрут, дошедший до Postgres, детерминированно
// отвечает 500 JSON во всех трёх мирах — без базы, с базой без схемы и с целой
// базой (pg отвергает не-integer bigint bind). См. boot recipe в knowledge scout-1.

import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const serverPath = path.join(testDir, '..', 'web', 'server.mjs');
const UNKNOWN_ROUTE_ENVELOPE = { error: 'нет такого маршрута' };

// Тот же путь, что сервер считает для /chart.png (root из lib/db.mjs), — без дублирования литерала.
process.env.DATABASE_URL ||= 'postgres://dummy@127.0.0.1/dummy';
const { root } = await import('../lib/db.mjs');
const chartPath = path.join(root, '..', 'sales-md', 'social', 'assets', 'prs-by-month-yatfa.png');

let child;
let baseUrl;

// Ждём строку загрузки «social-ops: http://127.0.0.1:<port>»; падаем, если процесс
// умер раньше (например, EADDRINUSE) — тогда вызывающий код делает один повтор.
function boot(port) {
  return new Promise((resolve, reject) => {
    const c = spawn(process.execPath, [serverPath], {
      env: { ...process.env, PORT: String(port), DATABASE_URL: 'postgres://dummy@127.0.0.1/dummy' },
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
      () => fail(new Error(`web/server.mjs не напечатал строку загрузки за 10с:\n${out}`)),
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
      fail(new Error(`web/server.mjs завершился до строки загрузки (code=${code}, signal=${signal}):\n${out}`)));
  });
}

before(async () => {
  let port = 40000 + Math.floor(Math.random() * 20000);
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      child = await boot(port);
      break;
    } catch (e) {
      child = undefined;
      if (attempt === 2 || !String(e.message).includes('EADDRINUSE')) throw e;
      port += 1; // один повтор при EADDRINUSE
    }
  }
  baseUrl = `http://127.0.0.1:${port}`;
});

after(() => { child?.kill(); });

const request = async (method, urlPath, body) => {
  const res = await fetch(`${baseUrl}${urlPath}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return {
    status: res.status,
    contentType: res.headers.get('content-type') || '',
    cacheControl: res.headers.get('cache-control') || '',
    text: await res.text(),
  };
};
const isJson = r => r.contentType.startsWith('application/json');

test('GET / отвечает страницей интерфейса (200, text/html)', async () => {
  const r = await request('GET', '/');
  assert.equal(r.status, 200);
  assert.ok(r.contentType.startsWith('text/html'), `content-type: ${r.contentType}`);
  assert.ok(r.text.length > 0, 'страница не пуста');
});

test('неизвестный маршрут отвечает 404-конвертом { error: «нет такого маршрута» } с Cache-Control: no-store', async () => {
  const r = await request('GET', '/definitely-not-a-route');
  assert.equal(r.status, 404);
  assert.ok(isJson(r), `content-type: ${r.contentType}`);
  assert.deepEqual(JSON.parse(r.text), UNKNOWN_ROUTE_ENVELOPE);
  // no-store держит тексты черновиков и состояние утверждения вне промежуточных кэшей.
  assert.equal(r.cacheControl, 'no-store');
});

test('GET /chart.png отвечает обычным 404 (не картинкой), пока соседний график отсутствует', {
  skip: fs.existsSync(chartPath)
    ? 'соседний график sales-md есть на этой машине — пин отсутствия неприменим'
    : false,
}, async () => {
  const r = await request('GET', '/chart.png');
  assert.equal(r.status, 404);
  assert.ok(!r.contentType.startsWith('image/png'), `content-type: ${r.contentType}`);
});

test('пять паритетных эндпоинтов зарегистрированы: каждый отвечает JSON и никогда — 404-конвертом неизвестного маршрута', async () => {
  const probes = [
    ['GET', '/api/state', undefined],
    ['POST', '/api/approve', { id: 1 }],
    ['POST', '/api/unapprove', { id: 1 }],
    ['POST', '/api/edit', { id: 1, body: 'probe' }],
    ['POST', '/api/skip', { id: 1, reason: 'probe' }],
  ];
  for (const [method, url, body] of probes) {
    const r = await request(method, url, body);
    assert.ok(isJson(r), `${method} ${url}: content-type ${r.contentType}`);
    assert.notEqual(r.status, 404, `${method} ${url}: попал в 404 неизвестного маршрута`);
    assert.notDeepEqual(safeJson(r.text), UNKNOWN_ROUTE_ENVELOPE, `${method} ${url}`);
  }
});

test('падающий запрос возвращает 500-конверт JSON и не валит сервер', async () => {
  // Валидный JSON с не-integer id: pg отвергает его детерминированно
  // (без базы — ECONNREFUSED, с базой — invalid input syntax для bigint).
  const r = await request('POST', '/api/approve', { id: 'не-число' });
  assert.equal(r.status, 500);
  assert.ok(isJson(r), `content-type: ${r.contentType}`);
  const body = safeJson(r.text);
  assert.ok(typeof body.error === 'string' && body.error.length > 0, `тело: ${r.text}`);
  // Сервер пережил запрос: следующий запрос получает настоящий HTTP-ответ.
  const next = await request('GET', '/definitely-not-a-route');
  assert.equal(next.status, 404);
  assert.deepEqual(JSON.parse(next.text), UNKNOWN_ROUTE_ENVELOPE);
});

function safeJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}
