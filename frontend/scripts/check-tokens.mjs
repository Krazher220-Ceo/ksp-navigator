// Проверка: токены кабинета совпадают с токенами макетов.
//
// Источник правды — блок :root в design/src/_head.html. Кабинет держит
// копию в app/tokens.css. Скрипт сверяет их посимвольно: пропала
// переменная, округлилось значение, подкрутилась тень — прогон краснеет.
//
// Три шрифтовые стопки сверяются иначе: next/font подставляет своё имя
// семейства, поэтому от них требуется, чтобы хвост стопки после
// добавленной переменной совпадал с макетом дословно.
//
// Запуск: npm run check:tokens
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const FONTS = ['--sans', '--serif', '--mono'];

// Разбор одного блока :root { ... } в пары «переменная — значение».
export function readTokens(css) {
  const block = css.slice(css.indexOf(':root'));
  const body = block.slice(block.indexOf('{') + 1, block.indexOf('}'));
  const out = new Map();
  for (const line of body.split(';')) {
    const m = line.match(/(--[a-z0-9-]+)\s*:\s*([\s\S]+)/i);
    if (m) out.set(m[1], m[2].replace(/\/\*[\s\S]*?\*\//g, '').trim());
  }
  return out;
}

const design = readTokens(readFileSync(join(root, '../design/src/_head.html'), 'utf8'));
const app = readTokens(readFileSync(join(root, 'app/tokens.css'), 'utf8'));

const errors = [];
for (const [name, value] of design) {
  const mine = app.get(name);
  if (mine === undefined) { errors.push(`токен ${name} потерян`); continue; }
  if (FONTS.includes(name)) {
    if (!mine.endsWith(value)) errors.push(`стопка ${name}: хвост разошёлся с макетом\n      макет: ${value}\n      кабинет: ${mine}`);
    if (!mine.startsWith('var(--font-')) errors.push(`стопка ${name}: перед стопкой нет переменной next/font`);
    continue;
  }
  if (mine !== value) errors.push(`токен ${name}: в макете «${value}», в кабинете «${mine}»`);
}
for (const name of app.keys()) if (!design.has(name)) errors.push(`токен ${name} в макетах не описан`);

if (errors.length) {
  console.error('Токены разошлись с макетами:');
  for (const e of errors) console.error('  — ' + e);
  process.exit(1);
}
console.log(`токены сверены с макетами: ${design.size} шт., расхождений нет`);
