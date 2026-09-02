'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon, type IconName } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, ссылкаНаФайл, type ЗаписьИстории } from '@/lib/api';

/**
 * История документов по артборду Istoriya.
 *
 * В одном списке и то, что собралось, и то, что собрать не вышло:
 * провалившаяся задача должна быть видна и повторяема, а не теряться.
 *
 * Фильтра по классу нет, хотя в артборде он нарисован: связи «документ →
 * класс» в базе не существует, и приписать урок классу наугад значило бы
 * соврать в том месте, где педагог потом ищет свой документ.
 */
const ВИДЫ: { ключ: string | null; подпись: string; счётчик?: 'konspekt' | 'ksp' | 'failed' }[] = [
  { ключ: null, подпись: 'Всё' },
  { ключ: 'konspekt', подпись: 'Конспекты', счётчик: 'konspekt' },
  { ключ: 'ksp', подпись: 'КСП', счётчик: 'ksp' },
  { ключ: 'failed', подпись: 'Не собрались', счётчик: 'failed' },
];

const ЗНАЧКИ: Record<string, { знак: IconName; цвет: string }> = {
  konspekt: { знак: 'file', цвет: 'var(--blue-700)' },
  ksp: { знак: 'doc', цвет: 'var(--blue-700)' },
  failed: { знак: 'warn', цвет: 'var(--red)' },
};

function когда(строка: string | null): string {
  if (!строка) return '—';
  const д = new Date(строка.includes('T') ? строка : строка.replace(' ', 'T'));
  if (Number.isNaN(д.getTime())) return строка;
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  }).format(д);
}

function длительность(секунд: number | null | undefined): string | null {
  if (!секунд) return null;
  const м = Math.floor(секунд / 60);
  const с = Math.round(секунд % 60);
  return `по записи ${м}:${String(с).padStart(2, '0')}`;
}

export default function IstoriyaPage() {
  const [записи, setЗаписи] = useState<ЗаписьИстории[]>([]);
  const [счётчики, setСчётчики] = useState({ konspekt: 0, ksp: 0, failed: 0 });
  const [вид, setВид] = useState<string | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);

  const загрузить = useCallback(async (фильтр: string | null) => {
    setОшибка(null);
    try {
      const ответ = await апи.история(фильтр ? { kind: фильтр } : undefined);
      setЗаписи(ответ.items);
      setСчётчики(ответ.counts);
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    }
  }, []);

  useEffect(() => { void загрузить(вид); }, [вид, загрузить]);

  async function повторить(task_id: string) {
    setЗанято(true);
    setОшибка(null);
    try {
      await апи.повторить(task_id);
      await загрузить(вид);
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  const всего = счётчики.konspekt + счётчики.ksp;

  return (
    <>
      <TopBar
        date="Материалы"
        title="История"
        searchPlaceholder="Тема, класс, код цели"
      />

      <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0, overflow: 'hidden' }}>
        <div className="row" style={{ gap: 7, flexWrap: 'wrap' }}>
          {ВИДЫ.map((пункт) => {
            const выбран = вид === пункт.ключ;
            const число = пункт.счётчик ? счётчики[пункт.счётчик] : всего;
            return (
              <button
                key={пункт.подпись} type="button" onClick={() => setВид(пункт.ключ)}
                className={выбран ? 'chip' : 'chip chip-grey'}
                style={{
                  border: 0, cursor: 'pointer', fontSize: 12.5, padding: '6px 12px',
                  background: выбран ? 'var(--blue-700)' : undefined,
                  color: выбран ? '#fff' : undefined,
                }}
              >
                {пункт.подпись}{пункт.ключ ? ` · ${число}` : ''}
              </button>
            );
          })}
          <span className="muted" style={{ fontSize: 12, marginLeft: 6 }}>{всего} документ(ов)</span>
        </div>

        {ошибка ? (
          <Card style={{ padding: '13px 16px', display: 'flex', gap: 12, background: 'var(--red-soft)' }}>
            <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={18} /></span>
            <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>{ошибка}</div>
          </Card>
        ) : null}

        <Card style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          {записи.length ? (
            <div style={{ overflowY: 'auto' }}>
              <table style={{ width: '100%', fontSize: 13.5 }}>
                <thead>
                  <tr style={{ background: 'var(--paper)' }}>
                    {['Документ', 'Цель обучения', 'Собран', 'Состояние', ''].map((подпись) => (
                      <th key={подпись} style={{ textAlign: 'left', padding: '10px 12px', fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-3)', fontWeight: 600 }}>
                        {подпись}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {записи.map((запись) => {
                    const значок = ЗНАЧКИ[запись.kind];
                    return (
                      <tr key={`${запись.kind}-${запись.id}`} style={запись.kind === 'failed' ? { background: 'var(--red-soft)' } : undefined}>
                        <td style={{ padding: '11px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>
                          <div className="row" style={{ gap: 10 }}>
                            <span style={{ color: значок.цвет }}><Icon name={значок.знак} size={17} /></span>
                            <div>
                              <div style={{ fontWeight: 600 }}>
                                {запись.kind === 'konspekt' ? 'Конспект · ' : запись.kind === 'ksp' ? 'КСП · ' : ''}
                                {запись.title}
                              </div>
                              <div className="muted" style={{ fontSize: 11.5 }}>
                                {запись.kind === 'failed'
                                  ? (запись.error || 'причина не записана')
                                  : [длительность(запись.duration_seconds),
                                     запись.has_docx ? 'docx' : null,
                                     запись.has_pdf ? 'pdf' : null].filter(Boolean).join(' · ')}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="mono" style={{ padding: '11px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>
                          {запись.objective_code || ''}
                        </td>
                        <td className="muted" style={{ padding: '11px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>
                          {когда(запись.created_at)}
                        </td>
                        <td style={{ padding: '11px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>
                          {запись.status === 'ready' ? <Chip tone="зелёный">готов</Chip> : null}
                          {запись.status === 'draft' ? <Chip tone="золотой">черновик</Chip> : null}
                          {запись.status === 'failed' ? <Chip tone="красный">не собран</Chip> : null}
                        </td>
                        <td style={{ padding: '11px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)', textAlign: 'right' }}>
                          {запись.kind === 'failed' ? (
                            запись.can_retry ? (
                              <Button size="малая" disabled={занято} onClick={() => void повторить(запись.id)}
                                      style={{ background: 'linear-gradient(180deg,#BC463C,#9C332B)' }}>
                                Повторить
                              </Button>
                            ) : (
                              <span className="muted" style={{ fontSize: 12 }}>повтор невозможен</span>
                            )
                          ) : (
                            <div className="row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                              {запись.has_docx ? (
                                <a href={ссылкаНаФайл(запись.kind as 'konspekt' | 'ksp', запись.id)}>
                                  <Button size="малая" variant="тихая" icon="dl">docx</Button>
                                </a>
                              ) : null}
                              {запись.has_pdf ? (
                                <a href={ссылкаНаФайл(запись.kind as 'konspekt', запись.id, 'pdf')}>
                                  <Button size="малая" variant="строкой">pdf</Button>
                                </a>
                              ) : null}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <CardBody>
              <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                Здесь пока пусто. Соберите конспект по записи урока или черновик КСП — документы
                появятся в этом списке.
              </p>
            </CardBody>
          )}
          <div className="row" style={{ marginTop: 'auto', padding: '12px 16px', boxShadow: 'inset 0 1px 0 var(--line-2)', gap: 9, color: 'var(--ink-3)', fontSize: 12 }}>
            <Icon name="warn" size={14} />
            <span>
              PDF есть только у конспекта: КСП правят перед утверждением и печатают из .docx.
            </span>
          </div>
        </Card>
      </div>
    </>
  );
}
