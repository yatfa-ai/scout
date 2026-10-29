// Перенос из sales-md/social/data/*.jsonl в базу. Повторный запуск безопасен.
import fs from 'node:fs';
import path from 'node:path';
import { pool, q, close, root } from '../lib/db.mjs';

const dataDir = path.join(root, '..', 'sales-md', 'social', 'data');
const read = f => fs.readFileSync(path.join(dataDir, f + '.jsonl'), 'utf8').trim().split('\n').map(l => JSON.parse(l));

const cli = await pool.connect();
try {
  await cli.query('begin');

  for (const p of read('people')) {
    await cli.query(
      `insert into people (id,name,network,profile,headline,followers,segment,relevance,first_seen,status)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) on conflict (id) do nothing`,
      [p.id, p.name, p.network, p.profile, p.headline, p.followers, p.segment, p.relevance, p.first_seen, p.status]);
  }

  const inter = read('interactions');
  for (const r of inter) {           // сначала без in_reply_to, чтобы не упереться в порядок
    await cli.query(
      `insert into interactions (id,ts,network,direction,kind,person,post_url,parent_url,text,draft_ref,stats,note)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) on conflict (id) do nothing`,
      [r.id, r.ts, r.network, r.direction, r.kind, r.person, r.post_url, r.parent_url ?? null, r.text, r.draft,
       r.stats ? JSON.stringify(r.stats) : null, r.note ?? null]);
  }
  for (const r of inter.filter(x => x.in_reply_to)) {
    await cli.query(`update interactions set in_reply_to = $2 where id = $1`, [r.id, r.in_reply_to]);
  }

  for (const r of read('questions')) {
    await cli.query(
      `insert into questions (id,date,network,asker,asker_role,to_us,question,context,source,url,topic,yatfa_answer,status)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) on conflict (id) do nothing`,
      [r.id, r.date, r.network, r.asker, r.asker_role, r.to_us, r.question, r.context, r.source, r.url, r.topic, r.yatfa_answer, r.status]);
  }

  // ledger.jsonl: журнал отправок (та же выгрузка, что и у остальных таблиц)
  for (const r of read('ledger')) {
    await cli.query(
      `insert into ledger (id,at,network,topic,url,outcome) values ($1,$2,$3,$4,$5,$6) on conflict (id) do nothing`,
      [r.id, r.at, r.network, r.topic, r.url ?? null, r.outcome ?? null]);
  }
  // явные id последовательность не двигают — выравниваем, чтобы следующий insert из soc sent не упёрся в PK
  await cli.query(`select setval(pg_get_serial_sequence('ledger','id'), coalesce((select max(id) from ledger), 0) + 1, false)`);

  await cli.query('commit');
  const c = await q(`select (select count(*) from people) p, (select count(*) from interactions) i,
                            (select count(*) from questions) qn, (select count(*) from ledger) l`);
  console.log('перенесено:', JSON.stringify(c[0]));
} catch (e) {
  await cli.query('rollback');
  console.error('откат:', e.message);
  process.exitCode = 1;
} finally {
  cli.release();
  await close();
}
