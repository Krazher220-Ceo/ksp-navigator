import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import type { НастройкиКсп } from '@/lib/api';

/**
 * Предпросмотр формы №130 до генерации.
 *
 * Показывает пустую форму с теми полями, которые уже заполнены в
 * мастере, — чтобы человек увидел документ раньше, чем потратит на него
 * генерацию. Содержимого «Хода урока» здесь нет и быть не может: его
 * пишет нейросеть, и рисовать вместо него правдоподобные строки —
 * значит показать педагогу то, чего он не получит.
 *
 * Порядок колонок приходит с сервера, из того же модуля, что собирает
 * .docx: оценивание идёт ПЕРЕД ресурсами, это приложение 4 приказа МОН
 * РК №130 в редакции от 30.04.2025 № 98. В проекте этот порядок уже
 * путали однажды — второй копии приказа здесь нет.
 *
 * Незаполненное показывается пустым. Заглушки вроде «будет определено
 * автоматически» хуже пустоты: их никто не заметит и не поправит.
 */
type Поле = { подпись: string; значение: string | null | undefined };

type Props = {
  школа: string | null;
  педагог: string | null;
  поля: Поле[];
  колонки: НастройкиКсп['hod_uroka_columns'];
};

const РАМКА = '1px solid var(--ink-2)';

export function Предпросмотр({ школа, педагог, поля, колонки }: Props) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div className="card-h">
        <span style={{ color: 'var(--blue-700)' }}><Icon name="eye" size={16} /></span>
        <h3>Предпросмотр формы</h3>
        <Chip tone="золотой" style={{ marginLeft: 'auto' }}>черновик · требует проверки</Chip>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', background: 'var(--paper)', padding: 18, display: 'flex', justifyContent: 'center' }}>
        <div style={{ background: '#fff', width: '100%', maxWidth: 640, boxShadow: 'var(--lift)', padding: '26px 30px', fontFamily: 'var(--serif)', fontSize: 11.5, color: 'var(--ink)' }}>
          <div style={{ textAlign: 'center', borderBottom: '1px solid var(--ink)', paddingBottom: 3, marginBottom: 3, minHeight: 17 }}>
            {школа || ''}
          </div>
          <div style={{ textAlign: 'center', fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 16 }}>
            (наименование организации образования)
          </div>
          <div style={{ textAlign: 'center', fontSize: 14.5, fontWeight: 700, marginBottom: 14 }}>
            Краткосрочный (поурочный) план
          </div>

          <table style={{ width: '100%', border: РАМКА, fontSize: 11 }}>
            <tbody>
              {[{ подпись: 'ФИО педагога', значение: педагог }, ...поля].map((поле) => (
                <tr key={поле.подпись}>
                  <td style={{ border: РАМКА, padding: '5px 8px' }}>
                    <b>{поле.подпись}:</b> {поле.значение || ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ fontWeight: 700, margin: '12px 0 5px' }}>Ход урока</div>
          <table style={{ width: '100%', border: РАМКА, fontSize: 10 }}>
            <tbody>
              <tr style={{ background: 'var(--line-2)' }}>
                {колонки.map((колонка) => (
                  <th key={колонка.key} style={{ border: РАМКА, padding: '4px 6px', textAlign: 'left' }}>
                    {колонка.label}
                  </th>
                ))}
              </tr>
              {['Начало', 'Середина', 'Конец'].map((этап) => (
                <tr key={этап}>
                  <td style={{ border: РАМКА, padding: '5px 6px', verticalAlign: 'top', color: 'var(--ink-3)' }}>{этап}</td>
                  {колонки.slice(1).map((колонка) => (
                    <td key={колонка.key} style={{ border: РАМКА, padding: '5px 6px', height: 34 }} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="row" style={{ padding: '11px 16px', boxShadow: 'inset 0 1px 0 var(--hair-2)', gap: 9, color: 'var(--ink-3)', fontSize: 12 }}>
        <Icon name="warn" size={14} />
        <span>
          Так выглядит форма. «Ход урока» заполнит нейросеть — это черновик, он требует вашей
          проверки и утверждения.
        </span>
      </div>
    </div>
  );
}
