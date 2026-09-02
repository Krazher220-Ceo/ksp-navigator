'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { MobileTabs } from '@/components/MobileTabs';
import { Sidebar, type Роль } from '@/components/Sidebar';
import { ОшибкаApi, апи, type Я } from '@/lib/api';

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
 *
 * Здесь же стоит дверь: не вошёл — отправляем на вход, вошёл без
 * профиля — на его дозаполнение. Пустой кабинет без объяснения, почему
 * он пустой, — худшее из возможных состояний.
 */
function инициалы(имя: string | null | undefined): string {
  if (!имя) return '—';
  return имя.trim().split(/\s+/).slice(0, 2).map((с) => с[0]?.toUpperCase() ?? '').join('');
}

export default function CabinetLayout({ children }: { children: React.ReactNode }) {
  const [я, setЯ] = useState<Я | null>(null);
  const [загружено, setЗагружено] = useState(false);
  const router = useRouter();
  const путь = usePathname();

  useEffect(() => {
    (async () => {
      try {
        const профиль = await апи.я();
        setЯ(профиль);
        // Вошёл, но профиля ещё нет: так выглядит человек, подтвердивший
        // почту по ссылке из письма. Ему нужен не пустой кабинет, а
        // форма профиля.
        if (профиль.role === null && путь !== '/app/ustanovka') {
          router.replace('/registraciya');
        }
      } catch (ошибка) {
        // 401 — не вошёл вовсе. Отправляем на вход, а не показываем
        // кабинет, в котором ничего не откроется.
        if (ошибка instanceof ОшибкаApi && ошибка.статус === 401) {
          router.replace('/vhod');
          return;
        }
        // Сервер недоступен — оставляем самое узкое меню и даём
        // страницам показать свою ошибку.
      } finally {
        setЗагружено(true);
      }
    })();
  }, [router, путь]);

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
      {/* На телефоне бокового меню нет — вкладки снизу его заменяют. */}
      <MobileTabs роль={роль} />
    </div>
  );
}
