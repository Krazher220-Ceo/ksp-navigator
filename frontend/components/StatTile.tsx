import type { ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * Плитка с числом. Число приходит строкой — форматирует его сервер,
 * чтобы бот и веб писали одинаково.
 */
type Props = { label: string; value: ReactNode; hint?: ReactNode; icon?: IconName; iconColor?: string };

export function StatTile({ label, value, hint, icon, iconColor }: Props) {
  return (
    <div className="stat">
      <div className="row">
        <span className="lbl">{label}</span>
        {icon ? <span style={{ marginLeft: 'auto', color: iconColor ?? 'var(--ink-3)' }}><Icon name={icon} size={16} /></span> : null}
      </div>
      <div className="stat-n mono">{value}</div>
      {hint ? <div className="muted" style={{ fontSize: 12 }}>{hint}</div> : null}
    </div>
  );
}
