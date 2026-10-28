// soc lead — управление воронкой лидов.
import { q, one, close } from '../lib/db.mjs';

const [cmd, ...rest] = process.argv.slice(2);
const flag = (n, d = null) => { const i = rest.indexOf('--' + n); return i >= 0 ? rest[i + 1] : d; };

try {
  if (cmd === 'list') {
    out(await q(`select l.id, p.name, p.network, l.stage, l.pain, l.next_step, p.profile
                 from leads l join people p on p.id = l.person
                 order by case l.stage when 'talking' then 0 when 'connected' then 1
                 when 'commented' then 2 when 'candidate' then 3 else 4 end, l.id`));
  } else if (cmd === 'add') {
    const row = await one(
      `insert into leads (person, stage, source_url, pain, next_step) values ($1,$2,$3,$4,$5)
       on conflict (person) do update set stage = excluded.stage, pain = coalesce(excluded.pain, leads.pain),
       next_step = coalesce(excluded.next_step, leads.next_step), updated_at = now() returning id, person, stage`,
      [need('--person'), flag('stage', 'candidate'), flag('url'), flag('pain'), flag('next')]);
    out(row);
  } else if (cmd === 'stage') {
    const id = need(rest[0], 'id лида');
    out(await one(`update leads set stage = $2, updated_at = now() where id = $1 returning id, stage`, [id, need(flag('stage', flag('to')), '--stage')]));
  } else { out('soc lead list | add --person <id> [--stage --url --pain --next] | stage <id> --stage <стадия>'); }

  function out(o) { console.log(typeof o === 'string' ? o : JSON.stringify(o, null, 1)); }
  function need(v, w) { if (!v) { throw new Error('нужно: ' + w); } return v; }
} finally { await close(); }
