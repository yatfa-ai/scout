// Общие помощники консольных утилит (bin/soc.mjs, bin/lead.mjs). Без импортов.

// Разбор флагов: makeFlag(rest) -> flag(имя, по_умолчанию = null).
// Если --имя стоит последним токеном, вернётся undefined (не def) — так было в обоих файлах.
export const makeFlag = rest => (name, def = null) => {
  const i = rest.indexOf('--' + name);
  return i >= 0 ? rest[i + 1] : def;
};

// Семантика отказа/вывода у каждой утилиты своя, поэтому набор выбирается по виду:
//   'exit'  (soc)  — need пишет в stderr и завершает процесс с кодом 2; JSON с отступом 2.
//   'throw' (lead) — need бросает Error (ловит верхний уровень утилиты); JSON с отступом 1.
export function makeGuards(kind) {
  if (kind === 'exit') {
    return {
      need: (v, what) => { if (v == null || v === '') { console.error('нужно: ' + what); process.exit(2); } return v; },
      out: o => console.log(typeof o === 'string' ? o : JSON.stringify(o, null, 2)),
    };
  }
  if (kind === 'throw') {
    return {
      need: (v, w) => { if (!v) { throw new Error('нужно: ' + w); } return v; },
      out: o => console.log(typeof o === 'string' ? o : JSON.stringify(o, null, 1)),
    };
  }
  throw new Error('неизвестный dispatch: ' + kind);
}
