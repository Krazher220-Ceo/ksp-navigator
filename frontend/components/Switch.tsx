'use client';

/**
 * Переключатель. Внешне — «таблетка» из макета, внутри — настоящий
 * checkbox, чтобы работали клавиатура и скринридер.
 */
type Props = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
};

export function Switch({ checked, onChange, label, disabled }: Props) {
  return (
    <label className="row" style={{ gap: 10, cursor: disabled ? 'default' : 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        // Настоящий checkbox остаётся в потоке для клавиатуры и скринридера,
        // но не виден: его роль играет «таблетка» ниже.
        style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
      />
      <span className={checked ? 'sw on' : 'sw'} style={{ opacity: disabled ? 0.55 : 1 }}><i /></span>
      <span style={{ fontSize: 13.5 }}>{label}</span>
    </label>
  );
}
