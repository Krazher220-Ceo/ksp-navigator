'use client';

import { useEffect, useRef } from 'react';

/**
 * Показывает секцию, когда до неё долистали.
 *
 * Через IntersectionObserver и только через него: слушатель scroll
 * срабатывает на каждый пиксель прокрутки и на телефоне съедает
 * плавность — ровно та ловушка, о которой предупреждает план блока Ф13.
 *
 * Что осознанно не делает: не прячет секцию обратно при уходе из окна —
 * анимация появления должна отыграть один раз. И не решает, двигать ли
 * что-то вообще: человека, попросившего систему не анимировать,
 * обслуживает media-запрос prefers-reduced-motion в app/landing.css.
 */
type Props = {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  /** Какая часть секции должна попасть в окно, чтобы она проснулась. */
  порог?: number;
  as?: 'section' | 'div' | 'header' | 'footer';
};

export function Reveal({ children, className, style, порог = 0.12, as = 'section' }: Props) {
  const узел = useRef<HTMLElement>(null);

  useEffect(() => {
    const элемент = узел.current;
    if (!элемент) return;
    // Без IntersectionObserver (очень старый браузер) секция просто
    // показывается сразу — лучше без анимации, чем пустая страница.
    if (typeof IntersectionObserver === 'undefined') {
      элемент.classList.add('видна');
      return;
    }
    const наблюдатель = new IntersectionObserver(
      (записи) => {
        for (const запись of записи) {
          if (запись.isIntersecting) {
            запись.target.classList.add('видна');
            наблюдатель.unobserve(запись.target);
          }
        }
      },
      { threshold: порог },
    );
    наблюдатель.observe(элемент);
    return () => наблюдатель.disconnect();
  }, [порог]);

  const Тег = as;
  return (
    <Тег ref={узел as never} className={['reveal', className].filter(Boolean).join(' ')} style={style}>
      {children}
    </Тег>
  );
}
