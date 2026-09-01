import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * Кнопка кабинета. Три вида и три размера — те же, что в макетах.
 *
 * Осознанно не делает: не показывает состояние загрузки (у кабинета для
 * долгих операций своя очередь, блок Ф6) и не рисует рамку — контур
 * кнопки живёт внутри тени.
 */
type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  /** «Основная» — залитая, «тихая» — белая, «строкой» — только текст. */
  variant?: 'основная' | 'тихая' | 'строкой';
  size?: 'обычная' | 'малая' | 'крупная';
  icon?: IconName;
  /** Стрелка в своём круге у правого края — приём макета для главного действия. */
  arrow?: boolean;
  children?: ReactNode;
};

const VARIANT = { основная: '', тихая: 'btn-2', строкой: 'btn-3' } as const;
const SIZE = { обычная: '', малая: 'btn-sm', крупная: 'btn-lg' } as const;
const ICON_SIZE = { обычная: 16, малая: 14, крупная: 18 } as const;

export function Button({
  variant = 'основная', size = 'обычная', icon, arrow, children, className, type = 'button', ...rest
}: Props) {
  const classes = ['btn', VARIANT[variant], SIZE[size], className].filter(Boolean).join(' ');
  return (
    <button type={type} className={classes} {...rest}>
      {icon ? <Icon name={icon} size={ICON_SIZE[size]} /> : null}
      {children}
      {arrow ? <span className="btn-ico"><Icon name="arrow" size={14} /></span> : null}
    </button>
  );
}
