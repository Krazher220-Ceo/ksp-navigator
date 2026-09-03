'use client';

import { useEffect, useState } from 'react';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { StatTile } from '@/components/StatTile';
import { TopBar } from '@/components/TopBar';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, type Аналитика } from '@/lib/api';

/**
 * Аналитика продукта — закрытый экран (блок Ф14).
 *
 * Открывается только при снятых двух замках: флаге окружения и записи в
 * admin_access. Проверяет их сервер; если он отказал — здесь просто
 * ничего нет, и это не ошибка интерфейса.
 *
 * Показываются только числа. Ни имён педагогов, ни тем уроков, ни
 * текстов документов сюда не приходит — сервер их и не отдаёт.
 * Рейтинга педагогов нет и не появится: как только аналитика учебного
 * процесса превращается в оценку человека, доступ в школы закрывается.
 */
const ДНИ = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

const ПОДПИСИ_ДЕЙСТВИЙ: Record<string, string> = {
  generate_ksp: 'Черновики КСП',
  generate_ktp: 'КТП на год',
  generate_konspekt: 'Конспекты',
  transcribe: 'Расшифровки',
  sverka_tetradi: 'Сверки тетради',
  parse_ksp: 'Разбор КСП педагога',
};

export default function AnalitikaPage() {
  const [данные, setДанные] = useState<Аналитика | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setДанные(await апи.аналитика());
      } catch (e) {
        setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, []);

  const максимум = данные ? Math.max(1, ...данные.by_weekday) : 1;

  return (
    <>
      <TopBar date="Команда проекта" title="Аналитика продукта" searchPlaceholder="Тема, класс или код цели" />

      <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 14, overflowY: 'auto' }}>
        {ошибка ? (
          <Card style={{ padding: '13px 16px', display: 'flex', gap: 12 }}>
            <span style={{ color: 'var(--ink-3)', flex: 'none' }}><Icon name="lock" size={18} /></span>
            <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>{ошибка}</div>
          </Card>
        ) : null}

        {данные ? (
          <>
            <Card style={{ background: 'var(--blue-soft)' }}>
              <CardBody style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 1 }}><Icon name="eye" size={18} /></span>
                <p style={{ fontSize: 13.5, lineHeight: 1.6 }}>
                  Здесь только события приложения: какое действие, когда, чем закончилось. Ни имён,
                  ни тем уроков, ни текстов документов. Рейтинга педагогов нет и не будет.
                </p>
              </CardBody>
            </Card>

            <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 150px), 1fr))' }}>
              <StatTile label="Педагогов работают" value={данные.active_teachers}
                        hint={`собрали документ за ${данные.window_days} дней`} icon="users" iconColor="var(--blue-700)" />
              <StatTile label="Вернулись" value={данные.returned_teachers}
                        hint="работали и на этой неделе, и раньше" icon="refresh" iconColor="var(--green)" />
              <StatTile label="Конспектов" value={данные.documents.konspekt} hint="за всё время" icon="file" iconColor="var(--blue-700)" />
              <StatTile label="Черновиков КСП" value={данные.documents.ksp} hint="за всё время" icon="doc" iconColor="var(--blue-700)" />
            </div>

            <Card>
              <CardHead icon="layers" iconColor="var(--blue-700)" title="Чем пользуются" />
              <CardBody>
                {Object.entries(данные.actions).length ? Object.entries(данные.actions).map(([тип, значения]) => (
                  <div key={тип} className="row" style={{ gap: 12, padding: '9px 0' }}>
                    <span style={{ fontSize: 13.5, minWidth: 190 }}>{ПОДПИСИ_ДЕЙСТВИЙ[тип] ?? тип}</span>
                    <div className="bar" style={{ flex: 1 }}>
                      <i style={{ width: `${(значения.всего / Math.max(1, ...Object.values(данные.actions).map((з) => з.всего))) * 100}%` }} />
                    </div>
                    <span className="mono" style={{ fontSize: 12.5, minWidth: 96, textAlign: 'right' }}>
                      {значения.done} из {значения.всего}
                    </span>
                    {значения.failed ? <Chip tone="красный">{значения.failed} не вышло</Chip> : null}
                  </div>
                )) : (
                  <p className="muted" style={{ fontSize: 13.5 }}>Событий пока нет.</p>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHead icon="clock" iconColor="var(--blue-700)" title="Когда работают"
                        aside={<span className="muted" style={{ fontSize: 12 }}>за {данные.window_days} дней</span>} />
              <CardBody>
                <div className="row" style={{ gap: 10, alignItems: 'flex-end', height: 140 }}>
                  {данные.by_weekday.map((число, день) => (
                    <div key={ДНИ[день]} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                      <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{число}</span>
                      <div style={{
                        width: '100%', height: `${(число / максимум) * 100}%`, minHeight: 3,
                        background: 'linear-gradient(180deg,#2C86CE,#16558D)', borderRadius: 6,
                      }} />
                      <span className="muted" style={{ fontSize: 11 }}>{ДНИ[день]}</span>
                    </div>
                  ))}
                </div>
                <p className="muted" style={{ fontSize: 12, marginTop: 12, lineHeight: 1.55 }}>
                  Распределение действий по дням недели. Без привязки к человеку — только сколько
                  всего действий пришлось на каждый день.
                </p>
              </CardBody>
            </Card>
          </>
        ) : null}
      </div>
    </>
  );
}
