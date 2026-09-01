'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Icon, type IconName } from './Icon';

/**
 * Нижняя панель телефона — пять разделов из макета «Дэшборд, телефон».
 * Пункт без href, как и в боковом меню, рисуется обычным блоком: страницы
 * приезжают своими блоками плана.
 */
type Tab = { key: string; icon: IconName; label: string; href?: string };

export const TABS: Tab[] = [
  { key: 'dash', icon: 'home', label: 'Дэшборд', href: '/' },
  { key: 'lesson', icon: 'mic', label: 'Урок' },
  { key: 'classes', icon: 'users', label: 'Классы' },
  { key: 'history', icon: 'file', label: 'История' },
  { key: 'more', icon: 'grid', label: 'Ещё' },
];

export function TabBar() {
  const path = usePathname();
  return (
    <nav className="tabbar">
      {TABS.map((tab) => {
        const on = tab.href === path;
        const inner = <><Icon name={tab.icon} size={22} /><span>{tab.label}</span></>;
        const cls = on ? 'tab on' : 'tab';
        return tab.href
          ? <Link key={tab.key} className={cls} href={tab.href} aria-current={on ? 'page' : undefined}>{inner}</Link>
          : <div key={tab.key} className={cls}>{inner}</div>;
      })}
    </nav>
  );
}
