'use client';

import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { Icon, type IconName } from './Icon';

/**
 * Боковое меню кабинета — один в один из макета «Дэшборд педагога».
 *
 * Активный пункт — залитая таблетка с внутренним бликом. Полоски слева
 * здесь нет и не появится: этот вариант автор отверг.
 *
 * Пункт без href рисуется как в артборде — обычным блоком, не ссылкой.
 * Так пункты ведут себя ровно до того блока, который добавит их страницу;
 * ссылок в никуда в кабинете не будет.
 *
 * Осознанно не делает: не решает, что человеку показывать. Скрытие
 * пунктов по роли — это блок Ф3, и делаться оно будет отсутствием пункта,
 * а не его отключением.
 */
type Item = { key: string; icon: IconName; label: string; href?: string; locked?: boolean; badge?: string };
type Group = { cap: string; items: Item[] };

export const NAV: Group[] = [
  { cap: 'Работа', items: [
    { key: 'dash', icon: 'home', label: 'Дэшборд', href: '/' },
    { key: 'konspekt', icon: 'mic', label: 'Конспект урока' },
    { key: 'ksp', icon: 'doc', label: 'Собрать КСП' },
    { key: 'ktp', icon: 'cal', label: 'Собрать КТП' },
  ] },
  { cap: 'Класс', items: [
    { key: 'classes', icon: 'users', label: 'Мои классы' },
  ] },
  { cap: 'Материалы', items: [
    { key: 'templates', icon: 'layers', label: 'Шаблоны' },
    { key: 'history', icon: 'file', label: 'История' },
  ] },
  { cap: 'Аналитика', items: [
    // Покрытие программы — этап 3, отложен автором до сентября 2026.
    { key: 'coverage', icon: 'chart', label: 'Покрытие программы', locked: true },
  ] },
  { cap: 'Аккаунт', items: [
    { key: 'tariff', icon: 'card', label: 'Тариф и оплата' },
  ] },
];

type Props = { userName: string; userInitials: string; userTariff: string };

export function Sidebar({ userName, userInitials, userTariff }: Props) {
  const path = usePathname();
  return (
    <aside className="side">
      <div className="brand">
        <Image className="mark" src="/mazmun-logo.png" width={34} height={34} alt="Mazmun" priority />
        <div>
          <div className="brand-name">Mazmun</div>
          <div className="brand-sub">Қазақстан</div>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((group) => (
          <div key={group.cap} style={{ display: 'contents' }}>
            <div className="nav-cap">{group.cap}</div>
            {group.items.map((item) => {
              const on = item.href === path;
              const inner = (
                <>
                  <Icon name={item.icon} size={19} />
                  <span>{item.label}</span>
                  {item.locked ? <span style={{ marginLeft: 'auto', color: '#52738E' }}><Icon name="lock" size={15} /></span> : null}
                  {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
                </>
              );
              const cls = on ? 'nav-item on' : 'nav-item';
              return item.href
                ? <Link key={item.key} className={cls} href={item.href} aria-current={on ? 'page' : undefined}>{inner}</Link>
                : <div key={item.key} className={cls}>{inner}</div>;
            })}
          </div>
        ))}
      </nav>
      <div className="side-foot">
        <div className="avatar">{userInitials}</div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12.5, color: '#fff', fontWeight: 600 }}>{userName}</div>
          <div style={{ fontSize: 11, color: '#6E90AC' }}>{userTariff}</div>
        </div>
        <span style={{ marginLeft: 'auto', color: '#6E90AC' }}><Icon name="chevd" size={15} /></span>
      </div>
    </aside>
  );
}
