'use client';

import { useEffect, useRef, useState } from 'react';
import { сохранитьВходTelegram } from '@/lib/telegramLogin';

/**
 * Вход в кабинет через Telegram Login Widget.
 *
 * Раньше кнопка «Войти через Telegram» вела в бота и на этом
 * заканчивалась: в кабинет она не пускала вовсе. Теперь это настоящая
 * третья дверь — виджет Telegram отдаёт подписанные данные, сервер
 * проверяет подпись (web/auth.py, verify_login_widget_string), и человек
 * оказывается в кабинете с той же ролью, что в боте.
 *
 * Виджет работает только если у бота задан домен через @BotFather
 * (`/setdomain`). Домен не задан — виджет молча не отрисуется, поэтому
 * рядом стоит честная подсказка, а не пустое место.
 */
type Props = { onВошёл: () => void };

export function ВходTelegram({ onВошёл }: Props) {
  const место = useRef<HTMLDivElement>(null);
  const [отрисован, setОтрисован] = useState(false);
  const имяБота = process.env.NEXT_PUBLIC_TELEGRAM_BOT_NAME;

  useEffect(() => {
    if (!имяБота || !место.current) return;

    // Виджет зовёт функцию по имени из window — своего API у него нет.
    (window as unknown as Record<string, unknown>).mazmunTelegramLogin = (данные: Record<string, string>) => {
      сохранитьВходTelegram(данные);
      onВошёл();
    };

    const скрипт = document.createElement('script');
    скрипт.src = 'https://telegram.org/js/telegram-widget.js?22';
    скрипт.async = true;
    скрипт.setAttribute('data-telegram-login', имяБота);
    скрипт.setAttribute('data-size', 'large');
    скрипт.setAttribute('data-radius', '11');
    скрипт.setAttribute('data-onauth', 'mazmunTelegramLogin(user)');
    скрипт.setAttribute('data-request-access', 'write');
    скрипт.onload = () => setОтрисован(true);
    место.current.appendChild(скрипт);

    return () => {
      delete (window as unknown as Record<string, unknown>).mazmunTelegramLogin;
    };
  }, [имяБота, onВошёл]);

  if (!имяБота) {
    return (
      <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.55, marginTop: 20 }}>
        Вход через Telegram ещё не настроен на этом сервере: не задан
        NEXT_PUBLIC_TELEGRAM_BOT_NAME.
      </p>
    );
  }

  return (
    <div style={{ marginTop: 20 }}>
      <div ref={место} style={{ display: 'flex', justifyContent: 'center', minHeight: 48 }} />
      {!отрисован ? (
        <p className="muted" style={{ fontSize: 12, marginTop: 8, textAlign: 'center' }}>
          Загружаю кнопку Telegram…
        </p>
      ) : (
        <p className="muted" style={{ fontSize: 12, marginTop: 8, textAlign: 'center' }}>
          Откроется подтверждение в Telegram — пароль вводить не нужно.
        </p>
      )}
    </div>
  );
}
