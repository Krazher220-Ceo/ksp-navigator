import type { InputHTMLAttributes } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * Поле ввода с подписью. Пароль всегда идёт type="password": показывать
 * его по умолчанию кабинет не будет (требование блока Ф4).
 *
 * Осознанно не делает: не проверяет введённое. Проверку делает FastAPI и
 * он же возвращает готовый русский текст ошибки — формул во фронтенде нет.
 */
type Props = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  icon?: IconName;
  /** Текст ошибки от сервера — показывается под полем. */
  error?: string;
};

export function Input({ label, icon, error, className, id, ...rest }: Props) {
  const field = (
    <div className="input">
      {icon ? <Icon name={icon} size={16} style={{ color: 'var(--ink-3)' }} /> : null}
      <input id={id} {...rest} />
    </div>
  );
  return (
    <div className={['field', className].filter(Boolean).join(' ')}>
      {label ? <label className="lbl" htmlFor={id}>{label}</label> : null}
      {field}
      {error ? <span style={{ color: 'var(--red)', fontSize: 12.5 }}>{error}</span> : null}
    </div>
  );
}
