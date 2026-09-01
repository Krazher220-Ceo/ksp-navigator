import { Icon, type IconName } from '@/components/Icon';
import { Reveal } from './Reveal';

/**
 * «Честно о данных» и блок про микрофон в классе.
 *
 * Фразы «запись не уходит наружу» здесь нет и быть не может: в пилоте
 * она неверна (design/CONTEXT.md, раздел 5, пункт 2). Написано то, что
 * есть на самом деле — запись уходит на расшифровку и сразу удаляется,
 * а школе, которой это не подходит, система ставится на её сервер.
 *
 * Блок про голоса детей стоит на странице сам, а не в ответ на вопрос:
 * решение автора от 01.09 — называть это первыми.
 */
const ГАРАНТИИ: { знак: IconName; заголовок: string; текст: string }[] = [
  { знак: 'shield', заголовок: 'Аудио удаляется сразу', текст: 'Не «через 30 дней» и не «по запросу» — в тот же момент, кодом.' },
  { знак: 'file', заголовок: 'Черновик помечен как черновик', текст: 'В каждом файле написано, что он сформирован ИИ и требует проверки.' },
  { знак: 'users', заголовок: 'Об ученике — только имя', текст: 'Ни ИИН, ни дат рождения, ни оценок. Их незачем хранить.' },
  { знак: 'lock', заголовок: 'Рейтингов педагогов нет', текст: 'И не появится. Это инструмент педагога, а не надзор над ним.' },
];

const ПРО_МИКРОФОН = [
  {
    заголовок: 'Согласие берётся до первой записи',
    текст: 'Письменное — у педагога, у законных представителей учеников — до начала пилота в классе. Не галочка в оферте, а отдельный документ.',
  },
  {
    заголовок: 'Файл удаляется в ту же секунду',
    текст: 'Не «через 30 дней» и не «по запросу» — удаление зашито в код обработки, сразу после расшифровки. На дисках записи нет.',
  },
  {
    заголовок: 'Расшифровка не публикуется и не показывается ученикам',
    текст: 'Её видит только сам педагог. Ни администрация, ни родители, ни другие учителя доступа не имеют.',
  },
];

export function DataHonesty() {
  return (
    <Reveal style={{ background: 'var(--paper)', borderTop: '1px solid var(--line)', padding: '70px 56px' }}>
      <div id="dannye" style={{ position: 'relative', top: -70 }} />
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.1fr)', gap: 56, alignItems: 'center' }}>
        <div className="an">
          <div className="lbl">Честно о данных</div>
          <h2 style={{ fontFamily: 'var(--serif)', fontSize: 34, lineHeight: 1.22, letterSpacing: '-0.02em', marginTop: 12 }}>
            Мы не обещаем того, чего не делаем.
          </h2>
          <p style={{ fontSize: 16, color: 'var(--ink-2)', lineHeight: 1.65, marginTop: 16 }}>
            В пилоте запись урока уходит на расшифровку внешнему сервису и <b>сразу после этого
            удаляется</b> — на наших дисках её нет. Мы говорим об этом прямо и берём согласие до
            первого использования, а не мелким шрифтом.
          </p>
          <p style={{ fontSize: 16, color: 'var(--ink-2)', lineHeight: 1.65, marginTop: 12 }}>
            Школе, которой такой порядок не подходит, система ставится на её собственный сервер:
            тогда запись не выходит за периметр школы. Это условие поставки, а не обещание в
            презентации.
          </p>
        </div>
        <div className="an d1" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          {ГАРАНТИИ.map((г) => (
            <div key={г.заголовок} className="card" style={{ padding: 18 }}>
              <span style={{ color: 'var(--green)' }}><Icon name={г.знак} size={20} /></span>
              <div style={{ fontWeight: 600, fontSize: 15, marginTop: 10 }}>{г.заголовок}</div>
              <div className="muted" style={{ fontSize: 13.5, marginTop: 4 }}>{г.текст}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card an d2" style={{ marginTop: 22, padding: '30px 34px', display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.25fr)', gap: 40, alignItems: 'start' }}>
        <div>
          <div className="row" style={{ gap: 10 }}>
            <span style={{ color: 'var(--red)' }}><Icon name="mic" size={20} /></span>
            <span className="lbl" style={{ color: 'var(--red)' }}>Вопрос, который задают первым</span>
          </div>
          <h3 style={{ fontFamily: 'var(--serif)', fontSize: 27, lineHeight: 1.2, letterSpacing: '-0.02em', marginTop: 12 }}>
            Микрофон в классе слышит и детей. Мы это не скрываем.
          </h3>
          <p style={{ fontSize: 15, color: 'var(--ink-2)', lineHeight: 1.65, marginTop: 14 }}>
            Делать вид, что записывается только голос педагога, нельзя — так микрофон не работает.
            Голос ребёнка по закону персональные данные, и на их обработку нужно согласие. Поэтому
            мы называем это первыми, а не ждём вопроса от директора.
          </p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {ПРО_МИКРОФОН.map((п) => (
            <div key={п.заголовок} className="row" style={{ gap: 13, alignItems: 'flex-start', background: 'rgba(11,26,43,.035)', borderRadius: 14, padding: '15px 17px' }}>
              <span style={{ color: 'var(--green)', flex: 'none', paddingTop: 2 }}><Icon name="check" size={17} /></span>
              <div>
                <b style={{ fontSize: 14.5 }}>{п.заголовок}</b>
                <div className="muted" style={{ fontSize: 13.5, marginTop: 2 }}>{п.текст}</div>
              </div>
            </div>
          ))}
          <div className="row" style={{ gap: 13, alignItems: 'flex-start', background: 'var(--gold-soft)', borderRadius: 14, padding: '15px 17px' }}>
            <span style={{ color: 'var(--gold)', flex: 'none', paddingTop: 2 }}><Icon name="warn" size={17} /></span>
            <div>
              <b style={{ fontSize: 14.5, color: '#7A5A12' }}>Что мы пока не можем обещать</b>
              <div style={{ fontSize: 13.5, marginTop: 2, color: '#7A5A12' }}>
                В пилоте расшифровку делает внешний сервис за пределами Казахстана — это
                трансграничная передача, и она названа в согласии прямым текстом. Школе, которой
                это не подходит, система ставится на её собственный сервер.
              </div>
            </div>
          </div>
        </div>
      </div>
    </Reveal>
  );
}
