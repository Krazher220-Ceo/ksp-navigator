import type { CSSProperties, ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * Чип — короткая пометка статуса. Золотой закреплён за словом «черновик»
 * и другим смыслам не отдаётся: по нему в кабинете узнаётся документ,
 * который ещё требует проверки человеком.
 */
type Props = {
  tone?: 'синий' | 'золотой' | 'зелёный' | 'красный' | 'серый';
  icon?: IconName;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
};

const TONE = {
  синий: '', золотой: 'chip-gold', зелёный: 'chip-green', красный: 'chip-red', серый: 'chip-grey',
} as const;

export function Chip({ tone = 'синий', icon, className, style, children }: Props) {
  return (
    <span className={['chip', TONE[tone], className].filter(Boolean).join(' ')} style={style}>
      {icon ? <Icon name={icon} size={12} /> : null}
      {children}
    </span>
  );
}
