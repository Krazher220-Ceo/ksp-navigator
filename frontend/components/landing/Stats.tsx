import { Icon } from '@/components/Icon';
import { ПОДПИСЬ_К_ПОКАЗАТЕЛЯМ, ПОКАЗАТЕЛИ } from '@/content/landing';
import { Reveal } from './Reveal';

/**
 * Полоса чисел под первым экраном.
 *
 * Сами числа и их происхождение — в content/landing.ts. Два из четырёх
 * там помечены как неподтверждённые: перед публикацией их надо либо
 * подтвердить замером, либо заменить.
 */
export function Stats() {
  return (
    <Reveal style={{
      background: 'var(--paper)', borderTop: '1px solid var(--line)',
      borderBottom: '1px solid var(--line)', padding: '34px 56px',
    }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,minmax(0,1fr))', gap: 34 }}>
        {ПОКАЗАТЕЛИ.map((п, i) => (
          <div key={п.число} className={i ? `an d${i}` : 'an'}>
            <div style={{ fontSize: 34, fontWeight: 700, letterSpacing: '-0.03em' }}>{п.число}</div>
            <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>{п.пояснение}</div>
          </div>
        ))}
      </div>
      <div className="row" style={{ gap: 8, marginTop: 22, color: 'var(--ink-3)', fontSize: 12 }}>
        <Icon name="chart" size={14} />
        <span>{ПОДПИСЬ_К_ПОКАЗАТЕЛЯМ}</span>
      </div>
    </Reveal>
  );
}
