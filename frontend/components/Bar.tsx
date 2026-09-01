import type { ReactNode } from 'react';

/**
 * Полоска расхода: «сколько осталось на сегодня».
 *
 * Осознанно не считает: доля приходит готовой от FastAPI, который берёт
 * её из core/limits.py. Складывать и делить здесь нельзя — иначе бот и
 * веб покажут разные числа.
 */
type Props = {
  /** Доля заполнения от 0 до 1, посчитанная на сервере. */
  fill: number;
  tone?: 'синий' | 'голубой';
  label?: ReactNode;
  /** Правая подпись, например «3 / 5» — тоже приходит готовой строкой. */
  value?: ReactNode;
};

const SKY = 'linear-gradient(90deg,#22C4DC,#009FBB)';

export function Bar({ fill, tone = 'синий', label, value }: Props) {
  const width = `${Math.max(0, Math.min(1, fill)) * 100}%`;
  return (
    <div>
      {label || value ? (
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
          <span style={{ fontSize: 13 }}>{label}</span>
          <span className="mono" style={{ fontSize: 12.5, fontWeight: 600 }}>{value}</span>
        </div>
      ) : null}
      <div className="bar"><i style={{ width, background: tone === 'голубой' ? SKY : undefined }} /></div>
    </div>
  );
}
