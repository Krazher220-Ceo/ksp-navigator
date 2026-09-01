import type { ReactNode } from 'react';

/**
 * Полоска расхода: «сколько израсходовано из положенного».
 *
 * Оба числа приходят от FastAPI, который берёт их из core.dashboard и
 * core/limits.py, — здесь они не считаются и не пересчитываются. Ширина
 * заливки выводится из них делением, и это единственная арифметика во
 * всём кабинете: она про ширину прямоугольника, а не про продукт.
 * Разойтись с ботом на ней нельзя — подпись «3 / 5» бот и кабинет берут
 * из одного и того же ответа.
 */
type Props = {
  /** Израсходовано — число с сервера. */
  расход: number;
  /** Потолок — тоже с сервера. Ноль означает «потолка нет». */
  потолок: number;
  tone?: 'синий' | 'голубой';
  label?: ReactNode;
  /** Правая подпись; по умолчанию «расход / потолок». */
  value?: ReactNode;
};

const SKY = 'linear-gradient(90deg,#22C4DC,#009FBB)';

export function Bar({ расход, потолок, tone = 'синий', label, value }: Props) {
  const доля = потолок > 0 ? Math.max(0, Math.min(1, расход / потолок)) : 0;
  const подпись = value ?? `${расход} / ${потолок}`;
  return (
    <div>
      {label || value !== undefined || потолок > 0 ? (
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
          <span style={{ fontSize: 13 }}>{label}</span>
          <span className="mono" style={{ fontSize: 12.5, fontWeight: 600 }}>{подпись}</span>
        </div>
      ) : null}
      <div className="bar">
        <i style={{ width: `${доля * 100}%`, background: tone === 'голубой' ? SKY : undefined }} />
      </div>
    </div>
  );
}
