// Restore from JSONL backups (people, interactions, questions, ledger) written by `soc export`.
// Reads SCOUT_DATA_DIR (default ./data) or --dir <path>. Safe to re-run: existing rows are kept.
import fs from 'node:fs';
import path from 'node:path';
import { pool, q, close, dataDir } from '../lib/db.mjs';

const i = process.argv.indexOf('--dir');
const dir = i >= 0 ? process.argv[i + 1] : dataDir;

const read = f => {
  const file = path.join(dir, f + '.jsonl');
  if (!fs.existsSync(file)) { console.error(`нет файла ${file}, пропускаю`); return []; }
  const text = fs.readFileSync(file, 'utf8').trim();
  return text ? text.split('\n').map(l => JSON.parse(l)) : [];
};

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
  for (const r of inter) {           // insert without in_reply_to first, so row order does not matter
    await cli.query(
      `insert into interactions (id,ts,network,direction,kind,person,post_url,parent_url,text,draft_ref,stats,note)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) on conflict (id) do nothing`,
      [r.id, r.ts, r.network, r.direction, r.kind, r.person, r.post_url, r.parent_url ?? null, r.text,
       r.draft_ref ?? r.draft ?? null, r.stats ? JSON.stringify(r.stats) : null, r.note ?? null]);
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

  // ledger rows keep their ids; the serial sequence is moved past them so the next `soc sent` does not collide
  for (const r of read('ledger')) {
    await cli.query(
      `insert into ledger (id,at,network,topic,url,outcome) values ($1,$2,$3,$4,$5,$6) on conflict (id) do nothing`,
      [r.id, r.at, r.network, r.topic, r.url, r.outcome]);
  }
  await cli.query(`select setval(pg_get_serial_sequence('ledger','id'), max(id)) from ledger having max(id) is not null`);

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
