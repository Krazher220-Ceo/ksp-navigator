'use client';

import { useEffect, useState } from 'react';

/**
 * Оглавление раздела документации с подсветкой читаемой главы.
 *
 * То же, что components/landing/Оглавление.tsx, но список приходит
 * параметром: у каждого раздела свои главы. Наблюдатель один и тот же —
 * IntersectionObserver, слушателя scroll в проекте нет намеренно.
 */
export function ОглавлениеРаздела({ главы }: { главы: { якорь: string; текст: string }[] }) {
  const [активный, setАктивный] = useState<string | null>(null);

  useEffect(() => {
    const заголовки = главы
      .map((г) => document.getElementById(г.якорь))
      .filter((э): э is HTMLElement => э !== null);
    if (заголовки.length === 0) return;

    const наблюдатель = new IntersectionObserver(
      (записи) => {
        const видимые = записи.filter((з) => з.isIntersecting);
        if (видимые.length === 0) return;
        const верхний = видимые.reduce((а, б) =>
          а.boundingClientRect.top <= б.boundingClientRect.top ? а : б);
        setАктивный(верхний.target.id);
      },
      { rootMargin: '-84px 0px -68% 0px' },
    );
    заголовки.forEach((з) => наблюдатель.observe(з));
    return () => наблюдатель.disconnect();
  }, [главы]);

  return (
    <aside style={{ fontSize: 13 }}>
      <div className="lbl" style={{ marginBottom: 10 }}>На этой странице</div>
      <div className="toc">
        {главы.map((г) => (
          <a
            key={г.якорь} href={`#${г.якорь}`}
            className={активный === г.якорь ? 'toc-link on' : 'toc-link'}
            aria-current={активный === г.якорь ? 'true' : undefined}
          >{г.текст}</a>
        ))}
      </div>
    </aside>
  );
}
