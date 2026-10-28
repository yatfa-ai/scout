// Общий доступ к базе. Строка подключения — из .env (в гите её нет) или переменной окружения.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function loadEnv() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  const f = path.join(root, '.env');
  if (!fs.existsSync(f)) throw new Error('Нет DATABASE_URL: ни в окружении, ни в .env');
  const m = fs.readFileSync(f, 'utf8').match(/^DATABASE_URL=(.+)$/m);
  if (!m) throw new Error('В .env нет строки DATABASE_URL=');
  return m[1].trim();
}

export const pool = new pg.Pool({ connectionString: loadEnv(), max: 4 });
export const q = (text, params) => pool.query(text, params).then(r => r.rows);
export const one = async (text, params) => (await q(text, params))[0] ?? null;
export const close = () => pool.end();
export { root };
