'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { Диктофон } from '@/components/lesson/Диктофон';
import { Результат } from '@/components/lesson/Результат';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, отправитьЗапись, type Задача, type Конспект } from '@/lib/api';
import { следитьЗаЗадачей } from '@/lib/pollTask';

/**
 * Конспект урока: запись, ожидание, результат.
 *
 * Главный сценарий продукта. Обработка идёт в очереди на сервере, здесь
 * только опрос статуса — расшифровка занимает десятки секунд, и держать
 * ради неё открытый запрос нельзя.
 *
 * Предел размера записи называется до загрузки, а не после: урок уже
 * записан, и узнать про лимит после сорока минут ожидания — издевательство.
 */
const ПРЕДЕЛ_МБ = 100;
const ПРЕДЕЛ_БАЙТ = ПРЕДЕЛ_МБ * 1024 * 1024;

type Шаг = 'выбор' | 'ожидание' | 'готово' | 'ошибка';

export default function UrokPage() {
  const [шаг, setШаг] = useState<Шаг>('выбор');
  const [режим, setРежим] = useState<'student' | 'teacher'>('student');
  const [задача, setЗадача] = useState<Задача | null>(null);
  const [конспект, setКонспект] = useState<Конспект | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const остановить = useRef<(() => void) | null>(null);

  useEffect(() => () => остановить.current?.(), []);

  const следить = useCallback((task_id: string) => {
    остановить.current?.();
    остановить.current = следитьЗаЗадачей(
      task_id,
      async (свежая) => {
        setЗадача(свежая);
        if (свежая.status === 'failed') {
          setОшибка(свежая.error ?? ТЕКСТЫ.ERROR_UNEXPECTED);
          setШаг('ошибка');
          return;
        }
        if (свежая.status === 'done') {
          const konspekt_id = свежая.result?.konspekt_id;
          if (!konspekt_id) {
            // Расшифровка готова, конспект собирается следующей задачей —
            // её идентификатор придёт своим чередом. Пока просто ждём.
            return;
          }
          try {
            setКонспект(await апи.конспект(konspekt_id));
            setШаг('готово');
          } catch (e) {
            setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
            setШаг('ошибка');
          }
        }
      },
      (сообщение) => setОшибка(сообщение),
    );
  }, []);

  async function отправить(файл: Blob, имя: string) {
    setОшибка(null);
    if (файл.size === 0) {
      setОшибка('Файл пустой — записи в нём нет.');
      return;
    }
    if (файл.size > ПРЕДЕЛ_БАЙТ) {
      // Тот же предел проверяет и сервер: браузеру верить нельзя, а
      // сказать человеку до отправки — вежливо.
      setОшибка(`Запись больше ${ПРЕДЕЛ_МБ} МБ. Перекодируйте её в моно с пониженным битрейтом или пришлите урок частями.`);
      return;
    }
    setШаг('ожидание');
    try {
      const { task_id } = await отправитьЗапись(файл, имя, режим);
      следить(task_id);
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      setШаг('ошибка');
    }
  }

  return (
    <>
      <TopBar
        date="Конспект урока"
        title={шаг === 'готово' ? (конспект?.tema ?? 'Конспект готов') : 'Запись урока'}
        searchPlaceholder="Тема, класс или код цели"
        action={шаг !== 'выбор' ? <Button variant="тихая" icon="plus" onClick={() => { остановить.current?.(); setШаг('выбор'); setКонспект(null); setЗадача(null); setОшибка(null); }}>Новая запись</Button> : undefined}
      />

      <div className="body" style={{ overflowY: 'auto' }}>
        {шаг === 'выбор' ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 14, alignItems: 'start' }}>
            <Card>
              <CardHead icon="mic" iconColor="var(--blue-700)" title="Записать урок" />
              <CardBody>
                <Диктофон onГотово={(запись) => void отправить(запись, 'urok.webm')} />
              </CardBody>
            </Card>

            <Card>
              <CardHead icon="ul" iconColor="var(--blue-700)" title="Или прислать готовую запись" />
              <CardBody>
                <label className="btn btn-2" style={{ cursor: 'pointer' }}>
                  <Icon name="ul" size={16} />
                  Выбрать файл
                  <input
                    type="file" accept="audio/*,.m4a,.mp3,.wav,.ogg,.webm"
                    style={{ display: 'none' }}
                    onChange={(е) => {
                      const файл = е.target.files?.[0];
                      if (файл) void отправить(файл, файл.name);
                    }}
                  />
                </label>
                <p className="muted" style={{ fontSize: 12.5, marginTop: 12, lineHeight: 1.6 }}>
                  Форматы: m4a, mp3, wav, ogg, webm. Не больше {ПРЕДЕЛ_МБ} МБ — урока на 45 минут
                  хватает с запасом. Если запись длиннее, перекодируйте её в моно с пониженным
                  битрейтом или пришлите урок частями.
                </p>

                <div className="sep" style={{ margin: '16px 0' }} />

                <div className="lbl">Что сделать с записью</div>
                <div role="radiogroup" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, background: 'rgba(11,26,43,.05)', borderRadius: 13, padding: 4, marginTop: 8 }}>
                  {([['student', 'Собрать конспект'], ['teacher', 'Только расшифровка']] as const).map(([значение, подпись]) => (
                    <button
                      key={значение} type="button" role="radio" aria-checked={режим === значение}
                      onClick={() => setРежим(значение)}
                      style={{
                        height: 38, borderRadius: 10, border: 0, fontSize: 13.5,
                        fontWeight: режим === значение ? 600 : 500,
                        background: режим === значение ? 'var(--card)' : 'transparent',
                        boxShadow: режим === значение ? 'var(--lift)' : 'none',
                        color: режим === значение ? 'var(--ink)' : 'var(--ink-3)',
                      }}
                    >
                      {подпись}
                    </button>
                  ))}
                </div>
                <p className="muted" style={{ fontSize: 12.5, marginTop: 10, lineHeight: 1.55 }}>
                  «Только расшифровка» — дословный текст без обработки нейросетью.
                </p>

                {ошибка ? <p style={{ color: 'var(--red)', fontSize: 13, marginTop: 12 }}>{ошибка}</p> : null}
              </CardBody>
            </Card>
          </div>
        ) : null}

        {шаг === 'ожидание' ? (
          <Card style={{ maxWidth: 620 }}>
            <CardHead icon="clock" iconColor="var(--blue-700)" title="Запись в работе"
                      aside={<Chip>{задача?.status === 'running' ? 'расшифровываю' : 'в очереди'}</Chip>} />
            <CardBody>
              <div className="bar"><i style={{ width: задача?.status === 'running' ? '65%' : '25%' }} /></div>
              <p style={{ fontSize: 14, lineHeight: 1.65, marginTop: 14 }}>
                Расшифровка урока на 45 минут занимает около полуминуты, следом собирается
                конспект. Страницу можно закрыть: работа идёт на сервере, и если у вас привязан
                Telegram, бот напишет туда же.
              </p>
              {задача?.retries ? (
                <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
                  Попытка {задача.retries + 1}: провайдер ответил не сразу, пробую ещё раз.
                </p>
              ) : null}
              {ошибка ? <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>{ошибка}</p> : null}
            </CardBody>
          </Card>
        ) : null}

        {шаг === 'ошибка' ? (
          <Card style={{ maxWidth: 620 }}>
            <CardHead icon="warn" iconColor="var(--red)" title="Не получилось" />
            <CardBody>
              <p style={{ fontSize: 14, lineHeight: 1.65 }}>{ошибка}</p>
              <p className="muted" style={{ fontSize: 12.5, marginTop: 10, lineHeight: 1.55 }}>
                Расход по тарифу за неудачную попытку не списан. Запись на сервере не осталась —
                она удаляется и при провале тоже.
              </p>
              <Button style={{ marginTop: 14 }} onClick={() => { setШаг('выбор'); setОшибка(null); }}>
                Попробовать ещё раз
              </Button>
            </CardBody>
          </Card>
        ) : null}

        {шаг === 'готово' && конспект ? <Результат конспект={конспект} /> : null}
      </div>
    </>
  );
}
