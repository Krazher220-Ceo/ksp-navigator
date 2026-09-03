import Link from 'next/link';
import { Button } from '@/components/Button';
import { Card, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import type { Дэшборд } from '@/lib/api';

/**
 * Продолжение второго вопроса: что ещё предстоит.
 *
 * Список приходит из core.dashboard уже отсортированным и обрезанным до
 * пяти — сортировать и резать здесь нечего.
 */
function день(iso: string): { число: string; день: string } {
  const д = new Date(`${iso}T00:00:00`);
  return {
    число: String(д.getDate()).padStart(2, '0'),
    день: new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(д).toUpperCase().slice(0, 2),
  };
}

export function УрокиБезПлана({ данные }: { данные: Дэшборд }) {
  const уроки = данные.upcoming_lessons_without_ksp;
  return (
    <Card style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <CardHead
        icon="cal" iconColor="var(--blue-700)" title="Уроки без плана"
        aside={<span className="muted" style={{ fontSize: 12.5 }}>всего {данные.ktp_coverage.not_covered}</span>}
      >
        {уроки.length ? <Chip tone="золотой">ближайшие {уроки.length}</Chip> : null}
      </CardHead>

      {уроки.length ? (
        <div style={{ padding: '6px 10px 10px', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {уроки.map((урок, i) => {
            const д = день(урок.planned_date);
            return (
              <div key={`${урок.planned_date}-${урок.topic}`}>
                {i > 0 ? <div className="sep" /> : null}
                <div className="row" style={{ padding: '12px 8px', gap: 14 }}>
                  <div style={{ width: 46, flex: 'none', textAlign: 'center' }}>
                    <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1, letterSpacing: '-0.03em' }}>{д.число}</div>
                    <div className="muted" style={{ fontSize: 10, letterSpacing: '.1em' }}>{д.день}</div>
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{урок.topic}</div>
                    <div className="row muted" style={{ fontSize: 12.5, gap: 7, marginTop: 2 }}>
                      <span>из КТП</span>
                    </div>
                  </div>
                  <Link href="/app/ksp">
                    <Button size="малая" variant={i === 0 ? 'основная' : 'тихая'}>Собрать</Button>
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ padding: '18px' }}>
          <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
            {данные.has_profile
              ? 'Ближайших уроков без черновика КСП не нашлось. Если КТП ещё не загружен — пришлите его боту командой /upload_ktp.'
              : 'Список появится, когда будет профиль и КТП.'}
          </p>
        </div>
      )}
    </Card>
  );
}
