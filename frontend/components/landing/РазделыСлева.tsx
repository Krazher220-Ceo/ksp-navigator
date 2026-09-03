import Link from 'next/link';
import { РАЗДЕЛЫ_ДОКУМЕНТАЦИИ } from '@/content/docs';

/**
 * Список разделов документации слева.
 *
 * Все восемь — ссылки. До 02.09.2026 семь из них были неактивным
 * текстом: страниц не существовало, и правило проекта «ссылок в никуда
 * не будет» держало их выключенными. Теперь страницы есть.
 *
 * «Аналитика продукта» ведёт на /docs — она свёрстана по своему
 * артборду и живёт отдельной страницей.
 */
export function РазделыСлева({ активный }: { активный: string }) {
  return (
    <aside style={{ fontSize: 13.5 }}>
      <div className="lbl" style={{ marginBottom: 10 }}>Разделы</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {РАЗДЕЛЫ_ДОКУМЕНТАЦИИ.map((р) => {
          const открыт = р.ключ === активный;
          const адрес = р.ключ === 'analitika' ? '/docs' : `/docs/${р.ключ}`;
          return (
            <Link
              key={р.ключ} href={адрес}
              aria-current={открыт ? 'page' : undefined}
              style={{
                padding: '7px 10px', borderRadius: 7,
                color: открыт ? 'var(--blue-800)' : 'var(--ink-2)',
                background: открыт ? 'var(--blue-soft)' : undefined,
                fontWeight: открыт ? 600 : undefined,
              }}
            >{р.название}</Link>
          );
        })}
      </div>
    </aside>
  );
}
