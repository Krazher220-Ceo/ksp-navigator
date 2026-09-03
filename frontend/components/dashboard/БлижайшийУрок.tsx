import Link from 'next/link';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import type { Дэшборд } from '@/lib/api';

/**
 * Второй вопрос дэшборда: что сделать сегодня.
 *
 * Показывает ближайший урок из КТП, для которого черновика КСП ещё нет.
 * Список считает core.dashboard, здесь берётся его первый элемент.
 *
 * Чего в карточке нет и почему: ни класса, ни номера урока, ни кабинета.
 * В артборде они нарисованы, а в базе их нет — `ktp_entries` хранит тему
 * и planned_date, и всё. Подставить правдоподобное «10 «А» · кабинет
 * 214» значило бы показать педагогу выдуманное расписание.
 */
function датаПоРусски(iso: string): string {
  // Дата урока — обычная календарная дата, и читает её человек в своём
  // часовом поясе. Продуктовых чисел здесь нет, считать нечего.
  const д = new Date(`${iso}T00:00:00`);
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'long' }).format(д);
}

export function БлижайшийУрок({ данные }: { данные: Дэшборд }) {
  const урок = данные.upcoming_lessons_without_ksp[0];

  return (
    <section
      className="bg-kz"
      style={{
        borderRadius: 18, padding: '22px 24px', color: '#fff', display: 'flex',
        flexDirection: 'column', position: 'relative', overflow: 'hidden',
        boxShadow: 'var(--lift-2)', height: '100%',
      }}
    >
      <div style={{ position: 'absolute', top: -70, right: -40, width: 250, height: 250, borderRadius: '50%', background: 'radial-gradient(circle,rgba(0,175,202,.28),transparent 68%)' }} />

      {урок ? (
        <>
          <div className="row" style={{ position: 'relative', gap: 9 }}>
            <Chip icon="cal" style={{ background: 'rgba(0,175,202,.18)', color: '#8FE3F2' }}>
              {датаПоРусски(урок.planned_date)}
            </Chip>
            <span className="muted" style={{ color: '#8FB6D2', fontSize: 12.5 }}>из вашего КТП</span>
          </div>
          <h2 style={{ position: 'relative', fontFamily: 'var(--serif)', fontSize: 28, lineHeight: 1.15, letterSpacing: '-0.025em', marginTop: 14, maxWidth: 400 }}>
            {урок.topic}
          </h2>
          <div className="row" style={{ position: 'relative', gap: 10, marginTop: 'auto', paddingTop: 20 }}>
            <Link href="/app/urok">
              <Button
                icon="mic" arrow
                style={{ background: 'linear-gradient(180deg,#1ECBE4,#009FBB)', color: '#052A38', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.5),0 10px 22px -10px rgba(0,175,202,.85)' }}
              >
                Начать запись
              </Button>
            </Link>
            <Link href="/app/ksp">
              <Button icon="doc" style={{ background: 'rgba(255,255,255,.09)', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.13)' }}>
                Собрать КСП заранее
              </Button>
            </Link>
          </div>
        </>
      ) : (
        <>
          <div className="row" style={{ position: 'relative', gap: 9 }}>
            <Chip style={{ background: 'rgba(0,175,202,.18)', color: '#8FE3F2' }}>
              {данные.has_profile ? 'Ближайших уроков без плана нет' : 'Профиль ещё не заведён'}
            </Chip>
          </div>
          <h2 style={{ position: 'relative', fontFamily: 'var(--serif)', fontSize: 28, lineHeight: 1.15, letterSpacing: '-0.025em', marginTop: 14, maxWidth: 420 }}>
            {данные.has_profile ? 'Всё запланированное собрано' : 'Заполните профиль — и появится ваше КТП'}
          </h2>
          <p style={{ position: 'relative', color: '#A9C6DE', fontSize: 14, lineHeight: 1.6, marginTop: 10, maxWidth: 440 }}>
            {данные.has_profile
              ? 'Урок можно записать и без плана: конспект соберётся из записи, а черновик КСП — по конспекту.'
              : 'Пока профиля нет, показываем только общее: расход и живучесть сервера. Числа появятся вместе с КТП.'}
          </p>
          <div className="row" style={{ position: 'relative', gap: 10, marginTop: 'auto', paddingTop: 20 }}>
            <Link href="/app/urok">
              <Button
                icon="mic" arrow
                style={{ background: 'linear-gradient(180deg,#1ECBE4,#009FBB)', color: '#052A38', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.5),0 10px 22px -10px rgba(0,175,202,.85)' }}
              >
                Записать урок
              </Button>
            </Link>
          </div>
        </>
      )}

      {данные.unparsed_planned_dates > 0 ? (
        <div className="row" style={{ position: 'relative', gap: 9, marginTop: 20, paddingTop: 16, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.09)', color: '#8FB6D2', fontSize: 12.5 }}>
          <Icon name="warn" size={15} />
          <span>
            В КТП {данные.unparsed_planned_dates} строк(и) с нераспознанной датой — их нет в списке ближайших.
          </span>
        </div>
      ) : null}
    </section>
  );
}
