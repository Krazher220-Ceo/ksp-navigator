import Link from 'next/link';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { Reveal } from './Reveal';

/**
 * Первый экран: вся цепочка продукта сразу — запись, расшифровка,
 * конспект, черновик КСП. Решение автора от 01.09: не «сначала о
 * проблеме», а показать результат целиком.
 *
 * Главная кнопка одна и та же по всей странице — «Записать первый
 * урок»; она встречается трижды и другой формулировки не имеет.
 */
function Дорожка() {
  // Полоски звуковой дорожки: анимация в .wv, разная длительность у
  // каждой второй, третьей и так далее — как в макете.
  return (
    <div className="wv" style={{ display: 'flex', alignItems: 'center', gap: 2, height: 24, flex: 'none' }}>
      {['blue', 'blue', 'blue', 'sky', 'sky', 'blue', 'blue', 'blue'].map((цвет, i) => (
        <i key={i} style={{
          width: 3, height: '100%', borderRadius: 2,
          background: цвет === 'sky' ? 'var(--sky)' : 'var(--blue-500)',
        }} />
      ))}
    </div>
  );
}

function Звено({ задержка }: { задержка: number }) {
  return (
    <div style={{ height: 26, marginLeft: 32, borderLeft: '2px dotted var(--line)', position: 'relative' }}>
      <i style={{
        position: 'absolute', left: -4, top: 0, width: 6, height: 6, borderRadius: '50%',
        background: 'var(--sky)', animation: `drift 2.6s linear ${задержка}s infinite`,
      }} />
    </div>
  );
}

export function Hero() {
  return (
    <Reveal style={{
      padding: '72px 56px 64px', display: 'grid',
      gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.02fr)', gap: 56, alignItems: 'center',
    }}>
      <div>
        <Chip className="an" icon="spark" style={{ background: 'var(--sky-soft)', color: '#0A5D6C', fontSize: 12.5, padding: '7px 14px' }}>
          Для учителей Казахстана · первая запись урока бесплатно
        </Chip>
        <h1 className="an d1" style={{ fontFamily: 'var(--serif)', fontSize: 56, lineHeight: 1.08, letterSpacing: '-0.03em', fontWeight: 700, marginTop: 20 }}>
          Урок прошёл.<br />Документы уже<br />собраны.
        </h1>
        <p className="an d2" style={{ fontSize: 18, lineHeight: 1.6, color: 'var(--ink-2)', marginTop: 20, maxWidth: 520 }}>
          Телефон в кармане записывает 45 минут урока. Через минуту после звонка у педагога есть
          конспект и черновик КСП по форме приказа МОН РК №130 — с его собственными формулировками.
          Школа не нужна: регистрируетесь сами, заводите класс, диктуете код ученикам.
        </p>
        <div className="an d3 row" style={{ gap: 12, marginTop: 30 }}>
          <Link href="/registraciya"><Button size="крупная" arrow>Записать первый урок</Button></Link>
          {/* Ролика пока нет — кнопка ведёт туда, где то же самое показано
              шагами. Ссылка в никуда хуже, чем ссылка на честное место. */}
          <a href="#kak"><Button size="крупная" variant="тихая" icon="play">Посмотреть за 90 секунд</Button></a>
        </div>
        <div className="an d4 row" style={{ gap: 20, marginTop: 22, color: 'var(--ink-3)', fontSize: 13 }}>
          <span className="row" style={{ gap: 7 }}><Icon name="check" size={15} /> Карта не нужна</span>
          <span className="row" style={{ gap: 7 }}><Icon name="check" size={15} /> Школа подключит вас бесплатно</span>
        </div>
      </div>

      <div className="an-z d2 card" style={{ padding: 26, boxShadow: '0 24px 60px -30px rgba(11,26,43,.5)', background: 'var(--paper)' }}>
        <div className="row" style={{ gap: 8, marginBottom: 18 }}>
          <span className="lbl">Один урок · четыре шага</span>
          <Chip tone="зелёный" style={{ marginLeft: 'auto' }}>≈1 минута</Chip>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="card" style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 13, background: 'var(--card)' }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--navy)', color: 'var(--sky)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <Icon name="mic" size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>Запись урока</div>
              <div className="muted" style={{ fontSize: 11.5 }}>43 минуты · телефон в кармане</div>
            </div>
            <Дорожка />
          </div>

          <Звено задержка={0} />

          <div className="card" style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 13, background: 'var(--card)' }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--blue-soft)', color: 'var(--blue-700)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <Icon name="wave" size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>Расшифровка</div>
              <div className="muted" style={{ fontSize: 11.5 }}>
                «…импульс — векторная величина
                <i style={{ display: 'inline-block', width: 1.5, height: 11, background: 'var(--blue-700)', verticalAlign: -1, marginLeft: 2, animation: 'caret 1s step-end infinite' }} />»
              </div>
            </div>
            <Chip tone="зелёный" style={{ flex: 'none' }}>12 сек</Chip>
          </div>

          <Звено задержка={0.5} />

          <div className="card" style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 13, background: 'var(--card)' }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--blue-soft)', color: 'var(--blue-700)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <Icon name="file" size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>Конспект урока</div>
              <div className="muted" style={{ fontSize: 11.5 }}>главное, разобранный пример, домашка</div>
            </div>
            <Chip style={{ flex: 'none' }}>docx · pdf</Chip>
          </div>

          <Звено задержка={1} />

          <div className="card" style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 13, background: 'var(--navy)', color: '#fff' }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: 'var(--sky)', color: 'var(--navy)', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <Icon name="doc" size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>Черновик КСП</div>
              <div style={{ color: '#A9C6DE', fontSize: 11.5 }}>форма приказа МОН РК №130, приложение 4</div>
            </div>
            <Chip tone="золотой" style={{ flex: 'none' }}>на проверку</Chip>
          </div>
        </div>
      </div>
    </Reveal>
  );
}
