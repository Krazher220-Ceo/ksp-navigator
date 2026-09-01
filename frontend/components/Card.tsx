import type { CSSProperties, ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * Карточка — белая плоскость с волосяным контуром внутри тени.
 * Рамки 1px у неё нет и быть не должно: за контур отвечает токен --lift.
 */
type CardProps = { className?: string; style?: CSSProperties; children: ReactNode };

export function Card({ className, style, children }: CardProps) {
  return <section className={['card', className].filter(Boolean).join(' ')} style={style}>{children}</section>;
}

type HeadProps = {
  icon?: IconName;
  /** Цвет иконки шапки — в макетах он смысловой: синий, зелёный, золотой. */
  iconColor?: string;
  title: string;
  /** Правый край шапки: чип со счётчиком, ссылка «весь список» и подобное. */
  aside?: ReactNode;
  children?: ReactNode;
};

export function CardHead({ icon, iconColor, title, aside, children }: HeadProps) {
  return (
    <div className="card-h">
      {icon ? <span style={{ color: iconColor }}><Icon name={icon} size={18} /></span> : null}
      <h3>{title}</h3>
      {children}
      {aside ? <span style={{ marginLeft: 'auto' }}>{aside}</span> : null}
    </div>
  );
}

export function CardBody({ className, style, children }: CardProps) {
  return <div className={['card-b', className].filter(Boolean).join(' ')} style={style}>{children}</div>;
}
