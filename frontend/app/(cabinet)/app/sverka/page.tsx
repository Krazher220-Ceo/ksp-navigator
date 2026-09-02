'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { ТЕКСТЫ } from '@/content/texts.generated';
import {
  ОшибкаApi, апи, отправитьФотоТетради,
  type Задача, type КлассУченика, type РезультатСверки, type УрокУченика,
} from '@/lib/api';
import { следитьЗаЗадачей } from '@/lib/pollTask';

/**
 * Сверка тетради — экран ученика по артборду MobUchenik.
 *
 * Показывает РАЗНИЦУ: чего в тетради нет против записи урока. Ни
 * расшифровки, ни полного конспекта здесь не появляется ни при каком
 * действии — сервер их и не отдаёт. Целиком конспект присылает только
 * учитель и только тому, кто пропустил.
 *
 * Оценка не ставится. Рукописная кириллица распознаётся на 30–70%: этой
 * точности хватает, чтобы подсказать про пропущенную формулу, и не
 * хватает, чтобы судить о человеке.
 */
type Шаг = 'выбор' | 'ожидание' | 'готово';

function дата(строка: string | null): string {
  if (!строка) return '';
  const д = new Date(строка.includes('T') ? строка : строка.replace(' ', 'T'));
  if (Number.isNaN(д.getTime())) return строка;
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(д);
}

export default function SverkaPage() {
  const [классы, setКлассы] = useState<КлассУченика[]>([]);
  const [класс, setКласс] = useState<number | null>(null);
  const [уроки, setУроки] = useState<УрокУченика[]>([]);
  const [урок, setУрок] = useState<УрокУченика | null>(null);
  const [шаг, setШаг] = useState<Шаг>('выбор');
  const [задача, setЗадача] = useState<Задача | null>(null);
  const [итог, setИтог] = useState<РезультатСверки | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const остановить = useRef<(() => void) | null>(null);

  useEffect(() => () => остановить.current?.(), []);

  useEffect(() => {
    (async () => {
      try {
        const { classes } = await апи.классыУченика();
        setКлассы(classes);
        setКласс(classes[0]?.id ?? null);
      } catch (e) {
        setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, []);

  useEffect(() => {
    if (класс === null) { setУроки([]); return; }
    (async () => {
      try {
        setУроки((await апи.урокиУченика(класс)).lessons);
      } catch (e) {
        setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, [класс]);

  const следить = useCallback((task_id: string) => {
    остановить.current?.();
    остановить.current = следитьЗаЗадачей(
      task_id,
      (свежая) => {
        setЗадача(свежая);
        if (свежая.status === 'failed') {
          setОшибка(свежая.error ?? ТЕКСТЫ.ERROR_UNEXPECTED);
          setШаг('выбор');
          return;
        }
        if (свежая.status === 'done') {
          setИтог((свежая.result as unknown as РезультатСверки) ?? { missing_items: [] });
          setШаг('готово');
        }
      },
      (сообщение) => setОшибка(сообщение),
    );
  }, []);

  async function отправить(фото: Blob, имя: string) {
    if (!урок) return;
    setОшибка(null);
    setШаг('ожидание');
    try {
      const { task_id } = await отправитьФотоТетради(фото, имя, урок.transcript_id);
      следить(task_id);
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      setШаг('выбор');
    }
  }

  return (
    <>
      <TopBar
        date="Сверка тетради"
        title={урок ? урок.topic : 'Сверка тетради'}
        searchPlaceholder="Урок или класс"
        action={шаг !== 'выбор' ? (
          <Button variant="тихая" icon="cam" onClick={() => {
            остановить.current?.(); setШаг('выбор'); setИтог(null); setЗадача(null); setОшибка(null);
          }}>
            Сверить другой урок
          </Button>
        ) : undefined}
      />

      <div className="body" style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 720 }}>
        {ошибка ? (
          <Card style={{ padding: '13px 16px', display: 'flex', gap: 12, background: 'var(--red-soft)' }}>
            <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={18} /></span>
            <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>{ошибка}</div>
          </Card>
        ) : null}

        {шаг === 'выбор' ? (
          <>
            {классы.length === 0 ? (
              <Card><CardBody>
                <p style={{ fontSize: 14, lineHeight: 1.6 }}>{ТЕКСТЫ.STUDENT_JOIN_ASK_CODE}</p>
              </CardBody></Card>
            ) : null}

            {классы.length > 1 ? (
              <div className="row" style={{ gap: 7, flexWrap: 'wrap' }}>
                {классы.map((к) => (
                  <button key={к.id} type="button" onClick={() => setКласс(к.id)}
                          className={класс === к.id ? 'chip' : 'chip chip-grey'}
                          style={{ border: 0, cursor: 'pointer', fontSize: 12.5, padding: '6px 12px',
                                   background: класс === к.id ? 'var(--blue-700)' : undefined,
                                   color: класс === к.id ? '#fff' : undefined }}>
                    {к.name} · {к.teacher_name}
                  </button>
                ))}
              </div>
            ) : null}

            <Card>
              <CardHead icon="book" iconColor="var(--blue-700)" title="Какой это был урок" />
              <CardBody style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {уроки.length ? уроки.map((у) => (
                  <button key={у.transcript_id} type="button" onClick={() => setУрок(у)}
                          className="card"
                          style={{ padding: '11px 13px', textAlign: 'left', border: 0, cursor: 'pointer',
                                   background: урок?.transcript_id === у.transcript_id ? 'var(--blue-soft)' : 'var(--card)' }}>
                    <div style={{ fontSize: 13.5, fontWeight: 600 }}>{у.topic}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{дата(у.created_at)}</div>
                  </button>
                )) : (
                  <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                    {ТЕКСТЫ.SVERKA_NO_TRANSCRIPT}
                  </p>
                )}
              </CardBody>
            </Card>

            {урок ? (
              <Card>
                <CardHead icon="cam" iconColor="var(--blue-700)" title="Фото тетради" />
                <CardBody>
                  <label className="btn" style={{ cursor: 'pointer' }}>
                    <Icon name="cam" size={16} />
                    Сфотографировать или выбрать файл
                    <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }}
                           onChange={(е) => {
                             const файл = е.target.files?.[0];
                             if (файл) void отправить(файл, файл.name);
                           }} />
                  </label>
                  <p className="muted" style={{ fontSize: 12.5, marginTop: 12, lineHeight: 1.6 }}>
                    Сфотографируйте страницу тетради за этот урок. Фото удаляется сразу после
                    сравнения — на дисках оно не остаётся.
                  </p>
                </CardBody>
              </Card>
            ) : null}
          </>
        ) : null}

        {шаг === 'ожидание' ? (
          <Card>
            <CardHead icon="clock" iconColor="var(--blue-700)" title="Сравниваю с уроком"
                      aside={<Chip>{задача?.status === 'running' ? 'в работе' : 'в очереди'}</Chip>} />
            <CardBody>
              <div className="bar"><i style={{ width: задача?.status === 'running' ? '70%' : '25%' }} /></div>
              <p style={{ fontSize: 14, lineHeight: 1.65, marginTop: 14 }}>{ТЕКСТЫ.SVERKA_PROCESSING}</p>
            </CardBody>
          </Card>
        ) : null}

        {шаг === 'готово' && итог ? (
          <>
            {итог.notebook_unreadable ? (
              <Card><CardBody>
                <p style={{ fontSize: 14, lineHeight: 1.65 }}>{ТЕКСТЫ.SVERKA_NOTEBOOK_UNREADABLE}</p>
              </CardBody></Card>
            ) : итог.too_much_missing ? (
              // Пропущено почти всё — списка нет намеренно: иначе он
              // превратился бы в пересказ урока целиком.
              <Card style={{ background: 'var(--gold-soft)' }}><CardBody>
                <p style={{ fontSize: 14, lineHeight: 1.65, color: '#7A5A12' }}>
                  {ТЕКСТЫ.SVERKA_TOO_MUCH_MISSING}
                </p>
              </CardBody></Card>
            ) : итог.missing_items.length ? (
              <Card style={{ background: 'var(--gold-soft)' }}>
                <CardBody>
                  <div className="row" style={{ gap: 8 }}>
                    <span style={{ color: '#7A5A12' }}><Icon name="warn" size={17} /></span>
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: '#7A5A12' }}>
                      {ТЕКСТЫ.SVERKA_RESULT_HEADER}
                    </span>
                  </div>
                  <ul style={{ marginTop: 11, display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {итог.missing_items.map((пункт) => (
                      <li key={пункт} style={{ display: 'flex', gap: 10 }}>
                        <span style={{ color: '#B08425', flex: 'none', paddingTop: 3 }}><Icon name="plus" size={14} /></span>
                        <span style={{ fontSize: 13.5, lineHeight: 1.5 }}>{пункт}</span>
                      </li>
                    ))}
                  </ul>
                </CardBody>
              </Card>
            ) : (
              <Card style={{ background: 'var(--green-soft)' }}><CardBody>
                <p style={{ fontSize: 14.5, lineHeight: 1.6, color: 'var(--green)', fontWeight: 600 }}>
                  {ТЕКСТЫ.SVERKA_NOTHING_MISSING}
                </p>
              </CardBody></Card>
            )}

            {урок?.homework ? (
              <Card>
                <CardBody>
                  <div className="row" style={{ gap: 8 }}>
                    <span style={{ color: 'var(--blue-700)' }}><Icon name="book" size={17} /></span>
                    <span className="lbl">Домашнее задание</span>
                  </div>
                  <div style={{ fontSize: 14.5, fontWeight: 600, marginTop: 5 }}>{урок.homework}</div>
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                    То, что учитель назвал вслух на уроке.
                  </div>
                </CardBody>
              </Card>
            ) : null}

            <Card style={{ background: 'var(--blue-soft)' }}>
              <CardBody style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 1 }}><Icon name="shield" size={17} /></span>
                <p style={{ fontSize: 12.5, lineHeight: 1.55 }}>
                  Здесь показано только то, чего нет в твоей тетради. Полный конспект урока может
                  прислать учитель — если ты болел и пропустил. Оценку система не ставит.
                </p>
              </CardBody>
            </Card>
          </>
        ) : null}
      </div>
    </>
  );
}
