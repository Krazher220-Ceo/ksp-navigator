'use client';

import { useEffect, useState } from 'react';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи } from '@/lib/api';

/**
 * Шаблоны КСП, доступные педагогу.
 *
 * Зачем экран: пункт «Шаблоны» в меню кабинета вёл в никуда, хотя сами
 * шаблоны существуют и уже участвуют в мастере КСП — от выбранного
 * шаблона зависит, насколько подробным получится черновик. Не показать
 * их значило оставить человека выбирать вслепую в одном выпадающем
 * списке на шаге сборки.
 *
 * Чего экран осознанно не делает: не даёт загрузить шаблон отсюда.
 * Личный шаблон появляется иначе — из вашего же прошлого КСП, когда вы
 * присылаете его как образец. Заводить вторую дверь к тому же самому
 * значило бы объяснять человеку разницу, которой нет.
 */
const ДАТА = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });

type Шаблон = { id: number; name: string; source?: string | null; category?: string | null };

const ПОДПИСЬ: Record<string, string> = {
  personal: 'ваш образец',
  builtin: 'встроенный',
};

export default function ShablonyPage() {
  const [шаблоны, setШаблоны] = useState<Шаблон[] | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const данные = await апи.настройкиКсп();
        setШаблоны(данные.templates as Шаблон[]);
      } catch (сбой) {
        setОшибка(сбой instanceof ОшибкаApi ? сбой.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, []);

  return (
    <>
      <TopBar
        date={ДАТА.format(new Date())}
        title="Шаблоны"
        searchPlaceholder="Название шаблона"
      />
      <div className="body" style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14, overflowY: 'auto' }}>
        <Card>
          <CardHead
            icon="layers" iconColor="var(--blue-700)" title="Доступные шаблоны"
            aside={<Chip>{шаблоны === null ? '—' : `${шаблоны.length}`}</Chip>}
          />
          <CardBody>
            {ошибка ? (
              <p style={{ color: 'var(--red)', fontSize: 13.5 }}>{ошибка}</p>
            ) : шаблоны === null ? (
              <p className="muted" style={{ fontSize: 13.5 }}>Собираю данные…</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {шаблоны.map((шаблон) => (
                  <div key={шаблон.id} className="card" style={{ padding: '13px 15px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                    <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 2 }}><Icon name="doc" size={18} /></span>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: 14, fontWeight: 600 }}>{шаблон.name}</div>
                      {шаблон.source ? (
                        <div className="muted" style={{ fontSize: 12.5, marginTop: 3, lineHeight: 1.5 }}>
                          Источник: {шаблон.source}
                        </div>
                      ) : null}
                    </div>
                    {шаблон.category ? (
                      <Chip style={{ flex: 'none' }}>{ПОДПИСЬ[шаблон.category] ?? шаблон.category}</Chip>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHead icon="spark" iconColor="var(--blue-700)" title="Откуда берётся ваш личный шаблон" />
          <CardBody>
            <p style={{ fontSize: 14, lineHeight: 1.65 }}>
              Пришлите боту своё готовое КСП — то, которое у вас уже приняли. Система разберёт его
              и запомнит вашу манеру: какие разделы вы заполняете, насколько подробно расписываете
              ход урока, что пишете в оценивании. Следующие черновики будут собираться по этому
              образцу, а не по общему.
            </p>
            <p className="muted" style={{ fontSize: 12.5, marginTop: 10, lineHeight: 1.55 }}>
              Шаблон виден только вам. Ни администрация школы, ни другие педагоги доступа к нему
              не имеют.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
