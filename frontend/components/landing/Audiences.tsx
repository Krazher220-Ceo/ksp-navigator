import { Icon, type IconName } from '@/components/Icon';
import { Reveal } from './Reveal';

/**
 * Две аудитории. Половина списка ученика — про то, чего продукт
 * намеренно НЕ делает: готового конспекта ребёнок не получает и оценок
 * система не ставит. Это не оговорка мелким шрифтом, а такой же пункт,
 * как остальные, и стоит он с замком, а не с галочкой.
 */
type Пункт = { знак: IconName; цвет: string; заголовок: string; текст: string };

const ПЕДАГОГУ: Пункт[] = [
  { знак: 'check', цвет: 'var(--green)', заголовок: 'Конспект каждого урока', текст: 'Из настоящей записи. Через год вы точно знаете, что говорили в этом классе.' },
  { знак: 'check', цвет: 'var(--green)', заголовок: 'КСП по форме №130', текст: 'Стиль подхватывается из ваших прошлых планов — документ выглядит как ваш.' },
  { знак: 'check', цвет: 'var(--green)', заголовок: 'КТП на учебный год', текст: 'Один раз в августе, вместо недели за таблицей.' },
  { знак: 'check', цвет: 'var(--green)', заголовок: 'Класс в одном месте', текст: 'Код приглашения диктуется вслух на уроке. Ссылки и списки не нужны.' },
];

const УЧЕНИКУ: Пункт[] = [
  { знак: 'check', цвет: 'var(--green)', заголовок: 'Сверка тетради по фото', текст: 'Показывает разницу: что прозвучало на уроке, но не попало в тетрадь.' },
  { знак: 'check', цвет: 'var(--green)', заголовок: 'Точное домашнее задание', текст: 'То, что учитель назвал вслух, а не то, что расслышали.' },
  { знак: 'lock', цвет: 'var(--red)', заголовок: 'Готового конспекта ученик не получает', текст: 'Целиком его присылает только учитель и только тому, кто болел. Автоматической рассылки классу нет — это решение принято намеренно.' },
  { знак: 'lock', цвет: 'var(--red)', заголовок: 'Оценок система не ставит', текст: 'Рукописный текст распознаётся неточно. На такой точности можно подсказать — нельзя оценивать человека.' },
];

function Список({ пункты }: { пункты: Пункт[] }) {
  return (
    <ul style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {пункты.map((п) => (
        <li key={п.заголовок} style={{ display: 'flex', gap: 12 }}>
          <span style={{ color: п.цвет, flex: 'none', paddingTop: 3 }}><Icon name={п.знак} size={16} /></span>
          <div>
            <b style={{ fontSize: 15 }}>{п.заголовок}</b>
            <div className="muted" style={{ fontSize: 14, marginTop: 2 }}>{п.текст}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Audiences() {
  return (
    <Reveal style={{ padding: '76px 56px' }}>
      <div style={{ textAlign: 'center', maxWidth: 660, margin: '0 auto' }}>
        <div className="lbl">Кому это нужно</div>
        <h2 className="an" style={{ fontFamily: 'var(--serif)', fontSize: 38, lineHeight: 1.2, letterSpacing: '-0.02em', marginTop: 12 }}>
          Педагог получает время. Ученик получает то, что не успел записать.
        </h2>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginTop: 40 }}>
        <div className="card an d1" style={{ padding: 30 }}>
          <div className="row" style={{ gap: 11 }}>
            <span style={{ color: 'var(--blue-700)' }}><Icon name="doc" size={22} /></span>
            <h3 style={{ fontSize: 21 }}>Педагогу</h3>
          </div>
          <Список пункты={ПЕДАГОГУ} />
        </div>

        <div className="card an d2" style={{ padding: 30, background: 'var(--paper)' }}>
          <div className="row" style={{ gap: 11 }}>
            <span style={{ color: 'var(--blue-700)' }}><Icon name="book" size={22} /></span>
            <h3 style={{ fontSize: 21 }}>Ученику</h3>
          </div>
          <Список пункты={УЧЕНИКУ} />
        </div>
      </div>
    </Reveal>
  );
}
