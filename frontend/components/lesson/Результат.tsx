'use client';

import { useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { ссылкаНаФайл, type Конспект } from '@/lib/api';

/**
 * Экран результата: слева расшифровка, справа конспект.
 *
 * Чего в расшифровке нет и почему: таймкодов. В артборде они нарисованы,
 * а xAI STT отдаёт сплошной текст — в `transcripts` лежит он и общая
 * длительность. Расставить времена «примерно» значило бы показать
 * педагогу выдуманные метки в документе, по которому он потом отчитается.
 *
 * Переключатель «для ученика / для педагога» переключает не адресата
 * рассылки, а глубину: ученику — разобранный конспект, педагогу —
 * опорные реплики урока. Ученику этот экран не показывается вовсе:
 * целиком конспект уходит только если его отправил учитель, вручную.
 */
function Список({ заголовок, пункты }: { заголовок: string; пункты?: string[] }) {
  if (!пункты?.length) return null;
  return (
    <div style={{ marginTop: 16 }}>
      <div className="lbl">{заголовок}</div>
      <ul style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {пункты.map((пункт) => (
          <li key={пункт} style={{ display: 'flex', gap: 10 }}>
            <span style={{ color: 'var(--green)', flex: 'none', paddingTop: 2 }}><Icon name="check" size={15} /></span>
            <span style={{ fontSize: 14, lineHeight: 1.6 }}>{пункт}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function длительность(секунд: number | null): string {
  if (!секунд) return '';
  const м = Math.round(секунд / 60);
  return `${м} мин`;
}

export function Результат({ конспект }: { конспект: Конспект }) {
  const [кому, setКому] = useState<'ученику' | 'педагогу'>('ученику');
  const ученику = конспект.content.konspekt_uchenika;
  const реплики = конспект.content.opornye_repliki;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.15fr)', gap: 14, alignItems: 'start' }}>
      <Card>
        <CardHead
          icon="wave" iconColor="var(--blue-700)" title="Расшифровка"
          aside={<span className="muted" style={{ fontSize: 12.5 }}>{длительность(конспект.transcript?.duration_seconds ?? null)}</span>}
        />
        <CardBody>
          <p style={{ fontSize: 14, lineHeight: 1.75, whiteSpace: 'pre-wrap', maxHeight: '58vh', overflowY: 'auto' }}>
            {конспект.transcript?.text || конспект.content.transcript_text || 'Расшифровка не сохранилась.'}
          </p>
          <p className="muted" style={{ fontSize: 12, marginTop: 14, lineHeight: 1.55 }}>
            Расшифровку видите только вы. Ни администрация, ни родители, ни другие учителя
            доступа к ней не имеют.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHead
          icon="file" iconColor="var(--blue-700)"
          title={конспект.tema || 'Конспект урока'}
          aside={<Chip tone="золотой">черновик</Chip>}
        />
        <CardBody>
          <div role="tablist" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, background: 'rgba(11,26,43,.05)', borderRadius: 13, padding: 4 }}>
            {(['ученику', 'педагогу'] as const).map((вариант) => (
              <button
                key={вариант} type="button" role="tab" aria-selected={кому === вариант}
                onClick={() => setКому(вариант)}
                style={{
                  height: 36, borderRadius: 10, border: 0, fontSize: 13.5,
                  fontWeight: кому === вариант ? 600 : 500,
                  background: кому === вариант ? 'var(--card)' : 'transparent',
                  boxShadow: кому === вариант ? 'var(--lift)' : 'none',
                  color: кому === вариант ? 'var(--ink)' : 'var(--ink-3)',
                }}
              >
                {вариант === 'ученику' ? 'Для ученика' : 'Для педагога'}
              </button>
            ))}
          </div>

          <div style={{ maxHeight: '52vh', overflowY: 'auto', marginTop: 4 }}>
            {кому === 'ученику' ? (
              <>
                <Список заголовок="Цели урока" пункты={ученику?.celi} />
                <Список заголовок="Главное" пункты={ученику?.glavnoe} />
                {ученику?.formuly?.length ? (
                  <div style={{ marginTop: 16 }}>
                    <div className="lbl">Формулы</div>
                    {ученику.formuly.map((ф) => (
                      <div key={ф.formula} style={{ marginTop: 8 }}>
                        <span className="mono" style={{ fontSize: 14, fontWeight: 600 }}>{ф.formula}</span>
                        <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{ф.znachenie}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                <Список заголовок="Разобранные примеры" пункты={ученику?.primery} />
                {ученику?.terminy?.length ? (
                  <div style={{ marginTop: 16 }}>
                    <div className="lbl">Термины</div>
                    {ученику.terminy.map((т) => (
                      <div key={т.termin} style={{ marginTop: 8, fontSize: 14, lineHeight: 1.6 }}>
                        <b>{т.termin}</b> — {т.opredelenie}
                      </div>
                    ))}
                  </div>
                ) : null}
                <Список заголовок="Вопросы для самопроверки" пункты={ученику?.voprosy_dlya_samoproverki} />
                {ученику?.domashnee_zadanie ? (
                  <div style={{ marginTop: 16 }}>
                    <div className="lbl">Домашнее задание</div>
                    <p style={{ fontSize: 14, lineHeight: 1.6, marginTop: 8 }}>{ученику.domashnee_zadanie}</p>
                  </div>
                ) : null}
              </>
            ) : (
              <Список заголовок="Опорные реплики урока" пункты={реплики} />
            )}
          </div>

          <div className="row" style={{ gap: 10, marginTop: 18 }}>
            {конспект.has_docx ? (
              <>
                <a href={ссылкаНаФайл('konspekt', конспект.konspekt_id)}>
                  <Button icon="dl">Скачать .docx</Button>
                </a>
                <a href={ссылкаНаФайл('konspekt', конспект.konspekt_id, 'pdf')}>
                  <Button variant="тихая" icon="dl">PDF</Button>
                </a>
              </>
            ) : null}
            <Button variant="тихая" icon="doc">Собрать КСП по конспекту</Button>
          </div>
          <p className="muted" style={{ fontSize: 12, marginTop: 12, lineHeight: 1.55 }}>
            Это черновик: конспект собрала нейросеть по расшифровке урока. Проверьте перед тем,
            как отдавать его ученикам.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
