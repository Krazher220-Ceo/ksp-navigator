'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { Input } from '@/components/Input';
import { Switch } from '@/components/Switch';
import { TopBar } from '@/components/TopBar';
import { Выбор } from '@/components/ksp/Выбор';
import { Предпросмотр } from '@/components/ksp/Предпросмотр';
import { ТЕКСТЫ } from '@/content/texts.generated';
import {
  ОшибкаApi, апи,
  type Задача, type НастройкиКсп, type ОпцииУрока, type ТемаКтп, type Я,
} from '@/lib/api';
import { следитьЗаЗадачей } from '@/lib/pollTask';

/**
 * Мастер сборки КСП по артборду SborKsp.
 *
 * Три шага: тема, настройки, сборка. Справа всё время виден
 * предпросмотр формы — документ показывается раньше, чем на него
 * потрачена генерация.
 *
 * Настройки задаются кнопками: варианты закреплены приказом и
 * методичками, и приходят с сервера из core/. Своих списков здесь нет.
 *
 * Быстрый путь: выбрал тему из своего КТП — раздел и код цели
 * подставились сами. Не нашлось в КТП — поля остаются пустыми, и это
 * честнее подставленного наугад.
 */
type Шаг = 'тема' | 'настройки' | 'сборка';

export default function KspPage() {
  const [шаг, setШаг] = useState<Шаг>('тема');
  const [справочники, setСправочники] = useState<НастройкиКсп | null>(null);
  const [темыКтп, setТемыКтп] = useState<ТемаКтп[]>([]);
  const [я, setЯ] = useState<Я | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [задача, setЗадача] = useState<Задача | null>(null);
  const остановить = useRef<(() => void) | null>(null);

  const [поля, setПоля] = useState({
    topic: '', razdel: '', klass: '', duration_minutes: '40',
    objective_code: '', prisutstvuet: '', otsutstvuet: '',
  });
  const [ktpEntryId, setKtpEntryId] = useState<number | null>(null);
  const [шаблон, setШаблон] = useState<number | null>(null);
  const [опции, setОпции] = useState<ОпцииУрока>({ page_orientation: 'book', vidy_deyatelnosti: [] });

  useEffect(() => () => остановить.current?.(), []);

  useEffect(() => {
    (async () => {
      try {
        const [профиль, спр, ктп] = await Promise.all([апи.я(), апи.настройкиКсп(), апи.темыКтп()]);
        setЯ(профиль);
        setСправочники(спр);
        setТемыКтп(ктп.entries);
        const официальный = спр.templates.find((ш) => ш.is_official) ?? спр.templates[0];
        if (официальный) setШаблон(официальный.id);
      } catch (e) {
        setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, []);

  const менять = (ключ: keyof typeof поля) => (е: React.ChangeEvent<HTMLInputElement>) =>
    setПоля((прежние) => ({ ...прежние, [ключ]: е.target.value }));

  function взятьИзКтп(тема: ТемаКтп) {
    setKtpEntryId(тема.id);
    setПоля((прежние) => ({
      ...прежние,
      topic: тема.topic ?? '',
      razdel: тема.section ?? '',
      objective_code: тема.objective_code ?? '',
    }));
  }

  /** Тема введена руками — спрашиваем сервер, нет ли для неё кода в КТП. */
  const подсказатьКод = useCallback(async (topic: string) => {
    if (!topic.trim() || поля.objective_code) return;
    try {
      const { objective_code } = await апи.кодЦели(topic);
      if (objective_code) setПоля((прежние) => ({ ...прежние, objective_code }));
    } catch {
      // Подсказка — не обязательная часть: не нашлась, значит поле
      // остаётся пустым, и человек впишет код сам.
    }
  }, [поля.objective_code]);

  async function собрать() {
    if (!шаблон) return;
    setОшибка(null);
    setШаг('сборка');
    try {
      const { task_id } = await апи.собратьКсп({
        topic: поля.topic,
        razdel: поля.razdel,
        subject: я?.profile?.subject ?? '',
        klass: поля.klass,
        duration_minutes: Number(поля.duration_minutes) || 40,
        template_id: шаблон,
        objective_code: поля.objective_code || null,
        ktp_entry_id: ktpEntryId,
        options: опции,
      });
      остановить.current = следитьЗаЗадачей(
        task_id,
        (свежая) => {
          setЗадача(свежая);
          if (свежая.status === 'failed') setОшибка(свежая.error ?? ТЕКСТЫ.ERROR_UNEXPECTED);
        },
        (сообщение) => setОшибка(сообщение),
      );
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      setШаг('настройки');
    }
  }

  const готовоКСборке = Boolean(поля.topic.trim() && поля.razdel.trim() && поля.klass.trim() && шаблон);
  const номерШага = шаг === 'тема' ? 1 : шаг === 'настройки' ? 2 : 3;

  return (
    <>
      <TopBar
        date="Собрать КСП"
        title={`Шаг ${номерШага} из 3 — ${шаг === 'тема' ? 'тема урока' : шаг === 'настройки' ? 'настройки урока' : 'сборка'}`}
        searchPlaceholder="Тема, класс или код цели"
        action={(
          <div className="row" style={{ gap: 8 }}>
            {шаг === 'настройки' ? (
              <Button size="малая" variant="строкой" icon="chevl" onClick={() => setШаг('тема')}>Назад</Button>
            ) : null}
            {шаг === 'тема' ? (
              <Button size="малая" arrow disabled={!готовоКСборке} onClick={() => setШаг('настройки')}>Дальше</Button>
            ) : null}
            {шаг === 'настройки' ? (
              <Button size="малая" arrow disabled={!готовоКСборке} onClick={() => void собрать()}>Собрать черновик</Button>
            ) : null}
          </div>
        )}
      />

      <div className="body" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1.15fr)', gap: 18, minHeight: 0, overflow: 'hidden' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, overflowY: 'auto' }}>
          {ошибка ? (
            <Card style={{ padding: '13px 16px', display: 'flex', gap: 12, background: 'var(--red-soft)' }}>
              <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={18} /></span>
              <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>{ошибка}</div>
            </Card>
          ) : null}

          {ktpEntryId ? (
            <Card style={{ padding: '13px 16px', display: 'flex', gap: 12, alignItems: 'center', background: 'var(--green-soft)' }}>
              <span style={{ color: 'var(--green)' }}><Icon name="check" size={18} /></span>
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>Из КТП подставлено само</div>
                <div className="muted" style={{ fontSize: 12.5, marginTop: 1 }}>
                  Раздел и код цели обучения — из вашего календарного плана.
                </div>
              </div>
              <Button size="малая" variant="строкой" style={{ marginLeft: 'auto' }} onClick={() => setKtpEntryId(null)}>
                Изменить
              </Button>
            </Card>
          ) : null}

          {шаг === 'тема' ? (
            <>
              {темыКтп.length ? (
                <Card>
                  <CardHead icon="cal" iconColor="var(--blue-700)" title="Взять тему из КТП"
                            aside={<span className="muted" style={{ fontSize: 12 }}>{темыКтп.length}</span>} />
                  <CardBody style={{ maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {темыКтп.map((тема) => (
                      <button
                        key={тема.id} type="button" onClick={() => взятьИзКтп(тема)}
                        className="card"
                        style={{ padding: '10px 12px', textAlign: 'left', border: 0, cursor: 'pointer', background: ktpEntryId === тема.id ? 'var(--blue-soft)' : 'var(--card)' }}
                      >
                        <div style={{ fontSize: 13.5, fontWeight: 600 }}>{тема.topic}</div>
                        <div className="row muted" style={{ fontSize: 12, gap: 8, marginTop: 2 }}>
                          {тема.section ? <span>{тема.section}</span> : null}
                          {тема.objective_code ? <span className="mono">{тема.objective_code}</span> : null}
                        </div>
                      </button>
                    ))}
                  </CardBody>
                </Card>
              ) : null}

              <Card>
                <CardHead icon="doc" iconColor="var(--blue-700)" title="Урок" />
                <CardBody style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
                  <Input id="ksp-topic" label="Тема урока" required value={поля.topic}
                         onChange={менять('topic')} onBlur={() => void подсказатьКод(поля.topic)}
                         placeholder="Импульс тела. Закон сохранения импульса" />
                  <Input id="ksp-razdel" label="Раздел программы" required value={поля.razdel}
                         onChange={менять('razdel')} placeholder="10.1В Законы сохранения" />
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 13 }}>
                    <Input id="ksp-klass" label="Класс" required value={поля.klass}
                           onChange={менять('klass')} placeholder="10" />
                    <Input id="ksp-duration" label="Минут" type="number" value={поля.duration_minutes}
                           onChange={менять('duration_minutes')} />
                    <Input id="ksp-code" label="Код цели" value={поля.objective_code}
                           onChange={менять('objective_code')} placeholder="10.1.4.1" />
                  </div>
                  <p className="muted" style={{ fontSize: 12, lineHeight: 1.55 }}>
                    Код цели подставляется из КТП сам, если тема там есть. Не нашлась — поле
                    останется пустым, и в документе тоже будет пусто.
                  </p>
                </CardBody>
              </Card>
            </>
          ) : null}

          {шаг === 'настройки' && справочники ? (
            <>
              <Card>
                <CardHead icon="layers" iconColor="var(--blue-700)" title="Шаблон" />
                <CardBody style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                  {справочники.templates.map((ш) => (
                    <button
                      key={ш.id} type="button" onClick={() => setШаблон(ш.id)}
                      className="card"
                      style={{
                        padding: '11px 13px', display: 'flex', gap: 11, alignItems: 'flex-start',
                        textAlign: 'left', border: 0, cursor: 'pointer',
                        boxShadow: шаблон === ш.id ? '0 0 0 2px var(--blue-soft), 0 0 0 1px var(--blue-600)' : 'var(--lift)',
                      }}
                    >
                      <div style={{ width: 17, height: 17, borderRadius: '50%', flex: 'none', marginTop: 2, boxShadow: шаблон === ш.id ? 'inset 0 0 0 5px var(--blue-600)' : 'inset 0 0 0 1.6px var(--line)' }} />
                      <div style={{ minWidth: 0 }}>
                        <div className="row" style={{ gap: 7 }}>
                          <span style={{ fontWeight: 600, fontSize: 13.5 }}>{ш.name}</span>
                          {ш.is_official ? <Chip>официальный</Chip> : <Chip tone="серый">загружен вами</Chip>}
                        </div>
                        {ш.description ? (
                          <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>{ш.description}</div>
                        ) : null}
                      </div>
                    </button>
                  ))}
                </CardBody>
              </Card>

              <Card>
                <CardHead icon="gear" iconColor="var(--blue-700)" title="Настройки урока"
                          aside={<span className="muted" style={{ fontSize: 12 }}>кнопками, без ручного ввода</span>} />
                <CardBody style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  <Выбор
                    подпись="Тип урока"
                    варианты={справочники.tip_uroka.map((т) => ({ значение: т, текст: т }))}
                    выбрано={опции.tip_uroka ?? null}
                    onВыбор={(з) => setОпции((п) => ({ ...п, tip_uroka: з }))}
                  />
                  <Выбор
                    подпись="Ценность «Адал азамат»"
                    цвет="var(--gold)"
                    варианты={справочники.cennosti.map((ц) => ({ значение: ц.key, текст: ц.name, подсказка: ц.goal }))}
                    выбрано={опции.cennost_key ?? null}
                    onВыбор={(з) => setОпции((п) => ({ ...п, cennost_key: з }))}
                  />
                  <Выбор
                    подпись="Проект «Адал азамат»"
                    цвет="var(--gold)"
                    варианты={справочники.adal_azamat_projects.map((п) => ({ значение: п.key, текст: п.name, подсказка: п.direction }))}
                    выбрано={опции.adal_azamat_project_key ?? null}
                    onВыбор={(з) => setОпции((п) => ({ ...п, adal_azamat_project_key: з }))}
                  />
                  <Выбор
                    подпись="Ориентация листа"
                    сбрасываемый={false}
                    варианты={[{ значение: 'book', текст: 'Книжная' }, { значение: 'album', текст: 'Альбомная' }]}
                    выбрано={опции.page_orientation ?? 'book'}
                    onВыбор={(з) => setОпции((п) => ({ ...п, page_orientation: (з ?? 'book') as 'book' | 'album' }))}
                  />

                  <div className="sep" />

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <Switch
                      checked={Boolean(опции.ima_oop)}
                      onChange={(v) => setОпции((п) => ({ ...п, ima_oop: v }))}
                      label="Раздел по ООП — действия по адаптации для особых образовательных потребностей"
                    />
                    <Switch
                      checked={Boolean(опции.sor_instead_of_reflection)}
                      onChange={(v) => setОпции((п) => ({ ...п, sor_instead_of_reflection: v }))}
                      label="СОР вместо рефлексии в конце урока"
                    />
                    <Switch
                      checked={Boolean(опции.fizkultminutka)}
                      onChange={(v) => setОпции((п) => ({ ...п, fizkultminutka: v }))}
                      label="Физкультминутка"
                    />
                  </div>
                </CardBody>
              </Card>
            </>
          ) : null}

          {шаг === 'сборка' ? (
            <Card>
              <CardHead icon="clock" iconColor="var(--blue-700)" title="Собираю черновик"
                        aside={<Chip>{задача?.status === 'running' ? 'в работе' : 'в очереди'}</Chip>} />
              <CardBody>
                <div className="bar"><i style={{ width: задача?.status === 'running' ? '70%' : '25%' }} /></div>
                <p style={{ fontSize: 14, lineHeight: 1.65, marginTop: 14 }}>
                  Сборка занимает около полуминуты. Страницу можно закрыть: работа идёт на сервере,
                  и если у вас привязан Telegram, готовый файл придёт туда же.
                </p>
                {задача?.status === 'done' ? (
                  <p style={{ fontSize: 14, lineHeight: 1.65, marginTop: 10 }}>
                    Готово. Файл лежит в истории документов.
                  </p>
                ) : null}
                <Button variant="тихая" style={{ marginTop: 14 }} onClick={() => { остановить.current?.(); setШаг('тема'); setЗадача(null); }}>
                  Собрать ещё один
                </Button>
              </CardBody>
            </Card>
          ) : null}
        </div>

        {справочники ? (
          <Предпросмотр
            школа={я?.profile?.school ?? null}
            педагог={я?.profile?.name ?? null}
            колонки={справочники.hod_uroka_columns}
            поля={[
              { подпись: 'Раздел', значение: поля.razdel },
              { подпись: 'Дата', значение: null },
              { подпись: 'Класс', значение: поля.klass },
              { подпись: 'Тема урока', значение: поля.topic },
              { подпись: 'Цели обучения', значение: поля.objective_code },
              { подпись: 'Цели урока', значение: null },
            ]}
          />
        ) : null}
      </div>
    </>
  );
}
