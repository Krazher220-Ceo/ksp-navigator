'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { Input } from '@/components/Input';
import { TopBar } from '@/components/TopBar';
import { КодПриглашения } from '@/components/classes/КодПриглашения';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, type Класс, type Ученик } from '@/lib/api';

/**
 * Классы и ученики по артборду Klassy.
 *
 * Слева список классов, справа код приглашения и таблица учеников.
 * Об ученике показывается ровно то, что о нём хранится: имя, дата
 * вступления, число сверок и последняя активность. Ни ИИН, ни дат
 * рождения, ни оценок в системе нет — и в таблице их взяться неоткуда.
 *
 * Кнопки «отправить конспект всему классу» здесь нет и не будет: это
 * решение автора, а не недоделка. Отправка всегда адресная.
 */
function инициалы(имя: string | null): string {
  if (!имя) return '—';
  return имя.trim().split(/\s+/).slice(0, 2).map((с) => с[0]?.toUpperCase() ?? '').join('');
}

function дата(строка: string | null): string {
  if (!строка) return '—';
  const д = new Date(строка.includes('T') || строка.includes(' ') ? строка : `${строка}T00:00:00`);
  if (Number.isNaN(д.getTime())) return строка;
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(д);
}

export default function KlassyPage() {
  const [классы, setКлассы] = useState<Класс[]>([]);
  const [выбран, setВыбран] = useState<number | null>(null);
  const [ученики, setУченики] = useState<Ученик[]>([]);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [создаём, setСоздаём] = useState(false);
  const [новый, setНовый] = useState({ name: '', subject: '' });
  const [удаляем, setУдаляем] = useState<number | null>(null);

  const загрузитьКлассы = useCallback(async () => {
    try {
      const { classes } = await апи.классы();
      setКлассы(classes);
      setВыбран((прежний) => (прежний && classes.some((к) => к.id === прежний) ? прежний : classes[0]?.id ?? null));
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    }
  }, []);

  useEffect(() => { void загрузитьКлассы(); }, [загрузитьКлассы]);

  useEffect(() => {
    if (выбран === null) { setУченики([]); return; }
    (async () => {
      try {
        setУченики((await апи.ученики(выбран)).students);
      } catch (e) {
        setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, [выбран]);

  const текущий = классы.find((к) => к.id === выбран) ?? null;

  async function создать(событие: React.FormEvent) {
    событие.preventDefault();
    setОшибка(null);
    setЗанято(true);
    try {
      const { class: класс } = await апи.создатьКласс(новый.name, новый.subject || undefined);
      setСоздаём(false);
      setНовый({ name: '', subject: '' });
      await загрузитьКлассы();
      setВыбран(класс.id);
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  async function перевыпустить() {
    if (!текущий) return;
    setЗанято(true);
    try {
      await апи.перевыпуститьКод(текущий.id);
      await загрузитьКлассы();
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  async function удалить(class_id: number) {
    setЗанято(true);
    try {
      await апи.удалитьКласс(class_id);
      setУдаляем(null);
      await загрузитьКлассы();
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  const всегоУчеников = классы.reduce((сумма, к) => сумма + к.students_count, 0);

  return (
    <>
      <TopBar
        date="Класс"
        title="Мои классы"
        searchPlaceholder="Ученик или класс"
        action={<Button size="малая" icon="plus" onClick={() => setСоздаём(true)}>Новый класс</Button>}
      />

      <div className="body split" style={{ display: 'grid', ['--split' as string]: '300px minmax(0,1fr)', gap: 18, minHeight: 0, overflow: 'hidden' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto' }}>
          <div className="muted" style={{ fontSize: 12 }}>
            {классы.length} класс(а) · {всегоУчеников} учеников
          </div>

          {классы.map((класс) => (
            <button
              key={класс.id} type="button" onClick={() => setВыбран(класс.id)}
              className="card"
              style={{
                padding: 14, textAlign: 'left', border: 0, cursor: 'pointer',
                boxShadow: выбран === класс.id ? '0 0 0 2px var(--blue-soft), 0 0 0 1px var(--blue-600)' : 'var(--lift)',
              }}
            >
              <div className="row">
                <span style={{ fontFamily: 'var(--serif)', fontSize: 20, fontWeight: 700 }}>{класс.name}</span>
                <Chip tone={выбран === класс.id ? 'синий' : 'серый'} style={{ marginLeft: 'auto' }}>активен</Chip>
              </div>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                {класс.subject ? `${класс.subject} · ` : ''}создан {дата(класс.created_at)}
              </div>
              <div className="row" style={{ marginTop: 12, gap: 14 }}>
                <div>
                  <div style={{ fontSize: 17, fontWeight: 700 }}>{класс.students_count}</div>
                  <div className="muted" style={{ fontSize: 11 }}>учеников</div>
                </div>
              </div>
            </button>
          ))}

          {создаём ? (
            <Card style={{ padding: 14 }}>
              <form onSubmit={создать} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <Input id="klass-name" label="Название класса" required placeholder="10 «А»"
                       value={новый.name} onChange={(е) => setНовый((п) => ({ ...п, name: е.target.value }))} />
                <Input id="klass-subject" label="Предмет" placeholder="Физика"
                       value={новый.subject} onChange={(е) => setНовый((п) => ({ ...п, subject: е.target.value }))} />
                <div className="row" style={{ gap: 8 }}>
                  <Button type="submit" size="малая" disabled={занято}>Создать</Button>
                  <Button size="малая" variant="тихая" onClick={() => setСоздаём(false)}>Отмена</Button>
                </div>
              </form>
            </Card>
          ) : (
            <Button variant="тихая" icon="plus" style={{ width: '100%', justifyContent: 'center' }}
                    onClick={() => setСоздаём(true)}>
              Добавить класс
            </Button>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0, overflowY: 'auto' }}>
          {ошибка ? (
            <Card style={{ padding: '13px 16px', display: 'flex', gap: 12, background: 'var(--red-soft)' }}>
              <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={18} /></span>
              <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>{ошибка}</div>
            </Card>
          ) : null}

          {текущий ? (
            <>
              <КодПриглашения класс={текущий.name} код={текущий.invite_code}
                              занято={занято} onПеревыпустить={() => void перевыпустить()} />

              <Card style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <CardHead icon="users" iconColor="var(--blue-700)" title={`Ученики ${текущий.name}`}
                          aside={<Chip tone="серый">{ученики.length} вступили</Chip>} />
                {ученики.length ? (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', fontSize: 13.5 }}>
                      <thead>
                        <tr style={{ background: 'var(--paper)' }}>
                          {['Ученик', 'Вступил', 'Сверок тетради', 'Последняя активность', ''].map((подпись) => (
                            <th key={подпись} style={{ textAlign: 'left', padding: '9px 12px', fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--ink-3)', fontWeight: 600 }}>
                              {подпись}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {ученики.map((ученик) => (
                          <tr key={ученик.id}>
                            <td style={{ padding: '10px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>
                              <div className="row" style={{ gap: 10 }}>
                                <div className="avatar" style={{ background: 'var(--blue-soft)', color: 'var(--blue-800)', fontSize: 11 }}>
                                  {инициалы(ученик.name)}
                                </div>
                                <span style={{ fontWeight: 500 }}>{ученик.name || 'без имени'}</span>
                              </div>
                            </td>
                            <td className="muted" style={{ padding: '10px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>{дата(ученик.joined_at)}</td>
                            <td style={{ padding: '10px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>{ученик.sverki}</td>
                            <td className="muted" style={{ padding: '10px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)' }}>{дата(ученик.last_activity)}</td>
                            <td style={{ padding: '10px 12px', boxShadow: 'inset 0 1px 0 var(--line-2)', textAlign: 'right' }}>
                              <Button size="малая" variant="тихая" icon="send" disabled={!ученик.can_receive}
                                      title={ученик.can_receive ? undefined : 'У ученика не привязан Telegram — доставить нечем'}>
                                Отправить конспект
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <CardBody>
                    <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                      В классе пока никого. Продиктуйте код приглашения на уроке — ученики введут его
                      сами, отдельная регистрация им не нужна.
                    </p>
                  </CardBody>
                )}
                <div className="row" style={{ marginTop: 'auto', padding: '11px 16px', boxShadow: 'inset 0 1px 0 var(--line-2)', gap: 9, color: 'var(--ink-3)', fontSize: 12 }}>
                  <Icon name="shield" size={14} />
                  <span>
                    О каждом ученике хранится только имя и идентификатор в мессенджере. Оценок,
                    ИИН и дат рождения в системе нет.
                  </span>
                </div>
              </Card>

              <Card style={{ padding: '13px 16px' }}>
                {удаляем === текущий.id ? (
                  <div>
                    <p style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                      Удалить класс «{текущий.name}»? Ученики останутся в системе — удалится только
                      их связь с этим классом, не сами ученики и не их данные. Действие необратимо.
                    </p>
                    <div className="row" style={{ gap: 8, marginTop: 12 }}>
                      <Button size="малая" disabled={занято} onClick={() => void удалить(текущий.id)}
                              style={{ background: 'linear-gradient(180deg,#BC463C,#9C332B)' }}>
                        Подтверждаю удаление
                      </Button>
                      <Button size="малая" variant="тихая" onClick={() => setУдаляем(null)}>Отменить</Button>
                    </div>
                  </div>
                ) : (
                  <Button size="малая" variant="строкой" onClick={() => setУдаляем(текущий.id)}>
                    Удалить класс
                  </Button>
                )}
              </Card>
            </>
          ) : (
            <Card>
              <CardBody>
                <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                  Классов пока нет. Создайте первый — код приглашения выдастся сразу, и его можно
                  будет продиктовать на уроке.
                </p>
              </CardBody>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
