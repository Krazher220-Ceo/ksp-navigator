import Image from 'next/image';
import Link from 'next/link';

/**
 * Подвал публичных страниц.
 *
 * Реквизитов здесь нет: юрлицо для оферты ещё не оформлено
 * (design/CONTEXT.md, раздел 5, пункт 3 — автору 16 лет, ИП оформляется
 * на законного представителя). Ставить в подвал квадратные скобки
 * нельзя, поэтому строки просто нет, пока нет её содержимого.
 */
export function SiteFooter() {
  return (
    <footer style={{
      background: '#081C2E', color: '#7FA6C4', padding: 'clamp(22px, 3.2vw, 34px) var(--pad-x)',
      display: 'flex', alignItems: 'center', gap: 16, fontSize: 13, flexWrap: 'wrap',
    }}>
      <div className="row" style={{ gap: 10 }}>
        <Image className="mark" src="/mazmun-logo.png" width={28} height={28} alt="" />
        <span style={{ color: '#CDE2F0' }}>Mazmun</span>
      </div>
      <div className="row foot-links" style={{ gap: 22, marginLeft: 34 }}>
        <a href="/#tarify" style={{ color: 'inherit' }}>Тарифы</a>
        <Link href="/docs" style={{ color: 'inherit' }}>Документация</Link>
        <a href="/#dannye" style={{ color: 'inherit' }}>Данные и приватность</a>
        <a href="/#shkola" style={{ color: 'inherit' }}>Для школы</a>
      </div>
      <span style={{ marginLeft: 'auto' }}>© 2026 · Костанай, Казахстан</span>
    </footer>
  );
}
