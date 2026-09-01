// Проверка: набор иконок кабинета совпадает с набором артбордов.
//
// Артборды рисуются из design/src/_icons.json — это Phosphor Light,
// пропущенный через Iconify. Кабинет берёт те же иконки из пакета
// @phosphor-icons/react. Скрипт сверяет геометрию контуров: подменили
// иконку или вес — прогон краснеет.
//
// Запуск: npm run check:icons
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { normPath, samePath } from './pathnorm.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const defs = join(root, 'node_modules/@phosphor-icons/react/dist/defs');
const wanted = JSON.parse(readFileSync(join(root, '../design/src/_icons.json'), 'utf8'));

// Карта берётся прямо из компонента: второго списка иконок в проекте нет,
// и разойтись ему не с чем.
const map = Object.fromEntries(
  [...readFileSync(join(root, 'components/Icon.tsx'), 'utf8')
    .match(/export const ICONS = \{([\s\S]*?)\n\} as const;/)[1]
    .matchAll(/^\s*([a-z]+):\s*([A-Za-z]+),$/gm)].map((m) => [m[1], m[2]]),
);

// Контуры веса light достаём текстом из файла определений пакета:
// поднимать React ради атрибута d незачем.
function lightPaths(name) {
  const t = readFileSync(join(defs, name + '.es.js'), 'utf8');
  const i = t.indexOf('"light"');
  if (i < 0) throw new Error('в пакете нет веса light у иконки ' + name);
  const j = t.indexOf('[\n    "', i + 1);
  return [...t.slice(i, j < 0 ? t.length : j).matchAll(/d:\s*"([^"]*)"/g)]
    .map((m) => normPath(m[1])).flatMap((p) => [...p, '|']);
}

const errors = [];
const designKeys = Object.keys(wanted).sort();
const mapKeys = Object.keys(map).sort();

if (designKeys.join() !== mapKeys.join()) {
  const lost = designKeys.filter((k) => !mapKeys.includes(k));
  const extra = mapKeys.filter((k) => !designKeys.includes(k));
  if (lost.length) errors.push('нет в Icon.tsx: ' + lost.join(', '));
  if (extra.length) errors.push('нет в макетах: ' + extra.join(', '));
}

for (const key of designKeys) {
  if (!map[key]) continue;
  const fromDesign = [...wanted[key].matchAll(/ d="([^"]*)"/g)]
    .map((m) => normPath(m[1])).flatMap((p) => [...p, '|']);
  if (!samePath(fromDesign, lightPaths(map[key]))) {
    errors.push(`иконка «${key}»: контур ${map[key]} из пакета не совпал с макетом`);
  }
}

if (errors.length) {
  console.error('Иконки разошлись с макетами:');
  for (const e of errors) console.error('  — ' + e);
  process.exit(1);
}
console.log(`иконки сверены с макетами: ${designKeys.length} шт., расхождений нет`);
