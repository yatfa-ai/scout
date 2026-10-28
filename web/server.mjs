// Веб-интерфейс Романа. Слушает ТОЛЬКО 127.0.0.1. Зависимостей нет, кроме pg.
// Главное правило: кнопка «Отправляй» меняет статус ОДНОЙ строки drafts на approved.
// Сама публикация идёт отдельно (агент, через браузер) и только для approved.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { q, one, root } from '../lib/db.mjs';

const PORT = Number(process.env.PORT || 4177);
const page = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'index.html'), 'utf8');

const json = (res, code, obj) => {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(obj));
};
const readBody = req => new Promise(r => { let s = ''; req.on('data', d => s += d); req.on('end', () => r(s ? JSON.parse(s) : {})); });

const routes = {
  'GET /api/state': async () => ({
    waiting: await q(`select d.*, p.name as person_name from drafts d left join people p on p.id = d.target_person
                      where d.status in ('waiting','approved') order by d.id`),
    sent: await q(`select d.id, d.network, d.kind, d.body, d.sent_at, d.sent_url, d.target_note from drafts d
                   where d.status = 'sent' order by d.sent_at desc limit 50`),
    ledger: await q(`select * from ledger order by id desc limit 80`),
    questions: await q(`select qn.*, p.name as asker_name from questions qn left join people p on p.id = qn.asker order by qn.date desc, qn.id desc`),
    people: await q(`select p.*, (select count(*) from interactions i where i.person = p.id) as n from people p order by p.first_seen desc, p.name`),
    leads: await q(`select l.*, p.name, p.network, p.profile, p.headline from leads l join people p on p.id = l.person
                    order by case l.stage when 'talking' then 0 when 'connected' then 1 when 'commented' then 2
                    when 'candidate' then 3 else 4 end, l.id`),
    inbound: await q(`select i.*, p.name as person_name from interactions i left join people p on p.id = i.person
                      where i.direction = 'in' order by i.ts desc limit 30`),
  }),

  // «Отправляй»: относится строго к одному черновику. Другие не затрагиваются.
  'POST /api/approve': async ({ id }) => one(
    `update drafts set status = 'approved', approved_at = now() where id = $1 and status = 'waiting' returning id, status`, [id]),
  'POST /api/unapprove': async ({ id }) => one(
    `update drafts set status = 'waiting', approved_at = null where id = $1 and status = 'approved' returning id, status`, [id]),
  'POST /api/edit': async ({ id, body }) => one(
    `update drafts set body = $2, status = 'waiting', approved_at = null where id = $1 and status in ('waiting','approved') returning id, status`, [id, body]),
  'POST /api/skip': async ({ id, reason }) => one(
    `update drafts set status = 'skipped', skip_reason = $2 where id = $1 and status in ('waiting','approved') returning id, status`, [id, reason || null]),
};

http.createServer(async (req, res) => {
  try {
    const key = `${req.method} ${req.url.split('?')[0]}`;
    if (key === 'GET /') { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); return res.end(page); }
    if (key === 'GET /chart.png') {
      const f = path.join(root, '..', 'sales-md', 'social', 'assets', 'prs-by-month-yatfa.png');
      if (fs.existsSync(f)) { res.writeHead(200, { 'Content-Type': 'image/png' }); return fs.createReadStream(f).pipe(res); }
    }
    const h = routes[key];
    if (!h) return json(res, 404, { error: 'нет такого маршрута' });
    const result = await h(req.method === 'POST' ? await readBody(req) : {});
    json(res, 200, result ?? { error: 'ничего не изменилось (статус не тот)' });
  } catch (e) {
    json(res, 500, { error: e.message });
  }
}).listen(PORT, '127.0.0.1', () => console.log(`social-ops: http://127.0.0.1:${PORT}`));
