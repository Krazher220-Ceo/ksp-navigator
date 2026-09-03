'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { Input } from '@/components/Input';
import { TopBar } from '@/components/TopBar';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, отправитьКтп, type Задача, type ТемаКтп } from '@/lib/api';
import { следитьЗаЗадачей } from '@/lib/pollTask';

/**
 * Календарно-тематический план: загрузить свой или собрать новый.
 *
 * Зачем экран: до 02.09.2026 пункт «Собрать КТП» в меню кабинета вёл в
 * никуда — эндпоинта не было, загрузить КТП можно было только ботом.
 * При этом весь мастер КСП опирается на КТП: из него берутся тема,
 * раздел и код цели обучения.
 *
 * Два пути намеренно разные по цене. Загрузка готового файла — чтение
 * таблицы, доли секунды, нейросеть не участвует вовсе, поэтому ответ
 * приходит сразу. Сборка нового КТП — десятки секунд работы нейросети,
 * поэтому она идёт очередью, как КСП.
 *
 * Чего экран осознанно не делает: не редактирует строки КТП по одной.
 * КТП — один документ на учебный год, и правится он перезагрузкой
 * исправленного файла, а не построчно в браузере.
 */
const ДАТА = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });

export default function KtpPage() {
  const [темы, setТемы] = useState<ТемаКтп[] | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [успех, setУспех] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [задача, setЗадача] = useState<Задача | null>(null);
  const файл = useRef<HTMLInputElement>(null);
  const остановить = useRef<(() => void) | null>(null);

  const обновить = useCallback(async () => {
    try {
      setТемы((await апи.темыКтп()).entries);
    } catch (сбой) {
      setОшибка(сбой instanceof ОшибкаApi ? сбой.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    }
  }, []);

  useEffect(() => { void обновить(); return () => остановить.current?.(); }, [обновить]);

  async function загрузить(событие: React.ChangeEvent<HTMLInputElement>) {
    const выбран = событие.target.files?.[0];
    // Поле сбрасывается сразу: иначе повторный выбор того же файла не
    // вызовет change и человек решит, что кнопка сломалась.
    событие.target.value = '';
    if (!выбран) return;
    setОшибка(null); setУспех(null); setЗанято(true);
    try {
      const итог = await отправитьКтп(выбран, выбран.name);
      setУспех(итог.replaced
        ? `Загружено строк: ${итог.inserted}. Прежний план заменён.`
        : `Загружено строк: ${итог.inserted}.`);
      await обновить();
    } catch (сбой) {
      setОшибка(сбой instanceof ОшибкаApi ? сбой.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  async function собрать(событие: React.FormEvent<HTMLFormElement>) {
    событие.preventDefault();
    const форма = new FormData(событие.currentTarget);
    setОшибка(null); setУспех(null); setЗанято(true);
    try {
      const { task_id } = await апи.собратьКтп({
        predmet: String(форма.get('predmet') || ''),
        klass: String(форма.get('klass') || ''),
        chasov_v_nedelu: Number(форма.get('chasov_v_nedelu') || 0),
        chasov_v_god: Number(форма.get('chasov_v_god') || 0),
        topics: String(форма.get('topics') || '') || null,
      });
      остановить.current?.();
      остановить.current = следитьЗаЗадачей(
        task_id,
        async (свежая) => {
          setЗадача(свежая);
          if (свежая.status === 'done') {
            setЗанято(false);
            setУспех('КТП собран и записан. Темы появились в списке ниже.');
            await обновить();
          }
          if (свежая.status === 'failed') {
            setЗанято(false);
            setОшибка(свежая.error ?? ТЕКСТЫ.ERROR_UNEXPECTED);
          }
        },
        (сообщение) => { setЗанято(false); setОшибка(сообщение); },
      );
    } catch (сбой) {
      setЗанято(false);
      setОшибка(сбой instanceof ОшибкаApi ? сбой.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    }
  }

  const идёт = занято && задача !== null && задача.status !== 'done' && задача.status !== 'failed';

  return (
    <>
      <TopBar
        date={ДАТА.format(new Date())}
        title="Календарно-тематический план"
        searchPlaceholder="Тема, раздел или код цели"
        action={(
          <Button icon="cal" onClick={() => файл.current?.click()} disabled={занято}>
            Загрузить КТП
          </Button>
        )}
      />

      <div className="body" style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14, overflowY: 'auto' }}>
        <input
          ref={файл} type="file" hidden onChange={загрузить}
          accept=".docx,.xlsx,.xls,.csv"
        />

        <div className="split" style={{ display: 'grid', ['--split' as string]: 'minmax(0,1fr) minmax(0,1.1fr)', gap: 14, alignItems: 'start' }}>
          <Card>
            <CardHead icon="cal" iconColor="var(--blue-700)" title="У меня уже есть КТП" />
            <CardBody>
              <p style={{ fontSize: 14, lineHeight: 1.65 }}>
                Пришлите файл, который выдали в школе, — .docx, .xlsx или .csv. Разбор идёт сразу,
                нейросеть в нём не участвует: из таблицы читаются раздел, тема, часы и код цели.
              </p>
              <p className="muted" style={{ fontSize: 12.5, marginTop: 10, lineHeight: 1.55 }}>
                Загрузка заменяет прежний план целиком: КТП — один документ на учебный год,
                и повторная загрузка означает «прислал исправленный».
              </p>
              <Button
                icon="cal" arrow onClick={() => файл.current?.click()} disabled={занято}
                style={{ marginTop: 14, width: '100%', justifyContent: 'center' }}
              >
                Выбрать файл
              </Button>
            </CardBody>
          </Card>

          <Card>
            <CardHead icon="spark" iconColor="var(--blue-700)" title="КТП ещё нет — соберите" />
            <CardBody>
              <form onSubmit={собрать} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 190px), 1fr))', gap: 12 }}>
                  <Input id="ktp-predmet" name="predmet" label="Предмет" required placeholder="Физика" />
                  <Input id="ktp-klass" name="klass" label="Класс" required placeholder="7" />
                  <Input id="ktp-nedelu" name="chasov_v_nedelu" label="Часов в неделю" type="number" min={1} required placeholder="2" />
                  <Input id="ktp-god" name="chasov_v_god" label="Часов в год" type="number" min={1} required placeholder="68" />
                </div>
                <Input
                  id="ktp-topics" name="topics" label="Разделы программы (необязательно)"
                  placeholder="через запятую, если хотите задать их сами"
                />
                <Button type="submit" size="крупная" arrow disabled={занято} style={{ width: '100%', justifyContent: 'center' }}>
                  {идёт ? 'Собираю КТП…' : 'Собрать КТП на год'}
                </Button>
                <p className="muted" style={{ fontSize: 12, lineHeight: 1.55 }}>
                  Черновик формирует нейросеть — проверьте его перед сдачей. Сборка занимает
                  до минуты, страницу можно не держать открытой.
                </p>
              </form>
            </CardBody>
          </Card>
        </div>

        {ошибка ? (
          <div className="card" style={{ padding: '13px 15px', background: 'var(--red-soft)', color: 'var(--red)', fontSize: 13.5 }}>{ошибка}</div>
        ) : null}
        {успех ? (
          <div className="card" style={{ padding: '13px 15px', background: 'var(--green-soft)', fontSize: 13.5 }}>{успех}</div>
        ) : null}

        <Card>
          <CardHead
            icon="layers" iconColor="var(--blue-700)" title="Темы в вашем плане"
            aside={<Chip>{темы === null ? '—' : `${темы.length}`}</Chip>}
          />
          <CardBody>
            {темы === null ? (
              <p className="muted" style={{ fontSize: 13.5 }}>Собираю данные…</p>
            ) : темы.length === 0 ? (
              <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                План пока пуст. Загрузите свой КТП или соберите новый — после этого мастер КСП
                начнёт подставлять тему, раздел и код цели сам.
              </p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13.5 }}>
                  <thead>
                    <tr className="lbl">
                      {['№', 'Раздел', 'Тема', 'Часы', 'Код цели', 'Четверть'].map((к) => (
                        <th key={к} style={{ textAlign: 'left', padding: '8px 10px', whiteSpace: 'nowrap' }}>{к}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {темы.map((т) => (
                      <tr key={т.id} style={{ borderTop: '1px solid var(--hair)' }}>
                        <td style={{ padding: '9px 10px', color: 'var(--ink-3)' }}>{т.lesson_number ?? '—'}</td>
                        <td style={{ padding: '9px 10px' }}>{т.section ?? '—'}</td>
                        <td style={{ padding: '9px 10px' }}>{т.topic ?? '—'}</td>
                        <td style={{ padding: '9px 10px', color: 'var(--ink-3)' }}>{т.hours ?? '—'}</td>
                        <td style={{ padding: '9px 10px' }} className="mono">{т.objective_code ?? '—'}</td>
                        <td style={{ padding: '9px 10px', color: 'var(--ink-3)' }}>{т.quarter ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>

        <p className="muted row" style={{ fontSize: 12.5, gap: 8 }}>
          <Icon name="shield" size={15} />
          Ваш план видите только вы. Ни администрация школы, ни другие педагоги доступа к нему не имеют.
        </p>
      </div>
    </>
  );
}
