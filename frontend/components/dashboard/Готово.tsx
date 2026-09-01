import { Card, CardHead } from '@/components/Card';
import { Icon } from '@/components/Icon';
import type { Дэшборд } from '@/lib/api';

/**
 * Третий вопрос дэшборда: что уже готово.
 *
 * Числа — из core.dashboard, все до одного. Списка документов здесь нет:
 * артборд показывает «Готово за эту неделю» перечнем файлов, а такого
 * перечня в расчёте нет — он приедет вместе с историей (блок Ф9).
 * Показывать вместо него выдуманные строки нельзя.
 */
export function Готово({ данные }: { данные: Дэшборд }) {
  const строки: [string, string, string][] = [
    ['doc', 'Черновиков КСП за неделю', String(данные.generated_ksp.last_7d)],
    ['file', 'За месяц', String(данные.generated_ksp.last_30d)],
    ['cal', 'Тем КТП с планом', `${данные.ktp_coverage.covered} из ${данные.ktp_coverage.covered + данные.ktp_coverage.not_covered}`],
  ];

  return (
    <Card style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <CardHead icon="seal" iconColor="var(--green)" title="Уже готово" />
      <div style={{ padding: '6px 10px 10px' }}>
        {строки.map(([знак, подпись, значение], i) => (
          <div key={подпись}>
            {i > 0 ? <div className="sep" /> : null}
            <div className="row" style={{ padding: '11px 8px', gap: 12 }}>
              <span style={{ color: 'var(--blue-700)' }}>
                <Icon name={знак as 'doc'} size={19} />
              </span>
              <div style={{ minWidth: 0, flex: 1, fontSize: 13.5 }}>{подпись}</div>
              <span className="mono" style={{ fontSize: 14, fontWeight: 600 }}>{значение}</span>
            </div>
          </div>
        ))}
        <div className="sep" />
        <div className="row" style={{ padding: '11px 8px', gap: 12 }}>
          <span style={{ color: данные.style_profile.exists ? 'var(--green)' : 'var(--ink-3)' }}>
            <Icon name="spark" size={19} />
          </span>
          <div style={{ minWidth: 0, flex: 1, fontSize: 13.5 }}>
            Профиль стиля
            <div className="muted" style={{ fontSize: 11.5 }}>
              {данные.style_profile.exists
                ? `собран по ${данные.style_profile.samples_count} КСП`
                : 'пришлите 2–5 своих КСП боту — документы станут похожи на ваши'}
            </div>
          </div>
        </div>
      </div>
      <div className="row" style={{ marginTop: 'auto', padding: '13px 18px', boxShadow: 'inset 0 1px 0 var(--hair-2)', gap: 10 }}>
        <span style={{ color: 'var(--ink-3)' }}><Icon name="shield" size={16} /></span>
        <span className="muted" style={{ fontSize: 12 }}>
          Аудиофайлов на диске: 0 — удаляются сразу после расшифровки
        </span>
      </div>
    </Card>
  );
}
