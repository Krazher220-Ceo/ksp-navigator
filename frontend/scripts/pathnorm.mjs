// Канон для атрибута d контура иконки.
//
// Зачем: макеты собраны через Iconify, и он переписывает путь — делает
// команды относительными, схлопывает L в H/V, выбрасывает Z, округляет
// радиусы дуг и заменяет прямые кубические кривые отрезками. Геометрия при
// этом та же, а строка другая, поэтому сравнивать пути побайтово нельзя.
//
// Что делает: приводит всё к абсолютным координатам, разворачивает H и V в
// L, выпрямляет вырожденные кривые и склеивает соседние отрезки на одной
// прямой. Чего осознанно не делает: не сравнивает растр и не разбирает
// заполнение — двух контуров с одинаковой геометрией и разным fill-rule в
// наборе нет.
const ARGS = { m: 2, l: 2, h: 1, v: 1, c: 6, s: 4, q: 4, t: 2, a: 7, z: 0 };
const EPS = 0.5; // полпункта сетки 256×256 — предел округления Iconify

// Расстояние от точки до отрезка p0→p1, если проекция попадает внутрь него.
function offSegment(p0, p1, p) {
  const dx = p1[0] - p0[0], dy = p1[1] - p0[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p[0] - p0[0], p[1] - p0[1]);
  const t = ((p[0] - p0[0]) * dx + (p[1] - p0[1]) * dy) / len2;
  if (t < -EPS || t > 1 + EPS) return Infinity;
  return Math.abs((p[0] - p0[0]) * dy - (p[1] - p0[1]) * dx) / Math.sqrt(len2);
}

function segments(d) {
  const tok = d.match(/[a-zA-Z]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) || [];
  const segs = [];
  let i = 0, cmd = '', x = 0, y = 0, sx = 0, sy = 0;
  while (i < tok.length) {
    if (/[a-zA-Z]/.test(tok[i])) cmd = tok[i++];
    const low = cmd.toLowerCase();
    const rel = cmd === low;
    const n = ARGS[low];
    if (n === undefined) throw new Error('неизвестная команда пути: ' + cmd);
    const a = tok.slice(i, i + n).map(Number);
    i += n;
    // Z не выводим: заливка замыкает контур сама, а Iconify его выбрасывает.
    // Текущую точку при этом вернуть в начало подпути обязаны.
    if (low === 'z') { x = sx; y = sy; }
    else if (low === 'h' || low === 'v') {
      if (low === 'h') x = rel ? x + a[0] : a[0]; else y = rel ? y + a[0] : a[0];
      segs.push({ c: 'L', a: [x, y] });
    } else if (low === 'a') {
      const nx = rel ? x + a[5] : a[5], ny = rel ? y + a[6] : a[6];
      segs.push({ c: 'A', a: [a[0], a[1], a[2], a[3], a[4], nx, ny] });
      x = nx; y = ny;
    } else {
      const abs = a.map((v, k) => (rel ? v + (k % 2 ? y : x) : v));
      segs.push({ c: low.toUpperCase(), a: abs });
      x = abs[abs.length - 2]; y = abs[abs.length - 1];
      if (low === 'm') { sx = x; sy = y; cmd = rel ? 'l' : 'L'; }
    }
  }
  return segs;
}

export function normPath(d) {
  const segs = segments(d);
  const out = [];
  let cur = [0, 0], start = [0, 0];
  for (let s of segs) {
    const end = [s.a[s.a.length - 2], s.a[s.a.length - 1]];
    // Кривая, все опорные точки которой лежат на хорде, — это отрезок.
    if ((s.c === 'C' || s.c === 'Q') && s.a.slice(0, -2).every((_, k, arr) =>
      k % 2 ? true : offSegment(cur, end, [arr[k], arr[k + 1]]) <= EPS)) {
      s = { c: 'L', a: end };
    }
    if (s.c === 'M') { start = end; out.push(s); }
    else if (s.c === 'L') {
      const prev = out[out.length - 1];
      // Отрезок нулевой длины выбрасываем, продолжение прямой — приклеиваем.
      if (Math.hypot(end[0] - cur[0], end[1] - cur[1]) <= EPS) { /* пропуск */ }
      else if (prev && prev.c === 'L'
               && offSegment(prevPoint(out, start), end, prev.a) <= EPS) prev.a = end;
      else out.push(s);
    } else out.push(s);
    cur = end;
  }
  const flat = [];
  for (const s of out) flat.push(s.c, ...s.a.map((v) => Math.round(v * 100) / 100));
  return flat;
}

// Точка, из которой вышел последний записанный сегмент.
function prevPoint(out, start) {
  const before = out[out.length - 2];
  return before ? [before.a[before.a.length - 2], before.a[before.a.length - 1]] : start;
}

// Пути одинаковы, если совпал порядок команд, а числа разошлись не больше
// чем на полпункта сетки 256×256 — величину, которой на экране не видно.
export function samePath(a, b) {
  if (a.length !== b.length) return false;
  return a.every((v, i) => (typeof v === 'string' ? v === b[i] : Math.abs(v - b[i]) <= EPS));
}
