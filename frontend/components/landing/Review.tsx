import { Icon } from '@/components/Icon';
import { Reveal } from './Reveal';

/**
 * Место под отзыв педагога.
 *
 * Заблюрено намеренно и с честной подписью: живого педагога, готового
 * дать цитату под своим именем, ещё нет (design/CONTEXT.md, раздел 9,
 * «Открыто», пункт 2). Выдуманную цитату на лендинге школьного продукта
 * ставить нельзя — её проверят одним звонком.
 *
 * Текст под блюром — рыба, и она не читается: filter, opacity,
 * user-select и aria-hidden вместе. Появится настоящий отзыв — этот
 * компонент заменяется целиком.
 */
export function Review() {
  return (
    <Reveal style={{ padding: '70px 56px' }}>
      <div className="card an" style={{ padding: 0, position: 'relative', overflow: 'hidden' }}>
        <div aria-hidden style={{
          filter: 'blur(14px) saturate(.4)', opacity: 0.5, pointerEvents: 'none', userSelect: 'none',
          padding: '46px 52px', display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 260px',
          gap: 44, alignItems: 'center',
        }}>
          <div>
            <span style={{ color: 'var(--line)', display: 'block' }}><Icon name="quote" size={30} /></span>
            <p style={{ fontFamily: 'var(--serif)', fontSize: 24, lineHeight: 1.45, marginTop: 14, letterSpacing: '-0.01em' }}>
              Раньше я садилась за планы в воскресенье вечером и вставала из-за стола к полуночи.
              Теперь кладу телефон на стол, а вечером остаётся только прочитать и подписать.
            </p>
            <div className="row" style={{ gap: 12, marginTop: 22 }}>
              <div className="avatar" style={{ width: 40, height: 40, background: 'var(--blue-soft)', color: 'var(--blue-800)' }}>
                <Icon name="users" size={18} />
              </div>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14.5 }}>Имя Фамилия</div>
                <div className="muted" style={{ fontSize: 13 }}>Предмет · Школа, город</div>
              </div>
            </div>
          </div>
          <div style={{ background: 'var(--paper)', borderRadius: 16, height: 150 }} />
        </div>

        <div style={{
          position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', gap: 12, textAlign: 'center', padding: '0 40px',
        }}>
          <span style={{ color: 'var(--ink-3)' }}><Icon name="lock" size={26} /></span>
          <div style={{ fontFamily: 'var(--serif)', fontSize: 23, fontWeight: 700, letterSpacing: '-0.015em' }}>
            Здесь будет отзыв педагога
          </div>
          <p className="muted" style={{ fontSize: 14.5, lineHeight: 1.6, maxWidth: 520 }}>
            Настоящего, с именем и школой — после первой четверти пилота. Выдуманную цитату на
            лендинге школьного продукта ставить нельзя: её проверят одним звонком.
          </p>
        </div>
      </div>
    </Reveal>
  );
}
