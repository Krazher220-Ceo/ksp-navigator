'use client';

import { useMemo, useState } from 'react';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import type { Реплика } from '@/lib/api';

/**
 * Расшифровка урока репликами, с настоящим временем.
 *
 * Зачем модуль: раньше расшифровка показывалась сплошным полотном на
 * сорок пять минут речи — читать это невозможно. Время здесь измеренное:
 * xAI отдаёт отметку начала и конца каждого слова, а реплики собираются
 * из них арифметикой (core/transcript_blocks.py). Ни одной секунды не
 * придумано — блок без отметки просто не появляется.
 *
 * Что осознанно не делает: не пересказывает и не выделяет «главное».
 * После расшифровки нейросеть в этом пути не участвует (решение автора
 * от 02.09.2026) — обобщает урок сборка КСП, отдельным промптом.
 *
 * Длинный урок сворачивается: показываются первые реплики, остальное —
 * по кнопке. Иначе экран уезжает на десять прокруток вниз.
 */
const ПОКАЗЫВАТЬ_СРАЗУ = 12;

function время(секунды: number): string {
  const всего = Math.floor(секунды);
  const ч = Math.floor(всего / 3600);
  const м = Math.floor((всего % 3600) / 60);
  const с = всего % 60;
  const дв = (n: number) => String(n).padStart(2, '0');
  return ч ? `${ч}:${дв(м)}:${дв(с)}` : `${дв(м)}:${дв(с)}`;
}

type Props = {
  реплики: Реплика[];
  /** Запасной вариант: расшифровка без отметок времени. */
  текст?: string;
  длительность?: number | null;
};

export function Расшифровка({ реплики, текст, длительность }: Props) {
  const [всё, setВсё] = useState(false);
  const видимые = useMemo(
    () => (всё ? реплики : реплики.slice(0, ПОКАЗЫВАТЬ_СРАЗУ)),
    [реплики, всё],
  );

  const слов = useMemo(() => {
    const источник = реплики.length ? реплики.map((р) => р.text).join(' ') : (текст ?? '');
    return источник.split(/\s+/).filter(Boolean).length;
  }, [реплики, текст]);

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div className="row" style={{ gap: 10, padding: '14px 16px', borderBottom: '1px solid var(--line)' }}>
        <span style={{ color: 'var(--blue-700)' }}><Icon name="wave" size={18} /></span>
        <div style={{ fontWeight: 600, fontSize: 14 }}>Расшифровка урока</div>
        <span className="row" style={{ marginLeft: 'auto', gap: 7 }}>
          {длительность ? <Chip tone="зелёный">{время(длительность)}</Chip> : null}
          <Chip>{слов} слов</Chip>
        </span>
      </div>

      {реплики.length === 0 ? (
        // Отметок времени нет — показываем текст как есть и говорим об
        // этом прямо, а не рисуем правдоподобные «00:00».
        <div style={{ padding: '14px 16px' }}>
          <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
            Для этой записи отметки времени не сохранились — расшифровка показана целиком.
          </p>
          <p style={{ fontSize: 14, lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{текст}</p>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {видимые.map((реплика, i) => (
              <div
                key={`${реплика.start}-${i}`}
                className="split replika"
                style={{
                  ['--split' as string]: '58px minmax(0,1fr)',
                  gap: 14, padding: '11px 16px',
                  borderTop: i === 0 ? undefined : '1px solid var(--hair)',
                }}
              >
                <span className="mono" style={{ fontSize: 12, color: 'var(--blue-700)', paddingTop: 2 }}>
                  {время(реплика.start)}
                </span>
                <span style={{ fontSize: 14, lineHeight: 1.65 }}>{реплика.text}</span>
              </div>
            ))}
          </div>

          {реплики.length > ПОКАЗЫВАТЬ_СРАЗУ ? (
            <button
              type="button" className="btn btn-3"
              onClick={() => setВсё((б) => !б)}
              style={{ width: '100%', justifyContent: 'center', padding: '12px 0', borderTop: '1px solid var(--line)' }}
            >
              {всё
                ? 'Свернуть'
                : `Показать всю расшифровку — ещё ${реплики.length - ПОКАЗЫВАТЬ_СРАЗУ}`}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
