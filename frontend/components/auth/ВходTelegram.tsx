'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { запрос } from '@/lib/api';
import { сохранитьВходTelegram } from '@/lib/telegramLogin';

/**
 * Вход в кабинет подтверждением в нашем боте — без номера телефона.
 *
 * Что здесь было раньше и почему заменено. Стоял официальный Telegram
 * Login Widget. Он открывает oauth.telegram.org, и та страница просит
 * НОМЕР ТЕЛЕФОНА, если человек не залогинен в Telegram Web в этом самом
 * браузере — на телефоне это почти всегда. То есть кнопка «Войти через
 * Telegram» на практике означала «введите номер телефона и ждите код»,
 * причём код приходил в Telegram, куда человек и так не мог зайти. Вход
 * не доходил до конца.
 *
 * Как сейчас: сайт берёт одноразовый талон, человек открывает бота по
 * ссылке, бот показывает контрольные знаки и кнопку «Это я». Номер не
 * спрашивается ни разу — Telegram уже знает, кто пишет боту.
 *
 * Про контрольные знаки. У входа по ссылке есть известная слабость:
 * можно создать талон у себя и прислать свою ссылку другому человеку,
 * чтобы он подтвердил чужой вход. Технически это не ловится — ловится
 * глазами: те же четыре знака показаны и здесь, и в боте, и бот прямо
 * просит их сверить.
 *
 * Чего этот компонент осознанно не делает: не проверяет подпись. Данные
 * входа подписывает и проверяет сервер (web/auth.py) — тем же кодом, что
 * и раньше проверял виджет.
 */
type Props = { onВошёл: () => void };

type Талон = { code: string; deep_link: string; check_digits: string; expires_in: number };

const ИНТЕРВАЛ_ОПРОСА_МС = 2000;

export function ВходTelegram({ onВошёл }: Props) {
  const [талон, setТалон] = useState<Талон | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const таймер = useRef<ReturnType<typeof setInterval> | null>(null);

  const остановить = useCallback(() => {
    if (таймер.current) {
      clearInterval(таймер.current);
      таймер.current = null;
    }
  }, []);

  const начать = useCallback(async () => {
    setОшибка(null);
    setЗанято(true);
    try {
      const новый = await запрос<Талон>('/api/v1/auth/telegram/start', {}, 'POST');
      setТалон(новый);
    } catch (сбой) {
      setОшибка(сбой instanceof Error ? сбой.message : 'Не получилось начать вход.');
    } finally {
      setЗанято(false);
    }
  }, []);

  useEffect(() => {
    if (!талон) return;

    таймер.current = setInterval(async () => {
      try {
        const ответ = await запрос<{ status: string; login?: Record<string, string> }>(
          `/api/v1/auth/telegram/poll?code=${encodeURIComponent(талон.code)}`,
        );
        if (ответ.status === 'confirmed' && ответ.login) {
          остановить();
          сохранитьВходTelegram(ответ.login);
          onВошёл();
        }
      } catch (сбой) {
        // Талон умер (пять минут или уже использован) — опрашивать
        // дальше нечего, показываем причину и предлагаем начать заново.
        остановить();
        setТалон(null);
        setОшибка(сбой instanceof Error ? сбой.message : 'Ссылка входа больше не действует.');
      }
    }, ИНТЕРВАЛ_ОПРОСА_МС);

    return остановить;
  }, [талон, onВошёл, остановить]);

  if (!талон) {
    return (
      <div style={{ marginTop: 20 }}>
        <button type="button" className="btn" onClick={начать} disabled={занято} style={{ width: '100%' }}>
          {занято ? 'Готовлю вход…' : 'Войти через Telegram'}
        </button>
        {ошибка ? (
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8, textAlign: 'center' }}>
            {ошибка}
          </p>
        ) : (
          <p className="muted" style={{ fontSize: 12, marginTop: 8, textAlign: 'center' }}>
            Подтверждение придёт в нашего бота. Номер телефона вводить не нужно.
          </p>
        )}
      </div>
    );
  }

  return (
    <div style={{ marginTop: 20 }}>
      <a
        className="btn"
        href={талон.deep_link}
        target="_blank"
        rel="noopener noreferrer"
        style={{ display: 'block', textAlign: 'center', width: '100%' }}
      >
        Открыть бота и подтвердить
      </a>

      <p style={{ fontSize: 13, lineHeight: 1.6, marginTop: 14, textAlign: 'center' }}>
        Контрольные знаки: <strong style={{ letterSpacing: '0.12em' }}>{талон.check_digits}</strong>
      </p>
      <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.55, marginTop: 6, textAlign: 'center' }}>
        Бот покажет такие же. Совпали — нажмите в нём «Это я», и эта страница
        сама пустит вас в кабинет. Не совпали — не подтверждайте: значит
        ссылку открыл не тот, кто начал вход.
      </p>

      <p className="muted" style={{ fontSize: 12, marginTop: 12, textAlign: 'center' }}>
        Жду подтверждения… Ссылка живёт 5 минут.{' '}
        <button
          type="button"
          onClick={() => {
            остановить();
            setТалон(null);
          }}
          style={{ background: 'none', border: 0, padding: 0, font: 'inherit', textDecoration: 'underline', cursor: 'pointer' }}
        >
          Начать заново
        </button>
      </p>
    </div>
  );
}
