import { Icon, type IconName } from '@/components/Icon';
import { Reveal } from './Reveal';

/** Проблема: воскресный вечер за планами. */
const КАРТОЧКИ: { иконка: IconName; заголовок: string; текст: string }[] = [
  {
    иконка: 'clock',
    заголовок: '40–60 минут на один план',
    текст: 'Столько педагоги называют, когда пишут КСП с нуля. Умножьте на количество уроков в неделе — получится выходной.',
  },
  {
    иконка: 'doc',
    заголовок: 'Готовые планы продаются',
    текст: 'Комплект чужих КСП на маркетплейсах стоит около 3 600 ₸. Люди платят деньги, чтобы не писать это самим. Но чужой план — не про их урок.',
  },
  {
    иконка: 'quote',
    заголовок: 'Живой урок никуда не записан',
    текст: 'Удачное объяснение, точный пример, предупреждение о типичной ошибке — всё это прозвучало один раз и исчезло. В плане оказывается канцелярит.',
  },
];

export function Problem() {
  return (
    <Reveal style={{ padding: '76px 56px' }}>
      <div style={{ maxWidth: 640 }}>
        <div className="lbl">Проблема</div>
        <h2 className="an" style={{ fontFamily: 'var(--serif)', fontSize: 38, lineHeight: 1.2, letterSpacing: '-0.02em', marginTop: 12 }}>
          Воскресенье, 21:40. Педагог пишет планы на неделю.
        </h2>
        <p className="an d1" style={{ fontSize: 16.5, color: 'var(--ink-2)', lineHeight: 1.65, marginTop: 16 }}>
          Приложение 3 приказа №130 требует поурочный план ежедневно, по расписанию. Двадцать четыре
          урока в неделю — двадцать четыре документа. И самое обидное: педагог уже провёл эти уроки.
          Всё, что нужно записать, он час назад сказал вслух.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 18, marginTop: 38 }}>
        {КАРТОЧКИ.map((к, i) => (
          <div key={к.заголовок} className={`card an d${i + 1}`} style={{ padding: 22 }}>
            <span style={{ color: 'var(--blue-700)' }}><Icon name={к.иконка} size={24} /></span>
            <h3 style={{ fontSize: 17, marginTop: 14 }}>{к.заголовок}</h3>
            <p className="muted" style={{ fontSize: 14, marginTop: 8, lineHeight: 1.6 }}>{к.текст}</p>
          </div>
        ))}
      </div>
    </Reveal>
  );
}
