'use client';

import { useEffect } from 'react';
import { отправитьОтложенное } from '@/lib/offlineQueue';

/**
 * Регистрирует service worker и досылает записи, снятые без сети.
 *
 * Ничего не рисует — это поведение, а не экран. Живёт в корневой
 * раскладке, чтобы отложенная запись ушла с любой страницы, как только
 * появится сеть: педагог мог закрыть экран записи и уйти в дэшборд.
 *
 * В разработке service worker не регистрируется: закэшированный дев-бандл
 * — верный способ полдня чинить то, что уже исправлено.
 */
export function РегистрацияSW() {
  useEffect(() => {
    if (process.env.NODE_ENV === 'production' && 'serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        // Без service worker кабинет работает как обычный сайт — это не
        // повод показывать человеку ошибку.
      });
    }

    const досылать = () => { void отправитьОтложенное(); };
    досылать();
    window.addEventListener('online', досылать);
    return () => window.removeEventListener('online', досылать);
  }, []);

  return null;
}
