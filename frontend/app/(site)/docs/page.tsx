import Link from 'next/link';
import { КОНТАКТЫ } from '@/content/contacts';
import type { Metadata } from 'next';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { DocsNav } from '@/components/landing/DocsNav';
import { РазделыСлева } from '@/components/landing/РазделыСлева';
import { Оглавление } from '@/components/landing/Оглавление';
import {
  ПОКАЗАТЕЛИ_АНАЛИТИКИ, ЧЕГО_НЕ_СЧИТАЕМ,
} from '@/content/docs';

export const metadata: Metadata = {
  title: 'Аналитика продукта — документация Mazmun',
  description:
    'Что Mazmun измеряет в собственном продукте и чего не измеряет никогда: '
    + 'рейтингов педагогов нет, оценок ученикам система не ставит.',
};

/**
 * Страница документации по макету design/Dokumentaciya.dc.html.
 *
 * Три колонки: разделы слева, текст посередине, оглавление справа.
 * Разделы кроме открытого пока не ссылки — их страницы приезжают своими
 * блоками; ссылок в никуда на сайте не будет, как и в меню кабинета.
 *
 * Дата обновления берётся из константы, а не из new Date(): страница
 * статическая, и «обновлено сегодня» на ней означало бы, что документ
 * обновляется сам по себе.
 */
const ОБНОВЛЕНО = '1 сентября 2026';

export default function DocsPage() {
  return (
    <>
      <DocsNav />
      <div className="docs-grid" style={{
        display: 'grid',
        gap: 'clamp(17px, 3.4vw, 40px)', padding: 'clamp(24px, 4vw, 38px) var(--pad-x) clamp(40px, 6vw, 70px)',
      }}>
      <div>
        <РазделыСлева активный="analitika" />
        <Card style={{ marginTop: 22, padding: 13, background: 'var(--gold-soft)' }}>
          <div className="row" style={{ gap: 7 }}>
            <span style={{ color: '#7A5A12' }}><Icon name="lock" size={15} /></span>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: '#7A5A12' }}>Раздел закрыт</span>
          </div>
          <p style={{ fontSize: 12, color: '#7A5A12', marginTop: 5, lineHeight: 1.5 }}>
            Сами дашборды видит только команда проекта. Эта страница объясняет, что там считается —
            и её может прочитать кто угодно.
          </p>
        </Card>
      </div>

      <main style={{ maxWidth: 720 }}>
        <div className="row" style={{ gap: 8 }}>
          <Chip tone="золотой" icon="lock">закрытый доступ</Chip>
          <span className="muted" style={{ fontSize: 12.5 }}>обновлено {ОБНОВЛЕНО}</span>
        </div>
        <h1 style={{ fontFamily: 'var(--serif)', fontSize: 'clamp(28px, 5vw, 40px)', lineHeight: 1.15, letterSpacing: '-0.025em', marginTop: 14 }}>
          Аналитика продукта
        </h1>
        <p style={{ fontSize: 17, color: 'var(--ink-2)', lineHeight: 1.65, marginTop: 14 }}>
          Это описание того, что мы измеряем в собственном продукте: сколько людей им пользуются,
          что именно делают и возвращаются ли. Речь идёт о поведении в приложении — не о качестве
          уроков и не об успеваемости детей.
        </p>

        <Card style={{ marginTop: 24, padding: '16px 18px', background: 'var(--blue-soft)', display: 'flex', gap: 12 }}>
          <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 1 }}><Icon name="eye" size={18} /></span>
          <p style={{ fontSize: 14, lineHeight: 1.6 }}>
            Страница написана обычным языком специально: педагог, директор школы или родитель
            должен понимать, что о нём считают, не открывая ни одного дашборда.
          </p>
        </Card>

        <h2 id="pravilo" style={{ fontFamily: 'var(--serif)', fontSize: 26, marginTop: 44, letterSpacing: '-0.015em', scrollMarginTop: 84 }}>
          Главное правило: система говорит только то, что видела
        </h2>
        <p style={{ fontSize: 15.5, color: 'var(--ink-2)', lineHeight: 1.7, marginTop: 12 }}>
          Все числа ниже — про события в приложении. Кнопку нажали, файл скачали, код класса ввели.
          Из этого нельзя сделать вывод о том, как прошёл урок, и мы такой вывод не делаем.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 14, marginTop: 20 }}>
          <Card style={{ padding: 16, background: 'var(--red-soft)' }}>
            <div className="row" style={{ gap: 8 }}>
              <span style={{ color: 'var(--red)' }}><Icon name="warn" size={15} /></span>
              <span className="lbl" style={{ color: 'var(--red)' }}>Так писать нельзя</span>
            </div>
            <p style={{ fontSize: 14, marginTop: 9, lineHeight: 1.55 }}>
              «Педагог не провёл рефлексию на уроке»<br />«Ученик не выучил тему»
            </p>
          </Card>
          <Card style={{ padding: 16, background: 'var(--green-soft)' }}>
            <div className="row" style={{ gap: 8 }}>
              <span style={{ color: 'var(--green)' }}><Icon name="check" size={15} /></span>
              <span className="lbl" style={{ color: 'var(--green)' }}>Так правильно</span>
            </div>
            <p style={{ fontSize: 14, marginTop: 9, lineHeight: 1.55 }}>
              «В записи не обнаружено этапа рефлексии»<br />«В тетради не найдено трёх мест из девятнадцати»
            </p>
          </Card>
        </div>

        <h2 id="chto-schitaem" style={{ fontFamily: 'var(--serif)', fontSize: 26, marginTop: 44, letterSpacing: '-0.015em', scrollMarginTop: 84 }}>
          Что именно мы считаем
        </h2>
        <p style={{ fontSize: 15.5, color: 'var(--ink-2)', lineHeight: 1.7, marginTop: 12 }}>
          Шесть показателей. У каждого — определение простыми словами и объяснение, зачем он нужен.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 22 }}>
          {ПОКАЗАТЕЛИ_АНАЛИТИКИ.map((п, i) => (
            <Card key={п.заголовок} style={{ padding: 20 }}>
              <div id={`pokazatel-${i}`} style={{ position: 'relative', top: -84 }} />
              <div className="row" style={{ gap: 11 }}>
                <span style={{ color: 'var(--blue-700)' }}><Icon name={п.знак} size={19} /></span>
                <h3 style={{ fontSize: 17 }}>{п.заголовок}</h3>
                {п.главный ? <Chip tone="серый" style={{ marginLeft: 'auto' }}>главный показатель</Chip> : null}
              </div>
              {п.абзацы.map(([подпись, текст], k) => (
                <p key={подпись} className={k ? 'muted' : undefined}
                   style={{ fontSize: k ? 14 : 14.5, lineHeight: k ? 1.6 : 1.65, marginTop: k ? 8 : 11 }}>
                  <b>{подпись}</b> {текст}
                </p>
              ))}
            </Card>
          ))}
        </div>

        <h2 id="chego-ne-schitaem" style={{ fontFamily: 'var(--serif)', fontSize: 26, marginTop: 44, letterSpacing: '-0.015em', scrollMarginTop: 84 }}>
          Чего мы не считаем — и не будем
        </h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 11, marginTop: 16 }}>
          {ЧЕГО_НЕ_СЧИТАЕМ.map(([заголовок, текст]) => (
            <div key={заголовок} className="row" style={{ gap: 11, alignItems: 'flex-start' }}>
              <span style={{ color: 'var(--red)', flex: 'none', paddingTop: 2 }}><Icon name="lock" size={17} /></span>
              <p style={{ fontSize: 15, lineHeight: 1.6 }}><b>{заголовок}</b> {текст}</p>
            </div>
          ))}
        </div>

        <h2 id="istochnik" style={{ fontFamily: 'var(--serif)', fontSize: 26, marginTop: 44, letterSpacing: '-0.015em', scrollMarginTop: 84 }}>
          Откуда берутся числа и кто их видит
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 14, marginTop: 16 }}>
          <Card style={{ padding: 18 }}>
            <span className="lbl">Источник</span>
            <p style={{ fontSize: 14.5, lineHeight: 1.6, marginTop: 8 }}>
              Обезличенные события приложения: какое действие, когда, чем закончилось. Без текстов
              документов и без имён.
            </p>
          </Card>
          <Card style={{ padding: 18 }}>
            <span className="lbl">Доступ</span>
            <p style={{ fontSize: 14.5, lineHeight: 1.6, marginTop: 8 }}>
              Дашборды открыты только команде проекта. Школе по запросу выдаётся сводка по её
              собственным педагогам — без сравнения их между собой.
            </p>
          </Card>
        </div>

        <Card style={{ marginTop: 34, padding: '22px 24px', background: 'var(--paper)' }}>
          <h3 style={{ fontSize: 17 }}>Остались вопросы</h3>
          <p style={{ fontSize: 14.5, color: 'var(--ink-2)', lineHeight: 1.6, marginTop: 8 }}>
            Если директор школы или родитель хочет уточнить, что именно считается о его педагогах и
            детях — мы отвечаем полностью и письменно. Это не коммерческая тайна.
          </p>
          <div className="row" style={{ gap: 10, marginTop: 14 }}>
            {/* Почта для заявок ещё не заведена (content/contacts.ts) —
                кнопка выключена, а не ведёт в никуда. */}
            {КОНТАКТЫ.почта ? (
              <a href={`mailto:${КОНТАКТЫ.почта}`}><Button size="малая">Написать нам</Button></a>
            ) : (
              <Button size="малая" disabled aria-disabled="true" title="Почта для вопросов ещё не заведена">
                Написать нам
              </Button>
            )}
            <Link href="/docs/data"><Button size="малая" variant="тихая">Данные и приватность</Button></Link>
          </div>
        </Card>
      </main>

      <aside style={{ fontSize: 13 }}>
        <div className="lbl" style={{ marginBottom: 10 }}>На этой странице</div>
        <Оглавление />
      </aside>
      </div>
    </>
  );
}
