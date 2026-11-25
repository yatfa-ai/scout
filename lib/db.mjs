// Shared database access. The connection string comes from DATABASE_URL or from .env (never committed).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Where `soc export` writes and `import` reads JSONL backups. Git-ignored: it holds real contacts.
const dataDir = process.env.SCOUT_DATA_DIR || path.join(root, 'data');

function loadEnv() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const f = path.join(root, '.env');
  if (!fs.existsSync(f)) throw new Error('Нет DATABASE_URL: ни в окружении, ни в .env');
  const m = fs.readFileSync(f, 'utf8').match(/^DATABASE_URL=(.+)$/m);
  if (!m) throw new Error('В .env нет строки DATABASE_URL=');
  return m[1].trim();
}

// A SQL `date` (questions.date, people.first_seen) is a calendar day, not an instant. pg turns it into a JS Date
// at local midnight, and a JSON export then writes the previous day in UTC: a backup made at UTC+3 and loaded
// into a UTC server lost a day per round trip. Keep it as the plain 'YYYY-MM-DD' string.
pg.types.setTypeParser(pg.types.builtins.DATE, v => v);

export const pool = new pg.Pool({ connectionString: loadEnv(), max: 4 });
export const q = (text, params) => pool.query(text, params).then(r => r.rows);
export const one = async (text, params) => (await q(text, params))[0] ?? null;
export const close = () => pool.end();
export { root, dataDir };
