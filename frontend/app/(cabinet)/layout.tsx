'use client';

import { useEffect, useState } from 'react';
import { Sidebar, type Роль } from '@/components/Sidebar';
import { апи, type Я } from '@/lib/api';

/**
 * Оболочка кабинета: тёмная рама, боковое меню и вложенное светлое ядро.
 *
 * Имя и роль приходят из /api/v1/me — решает сервер, а не браузер.
 * Ученику пункты педагога не показываются вовсе: не отключённый пункт, а
 * отсутствующий (требование блока У3 дословно).
 *
 * Пока ответ не пришёл, меню рисуется в ученическом виде — то есть в
 * самом узком. Мигнуть учительским меню перед ребёнком хуже, чем
 * показать на долю секунды меньше пунктов, чем нужно педагогу.
 */
function инициалы(имя: string | null | undefined): string {
  if (!имя) return '—';
  return имя.trim().split(/\s+/).slice(0, 2).map((с) => с[0]?.toUpperCase() ?? '').join('');
}

export default function CabinetLayout({ children }: { children: React.ReactNode }) {
  const [я, setЯ] = useState<Я | null>(null);
  const [загружено, setЗагружено] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setЯ(await апи.я());
      } catch {
        // Не узнали, кто перед нами, — показываем самое узкое меню.
      } finally {
        setЗагружено(true);
      }
    })();
  }, []);

  const роль: Роль = загружено ? (я?.role ?? 'teacher') : 'student';

  return (
    <div className="app">
      <Sidebar
        роль={роль}
        аналитика={Boolean(я?.analytics_available)}
        userName={я?.profile?.name ?? 'Ваш профиль'}
        userInitials={инициалы(я?.profile?.name)}
        userTariff={я?.role === 'student' ? 'Ученик' : 'Тариф «Учитель»'}
      />
      <div className="main">{children}</div>
    </div>
  );
}
