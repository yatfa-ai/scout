#!/usr/bin/env node
// soc — консоль агента. Читает очередь, записывает итоги. НИЧЕГО не публикует в сети:
// отправку делает агент через браузер только после статуса approved у конкретной строки.
import fs from 'node:fs';
import path from 'node:path';
import { q, one, close, root } from '../lib/db.mjs';
import { makeFlag, makeGuards } from '../lib/cli.mjs';

const [cmd, ...rest] = process.argv.slice(2);
const flag = makeFlag(rest);
const { need, out } = makeGuards('exit');

const commands = {
  async migrate() {
    const sql = fs.readFileSync(path.join(root, 'schema.sql'), 'utf8');
    await q(sql);
    out('схема применена');
  },

  // soc queue [--status waiting|approved|sent|skipped|draft|all]
  async queue() {
    const st = flag('status', 'waiting');
    const rows = await q(
      `select id, network, kind, status, target_url, left(body, 90) as body, created_at
         from drafts where ($1 = 'all' or status = $1) order by id`, [st]);
    out(rows);
  },

  // soc show <id>
  async show() {
    out(await one('select * from drafts where id = $1', [need(rest[0], 'id черновика')]));
  },

  // soc add --network linkedin --kind comment --url ... --note ... --body-file f.txt [--facts ...] [--question ...] [--person id]
  async add() {
    const bodyFile = flag('body-file');
    const body = bodyFile ? fs.readFileSync(bodyFile, 'utf8').trim() : need(flag('body'), '--body или --body-file');
    const row = await one(
      `insert into drafts (network, kind, status, target_person, target_url, target_note, body, facts, question)
       values ($1,$2,$3,$4,$5,$6,$7,$8,$9) returning id, status`,
      [need(flag('network'), '--network'), need(flag('kind'), '--kind'), flag('status', 'waiting'),
       flag('person'), flag('url'), flag('note'), body, flag('facts'), flag('question')]);
    out(row);
  },

  // soc revise <id> --body-file f.txt   (правка текста агентом; статус возвращается в waiting)
  async revise() {
    const id = need(rest[0], 'id');
    const bodyFile = flag('body-file');
    const body = bodyFile ? fs.readFileSync(bodyFile, 'utf8').trim() : need(flag('body'), '--body или --body-file');
    const row = await one(
      `update drafts set body = $2, status = 'waiting', approved_at = null where id = $1 returning id, status`, [id, body]);
    out(row ?? 'нет такого черновика');
  },

  // soc approved — что можно отправлять прямо сейчас (только approved, по одному)
  async approved() {
    out(await q(`select id, network, kind, target_url, body from drafts where status = 'approved' order by approved_at`));
  },

  // soc sent <id> --url <ссылка на отправленное> [--outcome "..."]
  async sent() {
    const id = need(rest[0], 'id');
    const d = await one(`select * from drafts where id = $1`, [id]);
    if (!d) return out('нет такого черновика');
    if (d.status !== 'approved') {
      console.error(`черновик ${id} в статусе «${d.status}», а не approved — отметить отправленным нельзя`);
      process.exit(3);
    }
    const url = need(flag('url'), '--url отправленного');
    await q(`update drafts set status = 'sent', sent_at = now(), sent_url = $2 where id = $1`, [id, url]);
    const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
    await q(`insert into ledger (at, network, topic, url, outcome) values ($1,$2,$3,$4,$5)`,
      [stamp, d.network, (d.target_note || d.body).slice(0, 140), url, flag('outcome')]);
    const iid = `${d.network.slice(0, 2)}-${stamp.slice(0, 10).replaceAll('-', '')}-d${id}`;
    await q(`insert into interactions (id, ts, network, direction, kind, person, post_url, parent_url, text, draft_id)
             values ($1,$2,$3,'out',$4,$5,$6,$7,$8,$9) on conflict (id) do nothing`,
      [iid, stamp, d.network, d.kind, d.target_person, url, d.target_url, d.body, id]);
    out({ id: Number(id), status: 'sent', interaction: iid });
  },

  // soc inbound --network linkedin --person id --text "..." --url ... [--reply-to interaction_id] [--ts ...]
  async inbound() {
    const ts = flag('ts', new Date().toISOString().slice(0, 16).replace('T', ' '));
    const id = flag('id', `${need(flag('network'), '--network').slice(0, 2)}-in-${ts.replace(/\D/g, '').slice(0, 12)}-${Math.floor(Math.random() * 900 + 100)}`);
    await q(`insert into interactions (id, ts, network, direction, kind, person, post_url, in_reply_to, text, note)
             values ($1,$2,$3,'in',$4,$5,$6,$7,$8,$9)`,
      [id, ts, flag('network'), flag('kind', 'reply'), flag('person'), flag('url'), flag('reply-to'),
       need(flag('text'), '--text'), flag('note')]);
    out({ id });
  },

  // soc question --id q-005 --asker person --question "..." --to-us true --topic "..." [--answer "..."]
  async question() {
    const id = flag('id') ?? 'q-' + String((await one(`select count(*)+1 as n from questions`)).n).padStart(3, '0');
    await q(`insert into questions (id, date, network, asker, asker_role, to_us, question, context, source, url, topic, yatfa_answer, status)
             values ($1, coalesce($2::date, current_date), $3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)`,
      [id, flag('date'), need(flag('network'), '--network'), flag('asker'), flag('role'), flag('to-us') === 'true',
       need(flag('question'), '--question'), flag('context'), flag('source'), flag('url'), flag('topic'),
       flag('answer'), flag('status', 'наблюдаем')]);
    out({ id });
  },

  // soc person --id x --name ... --network ...
  async person() {
    await q(`insert into people (id, name, network, profile, headline, followers, segment, relevance, first_seen, status)
             values ($1,$2,$3,$4,$5,$6,$7,$8, current_date, $9)
             on conflict (id) do update set status = excluded.status, relevance = coalesce(excluded.relevance, people.relevance)`,
      [need(flag('id'), '--id'), need(flag('name'), '--name'), need(flag('network'), '--network'), flag('profile'),
       flag('headline'), flag('followers'), flag('segment'), flag('relevance'), flag('status', 'наблюдаем')]);
    out('ok');
  },

  // soc export — резервная копия в JSONL (data/), чтобы история жила и в гите
  async export() {
    const dir = flag('dir', path.join(root, '..', 'sales-md', 'social', 'data'));
    fs.mkdirSync(dir, { recursive: true });
    for (const [t, order] of [['people', 'id'], ['interactions', 'ts, id'], ['questions', 'id'], ['drafts', 'id'], ['ledger', 'id']]) {
      const rows = await q(`select * from ${t} order by ${order}`);
      fs.writeFileSync(path.join(dir, t + '.jsonl'), rows.map(r => JSON.stringify(r)).join('\n') + '\n');
    }
    out('выгружено в ' + dir);
  },

  async help() {
    out(`soc <команда>
  migrate                       применить схему
  queue [--status waiting|approved|sent|skipped|draft|all]
  show <id>
  add --network --kind --url --note --body-file|--body [--facts --question --person]
  revise <id> --body-file|--body
  approved                      что утверждено и ждёт отправки
  sent <id> --url <ссылка> [--outcome ...]   только для approved
  inbound --network --text [--person --url --reply-to --kind --ts]
  question --network --question [--asker --role --to-us true --topic --answer --context --source --url]
  person --id --name --network [...]
  export [--dir <путь>]         JSONL-копия в sales-md/social/data`);
  },
};

try {
  const fn = commands[cmd ?? 'help'];
  if (!fn) { console.error('неизвестная команда: ' + cmd); await commands.help(); process.exitCode = 2; }
  else await fn();
} catch (e) {
  console.error('ошибка:', e.message);
  process.exitCode = 1;
} finally {
  await close();
}
