import type { ReactNode } from 'react';
import { Icon } from './Icon';

/**
 * Шапка кабинета: дата и обращение слева, поиск, колокольчик и главное
 * действие справа.
 *
 * Осознанно не делает: поиск здесь только выглядит полем — искать будет
 * сервер, и придёт это отдельным блоком. Точка на колокольчике зажигается
 * по флагу с сервера, а не по таймеру в браузере.
 */
type Props = {
  /** Дата строкой — форматирует её сервер, чтобы бот и веб писали одинаково. */
  date: string;
  title: string;
  searchPlaceholder: string;
  hasAlerts?: boolean;
  action?: ReactNode;
};

export function TopBar({ date, title, searchPlaceholder, hasAlerts, action }: Props) {
  return (
    <header className="top">
      <div>
        <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.2 }}>{date}</div>
        <h1 style={{ fontSize: 16, lineHeight: 1.25 }}>{title}</h1>
      </div>
      <div className="search top-search">
        <Icon name="search" size={16} />
        <span>{searchPlaceholder}</span>
      </div>
      <div className="top-bell" style={{ color: 'var(--ink-3)', position: 'relative' }}>
        <Icon name="bell" size={20} />
        {hasAlerts ? (
          <i style={{ position: 'absolute', top: 1, right: 1, width: 7, height: 7, borderRadius: '50%', background: 'var(--red)', boxShadow: '0 0 0 2px #fff' }} />
        ) : null}
      </div>
      <span className="top-action">{action}</span>
    </header>
  );
}
