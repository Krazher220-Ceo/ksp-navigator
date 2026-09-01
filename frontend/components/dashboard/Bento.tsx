import type { CSSProperties, ReactNode } from 'react';

/**
 * Ячейка бенто-сетки дэшборда. Ширина считается в колонках
 * двенадцатиколоночной сетки — как размечен артборд «Дэшборд педагога».
 */
export function Cell({ колонок, className, style, children }: {
  колонок: number; className?: string; style?: CSSProperties; children: ReactNode;
}) {
  return (
    <section
      className={className}
      style={{ gridColumn: `span ${колонок}`, minWidth: 0, ...style }}
    >
      {children}
    </section>
  );
}
