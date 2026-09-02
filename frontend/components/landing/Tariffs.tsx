import Link from 'next/link';
import { КОНТАКТЫ } from '@/content/contacts';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Chip';
import { Icon, type IconName } from '@/components/Icon';
import { ТАРИФЫ_ПЕДАГОГА, ТАРИФЫ_УЧЕНИКА, ТАРИФ_ШКОЛЫ } from '@/content/tariffs';
import { Reveal } from './Reveal';

/**
 * Витрина тарифов на лендинге. Числа — из content/tariffs.ts, который
 * повторяет design/TARIFFS.md; совпадение сторожит тест.
 *
 * Кнопки никуда не ведут: оплата в пилоте выключена, платёжного
 * провайдера нет, и включать его — решение автора (FRONTEND_PLAN.md,
 * раздел 6, пункт 3). Скидок, таймеров и слова «безлимитно» здесь нет.
 */
const ЗНАЧКИ: Record<string, { знак: IconName; цвет: string }> = {
  free: { знак: 'spark', цвет: 'var(--ink-3)' },
  teacher: { знак: 'seal', цвет: 'var(--blue-600)' },
  teacher_pro: { знак: 'crown', цвет: 'var(--gold)' },
};

function Карточка({ ключ }: { ключ: string }) {
  const тариф = ТАРИФЫ_ПЕДАГОГА.find((т) => т.ключ === ключ)!;
  const значок = ЗНАЧКИ[ключ];
  const главный = ключ === 'teacher';
  return (
    <div className="card" style={{ padding: 26, display: 'flex', flexDirection: 'column', boxShadow: главный ? 'none' : undefined }}>
      <div className="row" style={{ gap: 9 }}>
        <span style={{ color: значок.цвет }}><Icon name={значок.знак} size={20} /></span>
        <h3 style={{ fontSize: 18 }}>{тариф.название}</h3>
        {главный ? <Chip style={{ marginLeft: 'auto' }}>берут чаще всего</Chip> : null}
      </div>
      <div className="row" style={{ alignItems: 'baseline', gap: 8, marginTop: 16 }}>
        <span style={{ fontSize: 38, fontWeight: 700, letterSpacing: '-0.04em' }}>{тариф.ценаВМесяц}</span>
        {тариф.ценаЗаГод ? <span className="muted" style={{ fontSize: 14 }}>/ мес</span> : null}
      </div>
      <p className="muted" style={{ fontSize: 13.5, marginTop: 7, lineHeight: 1.55 }}>
        {тариф.ценаЗаГод ? `За учебный год — ${тариф.ценаЗаГод}. ` : ''}{тариф.пояснение}
      </p>
      <div className="sep" style={{ margin: '18px 0' }} />
      <ul style={{ display: 'flex', flexDirection: 'column', gap: 11, fontSize: 14 }}>
        {тариф.строки.map((строка) => (
          <li key={строка} className="row" style={{ gap: 11, alignItems: 'flex-start' }}>
            <span style={{ color: 'var(--green)' }}><Icon name="check" size={16} /></span>
            <span>{строка}</span>
          </li>
        ))}
      </ul>
      {главный ? (
        <Link href="/registraciya">
          <Button size="крупная" arrow style={{ marginTop: 24, width: '100%', justifyContent: 'center' }}>
            Выбрать «Учитель»
          </Button>
        </Link>
      ) : (
        // Все тарифы ведут в одно место — на регистрацию. Оплата в пилоте
        // выключена (design/TARIFFS.md), выбрать тариф в кабинете пока
        // нельзя, и кнопка, обещающая выбор, обещала бы лишнее.
        <Link href="/registraciya">
          <Button size="крупная" variant="тихая" style={{ marginTop: 24, width: '100%', justifyContent: 'center', borderRadius: 12 }}>
            {ключ === 'free' ? 'Зарегистрироваться' : 'Выбрать PRO'}
          </Button>
        </Link>
      )}
    </div>
  );
}

export function Tariffs() {
  return (
    <Reveal style={{ background: 'var(--paper)', boxShadow: 'inset 0 1px 0 var(--hair)', padding: '76px 56px' }}>
      <div id="tarify" style={{ position: 'relative', top: -70 }} />
      <div style={{ textAlign: 'center', maxWidth: 640, margin: '0 auto' }}>
        <div className="lbl">Тарифы</div>
        <h2 className="an" style={{ fontFamily: 'var(--serif)', fontSize: 38, lineHeight: 1.2, letterSpacing: '-0.025em', marginTop: 12 }}>
          Попробовать бесплатно. Дальше — по нагрузке.
        </h2>
        <p className="an d1" style={{ fontSize: 16, color: 'var(--ink-2)', lineHeight: 1.6, marginTop: 14 }}>
          Если вашу школу подключат по договору, вы не платите ничего — и ученики этого класса тоже.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 18, marginTop: 40, alignItems: 'start' }}>
        <div className="an d2"><Карточка ключ="free" /></div>
        <div className="an d3" style={{
          borderRadius: 24, padding: 8,
          background: 'linear-gradient(160deg,rgba(0,175,202,.3),rgba(23,90,147,.14))',
          boxShadow: '0 0 0 1px rgba(0,175,202,.22),0 34px 64px -36px rgba(12,43,73,.6)',
        }}>
          <Карточка ключ="teacher" />
        </div>
        <div className="an d4"><Карточка ключ="teacher_pro" /></div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.1fr) minmax(0,1fr)', gap: 18, marginTop: 18 }}>
        <div className="card an d5" style={{ padding: '24px 26px', display: 'flex', alignItems: 'center', gap: 20, background: 'linear-gradient(140deg,#124066,#08203A)', color: '#fff', boxShadow: 'var(--lift-2)' }}>
          <span id="shkola" style={{ position: 'relative', top: -70 }} />
          <span style={{ color: '#7FD9E8', flex: 'none' }}><Icon name="cap" size={26} /></span>
          <div>
            <div style={{ fontSize: 17, fontWeight: 600 }}>
              Школа подключает всех разом — {ТАРИФ_ШКОЛЫ.цена} {ТАРИФ_ШКОЛЫ.единица}
            </div>
            <div style={{ color: '#A9C6DE', fontSize: 14, marginTop: 4, lineHeight: 1.55 }}>
              {ТАРИФ_ШКОЛЫ.выгода} Договор со школой — и педагоги с учениками не платят ничего.
              Цена считается по количеству педагогов, а не по количеству детей.
            </div>
          </div>
          {/* Почта для заявок школ ещё не заведена (content/contacts.ts) —
              кнопка нарисована выключенной, а не ведёт в никуда. */}
          {КОНТАКТЫ.почта ? (
            <a href={`mailto:${КОНТАКТЫ.почта}?subject=${encodeURIComponent('Смета для школы')}`} style={{ marginLeft: 'auto', flex: 'none' }}>
              <Button style={{ background: 'rgba(255,255,255,.11)', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.14)' }}>
                Запросить смету
              </Button>
            </a>
          ) : (
            <Button
              disabled aria-disabled="true" title="Почта для заявок школ ещё не заведена"
              style={{ marginLeft: 'auto', flex: 'none', background: 'rgba(255,255,255,.11)', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.14)', opacity: 0.5 }}
            >
              Запросить смету
            </Button>
          )}
        </div>

        <div className="card an d6" style={{ padding: '24px 26px' }}>
          <div className="row" style={{ gap: 10 }}>
            <span style={{ color: 'var(--blue-700)' }}><Icon name="userplus" size={20} /></span>
            <h3 style={{ fontSize: 17 }}>Тарифы учеников</h3>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 12, marginTop: 16 }}>
            {ТАРИФЫ_УЧЕНИКА.map((т) => (
              <div key={т.ключ} style={{
                background: т.ключ === 'student' ? 'var(--blue-soft)' : 'rgba(11,26,43,.035)',
                borderRadius: 14, padding: 14,
                boxShadow: т.ключ === 'student' ? 'inset 0 0 0 1px rgba(28,116,188,.18)' : undefined,
              }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{т.название}</div>
                <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em', marginTop: 6 }}>{т.цена}</div>
                <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>{т.норма}</div>
              </div>
            ))}
          </div>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 14, lineHeight: 1.55 }}>
            Ученический тариф работает только там, где учитель уже на платном: без записи урока
            сверять не с чем. Полный конспект не даёт ни один тариф — его присылает учитель, вручную.
          </p>
        </div>
      </div>
    </Reveal>
  );
}
