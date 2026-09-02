'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/Button';

/**
 * Кнопка «Добавить на экран Домой».
 *
 * Android даёт браузеру событие beforeinstallprompt — тогда установка
 * происходит в один тап. iPhone такого события не даёт вовсе, и там
 * единственный честный путь — показать, куда нажать: «Поделиться» → «На
 * экран Домой». Поэтому кнопка появляется только если она действительно
 * работает, а инструкция стоит рядом всегда.
 */
type Приглашение = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };

export function Установка() {
  const [приглашение, setПриглашение] = useState<Приглашение | null>(null);
  const [установлено, setУстановлено] = useState(false);

  useEffect(() => {
    const перехват = (событие: Event) => {
      событие.preventDefault();
      setПриглашение(событие as Приглашение);
    };
    const поставлено = () => setУстановлено(true);
    window.addEventListener('beforeinstallprompt', перехват);
    window.addEventListener('appinstalled', поставлено);
    if (window.matchMedia('(display-mode: standalone)').matches) setУстановлено(true);
    return () => {
      window.removeEventListener('beforeinstallprompt', перехват);
      window.removeEventListener('appinstalled', поставлено);
    };
  }, []);

  if (установлено) {
    return (
      <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
        Приложение уже стоит на этом устройстве — вы открыли его с домашнего экрана.
      </p>
    );
  }

  if (!приглашение) {
    return (
      <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.6 }}>
        Этот браузер не предлагает установку кнопкой. Как поставить вручную — ниже.
      </p>
    );
  }

  return (
    <Button
      size="крупная" icon="dl"
      style={{ width: '100%', justifyContent: 'center' }}
      onClick={() => { void приглашение.prompt(); }}
    >
      Добавить на экран «Домой»
    </Button>
  );
}
