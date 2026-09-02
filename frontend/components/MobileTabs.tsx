'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Icon, type IconName } from './Icon';
import { NAV, NAV_УЧЕНИКА, type Роль } from './Sidebar';

/**
 * Нижние вкладки кабинета на телефоне.
 *
 * Зачем модуль: боковое меню шириной 250px на телефоне съедало пол-экрана,
 * и никакой другой навигации не было — кабинет на телефоне открывался
 * без вкладок вовсе. Здесь ровно они: четыре частых раздела внизу и
 * «Ещё» с полным списком.
 *
 * Что осознанно не делает: не решает, что человеку доступно. Список
 * берётся из того же NAV, что и боковое меню (components/Sidebar.tsx),
 * поэтому ученик и на телефоне видит только своё, а разойтись эти два
 * меню не могут — источник один.
 *
 * На что опирается: правила .tabbar/.tab из макета (app/base.css) и
 * @media в нём же — на широком экране этой панели нет.
 */
type Вкладка = { key: string; icon: IconName; label: string; href: string };

const ВКЛАДКИ_ПЕДАГОГА: Вкладка[] = [
  { key: 'dash', icon: 'home', label: 'Дэшборд', href: '/app' },
  { key: 'lesson', icon: 'mic', label: 'Урок', href: '/app/urok' },
  { key: 'ksp', icon: 'doc', label: 'КСП', href: '/app/ksp' },
  { key: 'history', icon: 'file', label: 'История', href: '/app/istoriya' },
];

const ВКЛАДКИ_УЧЕНИКА: Вкладка[] = [
  { key: 'sverka', icon: 'cam', label: 'Сверка', href: '/app/sverka' },
];

export function MobileTabs({ роль = 'teacher' }: { роль?: Роль }) {
  const путь = usePathname();
  const [открыто, setОткрыто] = useState(false);
  const вкладки = роль === 'student' ? ВКЛАДКИ_УЧЕНИКА : ВКЛАДКИ_ПЕДАГОГА;
  const группы = роль === 'student' ? NAV_УЧЕНИКА : NAV;

  // Переход по ссылке закрывает «Ещё»: иначе список остаётся поверх
  // страницы, на которую человек только что ушёл.
  useEffect(() => setОткрыто(false), [путь]);

  return (
    <>
      {открыто ? (
        <div className="sheet-back" onClick={() => setОткрыто(false)}>
          <div className="sheet" onClick={(с) => с.stopPropagation()} role="dialog" aria-label="Все разделы">
            <div className="sheet-grip" />
            <div className="lbl" style={{ padding: '0 16px 8px' }}>Все разделы</div>
            {группы.map((группа) => (
              <div key={группа.cap}>
                <div className="nav-cap" style={{ color: 'var(--ink-3)', padding: '10px 16px 4px' }}>{группа.cap}</div>
                {группа.items.map((пункт) => {
                  const внутри = (
                    <>
                      <span style={{ color: 'var(--blue-700)' }}><Icon name={пункт.icon} size={18} /></span>
                      <span style={{ fontSize: 14, fontWeight: 500 }}>{пункт.label}</span>
                      {пункт.locked ? <span className="muted" style={{ marginLeft: 'auto', fontSize: 11.5 }}>скоро</span> : null}
                    </>
                  );
                  return пункт.href ? (
                    <Link key={пункт.key} className="sheet-row row" href={пункт.href}>{внутри}</Link>
                  ) : (
                    <div key={пункт.key} className="sheet-row row" style={{ opacity: 0.55 }}>{внутри}</div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <nav className="tabbar tabbar-fixed" aria-label="Разделы кабинета">
        {вкладки.map((вкладка) => {
          const на = вкладка.href === путь;
          return (
            <Link
              key={вкладка.key} href={вкладка.href}
              className={на ? 'tab on' : 'tab'} aria-current={на ? 'page' : undefined}
            >
              <Icon name={вкладка.icon} size={22} />
              <span>{вкладка.label}</span>
            </Link>
          );
        })}
        <button type="button" className={открыто ? 'tab on' : 'tab'} onClick={() => setОткрыто((б) => !б)} aria-expanded={открыто}>
          <Icon name="grid" size={22} />
          <span>Ещё</span>
        </button>
      </nav>
    </>
  );
}
