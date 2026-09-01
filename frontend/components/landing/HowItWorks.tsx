import { Icon } from '@/components/Icon';
import { Reveal } from './Reveal';

/**
 * Три действия педагога. Фон — фирменный орнамент с затемняющей
 * подложкой (.bg-kz): без неё белый текст по узору не читается.
 */
export function HowItWorks() {
  return (
    <Reveal className="bg-kz" style={{ color: '#fff', padding: '76px 56px' }}>
      <div id="kak" style={{ position: 'relative', top: -70 }} />
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ maxWidth: 600 }}>
          <div className="lbl" style={{ color: 'var(--sky)' }}>Как это работает</div>
          <h2 className="an" style={{ fontFamily: 'var(--serif)', fontSize: 38, lineHeight: 1.2, letterSpacing: '-0.02em', marginTop: 12, color: '#fff' }}>
            Три действия педагога.<br />Остальное делает система.
          </h2>
        </div>
        <p className="an d1" style={{ marginLeft: 'auto', maxWidth: 340, color: '#A9C6DE', fontSize: 14.5, lineHeight: 1.6 }}>
          Ни одной новой программы на компьютере. Работает в браузере, в Telegram и на телефоне —
          данные общие.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 20, marginTop: 44 }}>
        <div className="an d2" style={{ borderTop: '2px solid var(--sky)', paddingTop: 20 }}>
          <div className="mono" style={{ color: 'var(--sky)', fontSize: 13 }}>01</div>
          <h3 style={{ fontSize: 20, marginTop: 10, color: '#fff' }}>Нажать «Записать» перед звонком</h3>
          <p style={{ color: '#A9C6DE', fontSize: 14.5, lineHeight: 1.65, marginTop: 10 }}>
            Телефон лежит на столе. Ведите урок как обычно — приложение не мешает и ничего не
            показывает классу.
          </p>
          <div className="card" style={{ marginTop: 18, background: 'rgba(255,255,255,.06)', padding: 14, display: 'flex', alignItems: 'center', gap: 12, boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.13)' }}>
            <div style={{ width: 34, height: 34, borderRadius: '50%', background: '#E1665C', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <i style={{ width: 12, height: 12, borderRadius: 3, background: '#fff' }} />
            </div>
            <div className="mono" style={{ fontSize: 19, fontWeight: 600 }}>18:24</div>
            <div className="wv" style={{ display: 'flex', alignItems: 'center', gap: 2, height: 20, marginLeft: 'auto' }}>
              {[0, 1, 2, 3, 4, 5].map((i) => (
                <i key={i} style={{ width: 3, height: '100%', background: 'var(--sky)', borderRadius: 2 }} />
              ))}
            </div>
          </div>
        </div>

        <div className="an d3" style={{ borderTop: '2px solid rgba(255,255,255,.2)', paddingTop: 20 }}>
          <div className="mono" style={{ color: 'var(--sky)', fontSize: 13 }}>02</div>
          <h3 style={{ fontSize: 20, marginTop: 10, color: '#fff' }}>Прочитать конспект</h3>
          <p style={{ color: '#A9C6DE', fontSize: 14.5, lineHeight: 1.65, marginTop: 10 }}>
            Через минуту после звонка приходят два конспекта: подробный для вас и короткий для
            учеников. С вашими фразами, а не с общими словами.
          </p>
          <div className="card" style={{ marginTop: 18, background: 'rgba(255,255,255,.06)', padding: 14, fontSize: 13, lineHeight: 1.6, color: '#CDE2F0', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.13)' }}>
            <div style={{ fontWeight: 600, color: '#fff', marginBottom: 6 }}>Главное с урока</div>
            <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
              <span style={{ color: 'var(--sky)', flex: 'none' }}><Icon name="check" size={14} /></span>
              <span>Импульс <span className="mono">p = m·v</span> — векторная величина</span>
            </div>
            <div className="row" style={{ gap: 8, alignItems: 'flex-start', marginTop: 6 }}>
              <span style={{ color: 'var(--sky)', flex: 'none' }}><Icon name="check" size={14} /></span>
              <span>В замкнутой системе <span className="mono">Σp = const</span></span>
            </div>
          </div>
        </div>

        <div className="an d4" style={{ borderTop: '2px solid rgba(255,255,255,.2)', paddingTop: 20 }}>
          <div className="mono" style={{ color: 'var(--sky)', fontSize: 13 }}>03</div>
          <h3 style={{ fontSize: 20, marginTop: 10, color: '#fff' }}>Проверить и подписать КСП</h3>
          <p style={{ color: '#A9C6DE', fontSize: 14.5, lineHeight: 1.65, marginTop: 10 }}>
            Черновик собран по официальной форме и подставлен в вашу таблицу. Педагог правит то,
            что считает нужным, и утверждает.
          </p>
          <div className="card" style={{ marginTop: 18, background: 'rgba(255,255,255,.06)', padding: 14, boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.13)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {[
                { ширина: '100%', цвет: 'rgba(255,255,255,.22)', задержка: '0s' },
                { ширина: '82%', цвет: 'rgba(255,255,255,.22)', задержка: '.15s' },
                { ширина: '64%', цвет: 'var(--sky)', задержка: '.3s' },
                { ширина: '91%', цвет: 'rgba(255,255,255,.22)', задержка: '.45s' },
              ].map((с) => (
                <div key={с.ширина} style={{
                  height: 7, borderRadius: 3, background: с.цвет, width: с.ширина,
                  animation: `growW 1.2s ease ${с.задержка} both`,
                }} />
              ))}
            </div>
            <div className="row" style={{ gap: 8, marginTop: 12, fontSize: 12, color: '#A9C6DE' }}>
              <Icon name="check" size={14} />
              <span>Пять колонок «Хода урока» в закреплённом порядке</span>
            </div>
          </div>
        </div>
      </div>
    </Reveal>
  );
}
