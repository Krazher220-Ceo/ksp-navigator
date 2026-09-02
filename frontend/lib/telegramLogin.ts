'use client';

/**
 * Хранение входа через Telegram Login Widget.
 *
 * Виджет отдаёт набор полей с подписью. Сервер проверяет её сам
 * (web/auth.py), поэтому браузеру достаточно сохранить эти поля и
 * присылать их заголовком — ровно так же, как Mini App присылает
 * initData.
 *
 * Что осознанно не делается: подпись здесь не проверяется и не
 * пересобирается. Проверка в браузере не защищает ни от чего — её
 * делает тот, кто знает токен бота, то есть сервер.
 *
 * Живёт в localStorage: это не секрет длительного действия, подпись
 * действительна сутки, и сервер сам откажет просроченной.
 */
const КЛЮЧ = 'mazmun-telegram-login';

export function сохранитьВходTelegram(данные: Record<string, unknown>): void {
  const поля = new URLSearchParams();
  for (const [имя, значение] of Object.entries(данные)) {
    if (значение !== undefined && значение !== null) поля.set(имя, String(значение));
  }
  try {
    localStorage.setItem(КЛЮЧ, поля.toString());
  } catch {
    // Приватное окно без хранилища — вход просто не запомнится.
  }
}

export function входTelegram(): string | null {
  try {
    return localStorage.getItem(КЛЮЧ);
  } catch {
    return null;
  }
}

export function забытьВходTelegram(): void {
  try {
    localStorage.removeItem(КЛЮЧ);
  } catch {
    // нечего забывать
  }
}
