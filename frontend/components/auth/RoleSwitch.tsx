'use client';

import { Icon } from '@/components/Icon';

/**
 * Переключатель «педагог / ученик» из макетов входа и регистрации.
 *
 * Роли ровно две и третьей не будет. Выбор здесь — это выбор экрана, а
 * не заявка на права: настоящую роль присваивает сервер, когда человек
 * заводит профиль или вступает в класс.
 */
export type Роль = 'teacher' | 'student';

type Props = { значение: Роль; onChange: (роль: Роль) => void; высота?: number };

export function RoleSwitch({ значение, onChange, высота = 38 }: Props) {
  const пункт = (роль: Роль, иконка: 'doc' | 'book', подпись: string) => {
    const выбран = значение === роль;
    return (
      <button
        type="button"
        role="tab"
        aria-selected={выбран}
        onClick={() => onChange(роль)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          height: высота, borderRadius: 10, border: 0,
          background: выбран ? 'var(--card)' : 'transparent',
          boxShadow: выбран ? 'var(--lift)' : 'none',
          color: выбран ? 'var(--ink)' : 'var(--ink-3)',
          fontSize: 13.5, fontWeight: выбран ? 600 : 500,
          transition: 'all .4s var(--ease)',
        }}
      >
        <Icon name={иконка} size={16} />
        {подпись}
      </button>
    );
  };

  return (
    <div
      role="tablist"
      aria-label="Кто вы"
      style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6,
        background: 'rgba(11,26,43,.05)', borderRadius: 13, padding: 4,
      }}
    >
      {пункт('teacher', 'doc', 'Я педагог')}
      {пункт('student', 'book', 'Я ученик')}
    </div>
  );
}
