import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';
import type { Дэшборд } from '@/lib/api';

/**
 * Первый из трёх вопросов дэшборда: что сломано.
 *
 * Показывается только когда действительно сломано. Пустая карточка
 * «всё хорошо» на первом месте отучает смотреть на это место вообще.
 *
 * Ничего не считает: и число провалов, и время простоя приходят из
 * core.dashboard уже посчитанными.
 */
export function Тревога({ данные }: { данные: Дэшборд }) {
  const провалов = данные.queue.failed_7d;
  const инцидент = данные.uptime.last_incident;
  const открытыйИнцидент = инцидент && !инцидент.ended_at ? инцидент : null;

  if (!провалов && !открытыйИнцидент) return null;

  return (
    <section
      className="card"
      style={{
        background: 'linear-gradient(180deg,#FCEFEC,#FAEAE7)',
        boxShadow: '0 0 0 1px rgba(169,58,49,.13),0 12px 26px -20px rgba(169,58,49,.5)',
        padding: '16px 18px', display: 'flex', gap: 13,
      }}
    >
      <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={21} /></span>
      <div style={{ minWidth: 0, flex: 1 }}>
        {открытыйИнцидент ? (
          <>
            <div style={{ fontWeight: 600, fontSize: 14 }}>Сервер сейчас недоступен</div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 2, lineHeight: 1.5 }}>
              Причина: {открытыйИнцидент.reason}. Начало: {открытыйИнцидент.started_at}.
            </div>
          </>
        ) : (
          <>
            <div style={{ fontWeight: 600, fontSize: 14 }}>
              {провалов === 1 ? 'Одна задача не выполнилась' : `Задач не выполнилось: ${провалов}`}
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 2, lineHeight: 1.5 }}>
              За последние семь дней. Расход по тарифу за неудачные попытки не списан.
            </div>
          </>
        )}
        <div className="row" style={{ gap: 8, marginTop: 11 }}>
          <Button
            size="малая"
            style={{ background: 'linear-gradient(180deg,#BC463C,#9C332B)', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.2)' }}
          >
            Повторить
          </Button>
          <Button size="малая" variant="тихая">Что случилось</Button>
        </div>
      </div>
    </section>
  );
}
