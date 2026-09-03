import Image from 'next/image';
import Link from 'next/link';
import { Button } from '@/components/Button';

/**
 * Шапка публичных страниц.
 *
 * Переключатель ҚАЗ/RU нарисован выключенным намеренно: казахской
 * версии интерфейса нет, а её открытие — решение автора
 * (FRONTEND_PLAN.md, раздел 6, пункт 5). Кнопка, которая ничего не
 * делает, честнее отсутствующей: человек видит, что язык учтён.
 */
const РАЗДЕЛЫ = [
  { текст: 'Как это работает', href: '#kak' },
  { текст: 'Тарифы', href: '#tarify' },
  { текст: 'Для школы', href: '#shkola' },
  { текст: 'Документация', href: '/docs' },
];

export function SiteNav() {
  return (
    <nav style={{
      height: 70, borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center',
      gap: 12, padding: '0 var(--pad-x)', background: 'rgba(255,255,255,.9)',
      position: 'sticky', top: 0, zIndex: 20, backdropFilter: 'blur(14px)',
    }}>
      <Link className="row" style={{ gap: 10 }} href="/">
        <Image className="mark" src="/mazmun-logo.png" width={32} height={32} alt="Mazmun" priority />
        <div>
          <div className="brand-name" style={{ color: 'var(--ink)', fontSize: 15 }}>Mazmun</div>
          <div className="brand-sub" style={{ color: 'var(--ink-3)' }}>Костанай · Қазақстан</div>
        </div>
      </Link>
      <div className="row nav-sections" style={{ gap: 26, marginLeft: 44, fontSize: 14, color: 'var(--ink-2)' }}>
        {РАЗДЕЛЫ.map((р) => <a key={р.href} href={р.href} style={{ color: 'inherit' }}>{р.текст}</a>)}
      </div>
      <div className="row" style={{ marginLeft: 'auto', gap: 12 }}>
        <div className="row nav-lang" style={{ gap: 3, background: 'rgba(11,26,43,.05)', borderRadius: 9, padding: 3 }}>
          <span style={{ fontSize: 12, fontWeight: 600, padding: '4px 9px', borderRadius: 6, background: '#fff', boxShadow: 'var(--lift)' }}>RU</span>
          <span
            style={{ fontSize: 12, padding: '4px 9px', color: 'var(--ink-3)', opacity: 0.5 }}
            title="Казахская версия готовится"
            aria-disabled
          >ҚАЗ</span>
        </div>
        <Link href="/vhod"><Button variant="строкой">Войти</Button></Link>
        <Link href="/registraciya" className="nav-cta"><Button arrow>Записать первый урок</Button></Link>
      </div>
    </nav>
  );
}
