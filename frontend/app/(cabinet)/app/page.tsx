'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { Icon } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { Cell } from '@/components/dashboard/Bento';
import { БлижайшийУрок } from '@/components/dashboard/БлижайшийУрок';
import { Готово } from '@/components/dashboard/Готово';
import { Расход } from '@/components/dashboard/Расход';
import { Тревога } from '@/components/dashboard/Тревога';
import { УрокиБезПлана } from '@/components/dashboard/УрокиБезПлана';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, type Дэшборд, type Я } from '@/lib/api';

/**
 * Дэшборд педагога по артборду «Дэшборд педагога».
 *
 * Три вопроса и ровно в этом порядке: что сломано → что сделать
 * сегодня → что уже готово. Первый показывается, только когда есть о
 * чём тревожиться: пустая карточка «всё хорошо» на первом месте отучает
 * смотреть на это место вообще.
 *
 * Ни одного числа страница не считает. Всё, что показано, посчитал
 * core/dashboard.py, и те же числа видит бот в текстовой сводке.
 */
export default function DashboardPage() {
  const [данные, setДанные] = useState<Дэшборд | null>(null);
  const [я, setЯ] = useState<Я | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [обновляется, setОбновляется] = useState(false);

  const загрузить = useCallback(async () => {
    setОбновляется(true);
    setОшибка(null);
    try {
      const [профиль, дэшборд] = await Promise.all([апи.я(), апи.дэшборд()]);
      setЯ(профиль);
      setДанные(дэшборд);
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setОбновляется(false);
    }
  }, []);

  useEffect(() => { void загрузить(); }, [загрузить]);

  // Сегодняшняя дата — обычный календарь читающего, а не продуктовое
  // число: её и считает браузер, в часовом поясе самого педагога.
  const сегодня = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })
    .format(new Date());
  const обращение = я?.profile?.name ? `Здравствуйте, ${я.profile.name}` : 'Здравствуйте';

  return (
    <>
      <TopBar
        date={сегодня[0].toUpperCase() + сегодня.slice(1)}
        title={обращение}
        searchPlaceholder="Тема, класс или код цели"
        hasAlerts={Boolean(данные && данные.queue.failed_7d > 0)}
        action={<Link href="/app/urok"><Button icon="mic" arrow>Записать урок</Button></Link>}
      />

      <div className="body" style={{ display: 'grid', gridTemplateColumns: 'repeat(12,minmax(0,1fr))', gridTemplateRows: 'auto 1fr', gap: 14, minHeight: 0 }}>
        {ошибка ? (
          <Cell колонок={12}>
            <Card style={{ padding: '16px 18px', display: 'flex', gap: 13 }}>
              <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={20} /></span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, lineHeight: 1.6 }}>{ошибка}</div>
                <Button size="малая" variant="тихая" style={{ marginTop: 10 }} onClick={() => void загрузить()}>
                  Повторить
                </Button>
              </div>
            </Card>
          </Cell>
        ) : null}

        {данные ? (
          <>
            <Cell колонок={7}><БлижайшийУрок данные={данные} /></Cell>
            <Cell колонок={5} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <Тревога данные={данные} />
              <Расход данные={данные} />
            </Cell>
            <Cell колонок={7}><УрокиБезПлана данные={данные} /></Cell>
            <Cell колонок={5}><Готово данные={данные} /></Cell>

            <Cell колонок={12}>
              <div className="row" style={{ gap: 10, color: 'var(--ink-3)', fontSize: 12 }}>
                <Icon name="clock" size={14} />
                <span>Данные на {данные.generated_at_label}</span>
                <Button
                  size="малая" variant="строкой" icon="refresh"
                  disabled={обновляется} onClick={() => void загрузить()}
                >
                  Обновить
                </Button>
              </div>
            </Cell>
          </>
        ) : null}

        {!данные && !ошибка ? (
          <Cell колонок={12}>
            <p className="muted" style={{ fontSize: 13.5 }}>Собираю данные…</p>
          </Cell>
        ) : null}
      </div>
    </>
  );
}
