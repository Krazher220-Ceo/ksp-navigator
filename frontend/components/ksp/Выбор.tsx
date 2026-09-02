'use client';

import type { ReactNode } from 'react';

/**
 * Настройки урока в мастере сборки КСП задаются кнопками, а не вводом
 * с клавиатуры: варианты закреплены приказом и методичками, и свободный
 * текст здесь означал бы опечатку в официальном документе.
 */
type Props<T extends string> = {
  подпись: string;
  варианты: { значение: T; текст: string; подсказка?: string }[];
  выбрано: T | null;
  onВыбор: (значение: T | null) => void;
  /** Цвет выбранной кнопки: у ценностей он золотой, как в макете. */
  цвет?: string;
  /** Можно ли снять выбор повторным нажатием. */
  сбрасываемый?: boolean;
  children?: ReactNode;
};

export function Выбор<T extends string>({
  подпись, варианты, выбрано, onВыбор, цвет = 'var(--blue-700)', сбрасываемый = true, children,
}: Props<T>) {
  return (
    <div className="field">
      <span className="lbl">{подпись}</span>
      <div className="row" style={{ gap: 7, flexWrap: 'wrap' }}>
        {варианты.map((вариант) => {
          const выбран = выбрано === вариант.значение;
          return (
            <button
              key={вариант.значение}
              type="button"
              aria-pressed={выбран}
              title={вариант.подсказка}
              onClick={() => onВыбор(выбран && сбрасываемый ? null : вариант.значение)}
              className={выбран ? 'chip' : 'chip chip-grey'}
              style={{
                border: 0, fontSize: 12.5, padding: '6px 12px', cursor: 'pointer',
                background: выбран ? цвет : undefined,
                color: выбран ? '#fff' : undefined,
              }}
            >
              {вариант.текст}
            </button>
          );
        })}
        {children}
      </div>
    </div>
  );
}
