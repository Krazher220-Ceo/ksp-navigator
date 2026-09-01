import Image from 'next/image';
import type { ReactNode } from 'react';

/**
 * Оболочка экранов входа и регистрации по макетам Vhod и Registraciya.
 *
 * Слева — фирменный фон с обещанием продукта, справа — форма. На узком
 * экране колонки складываются в то, что нарисовано в MobVhod: тёмная
 * шапка сверху и белый лист с формой под ней. Это один и тот же адрес,
 * а не отдельная мобильная версия — раскладка меняется по ширине.
 */
type Props = {
  заголовок: ReactNode;
  подзаголовок: ReactNode;
  /** Плитки под текстом: числа на входе, преимущества на регистрации. */
  слева?: ReactNode;
  /** Строка со щитом внизу тёмной колонки. */
  сноска?: ReactNode;
  ширинаФормы?: number;
  children: ReactNode;
};

export function AuthShell({ заголовок, подзаголовок, слева, сноска, ширинаФормы = 620, children }: Props) {
  return (
    <div className="auth" style={{ ['--auth-form' as string]: `${ширинаФормы}px` }}>
      <div className="auth-promo bg-kz">
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 11 }}>
          <Image className="mark" src="/mazmun-logo.png" width={38} height={38} alt="Mazmun" priority />
          <div>
            <div className="brand-name" style={{ fontSize: 16 }}>Mazmun</div>
            <div className="brand-sub">Костанай · Қазақстан</div>
          </div>
        </div>

        <div className="auth-promo-body">
          <h2 style={{ fontFamily: 'var(--serif)', fontSize: 38, lineHeight: 1.15, fontWeight: 700, letterSpacing: '-0.02em' }}>
            {заголовок}
          </h2>
          <p style={{ color: '#A9C6DE', fontSize: 15, marginTop: 16, lineHeight: 1.55 }}>{подзаголовок}</p>
          {слева}
        </div>

        {сноска ? (
          <div style={{ position: 'relative', marginTop: 34, display: 'flex', alignItems: 'center', gap: 9, color: '#7FA6C4', fontSize: 12 }}>
            {сноска}
          </div>
        ) : null}
      </div>

      <div className="auth-form">{children}</div>
    </div>
  );
}
