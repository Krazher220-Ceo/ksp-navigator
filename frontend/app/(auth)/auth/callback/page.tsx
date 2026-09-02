'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { Button } from '@/components/Button';
import { ОшибкаApi, апи } from '@/lib/api';
import { supabase, входПоПочтеНастроен } from '@/lib/supabase';

/**
 * Возврат из письма Supabase: подтверждение почты и смена пароля.
 *
 * Зачем модуль: этой страницы в проекте не было вовсе, и ссылка из
 * письма приводила человека на лендинг. Токены приходят в «хвосте»
 * адреса (после #), разобрать их может только страница, на которой
 * создан клиент Supabase, — на лендинге его нет. Снаружи это выглядело
 * так, будто регистрации просто не существует.
 *
 * Что делает: превращает то, что пришло в адресе, в сессию, а потом
 * решает по ответу сервера, куда человека вести — дозаполнять профиль
 * или сразу в кабинет. Сам не решает ничего: роль спрашивается у
 * /api/v1/me.
 *
 * Чего осознанно не делает: не показывает «успешно» и не оставляет
 * человека на этой странице. Она служебная, задерживаться на ней не за
 * чем — только сообщить, если ссылка не сработала.
 */
export default function AuthCallbackPage() {
  const router = useRouter();
  const [ошибка, setОшибка] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      if (!входПоПочтеНастроен()) {
        setОшибка('Вход по почте ещё не настроен на этом сервере.');
        return;
      }

      const адрес = new URL(window.location.href);
      const хвост = new URLSearchParams(адрес.hash.replace(/^#/, ''));

      // Supabase объясняет отказ по-английски и прямо в адресе:
      // просроченная ссылка, уже использованная, чужая. Показать это
      // человеку понятнее, чем молча вернуть его на форму.
      const отказ = адрес.searchParams.get('error_description') ?? хвост.get('error_description');
      if (отказ) {
        setОшибка(
          'Ссылка из письма не сработала. Обычно это значит, что она устарела '
          + 'или ей уже воспользовались — запросите письмо заново.',
        );
        return;
      }

      const клиент = supabase();
      try {
        // Две схемы возврата, и обе живые. PKCE присылает код в
        // обычных параметрах, implicit — готовые токены в хвосте.
        // Проект на implicit (это умолчание supabase-js), но письмо,
        // открытое на другом устройстве, может прийти и кодом.
        const код = адрес.searchParams.get('code');
        const токен = хвост.get('access_token');
        const обновление = хвост.get('refresh_token');

        if (код) {
          const { error } = await клиент.auth.exchangeCodeForSession(код);
          if (error) throw error;
        } else if (токен && обновление) {
          const { error } = await клиент.auth.setSession({
            access_token: токен, refresh_token: обновление,
          });
          if (error) throw error;
        }

        const { data } = await клиент.auth.getSession();
        if (!data.session) {
          setОшибка(
            'Ссылка из письма не сработала. Обычно это значит, что она устарела '
            + 'или ей уже воспользовались — запросите письмо заново.',
          );
          return;
        }

        // Смена пароля — отдельный разговор: человек пришёл вписать
        // новый, а не в кабинет.
        if ((хвост.get('type') ?? адрес.searchParams.get('type')) === 'recovery') {
          router.replace('/auth/novyy-parol');
          return;
        }
      } catch {
        setОшибка(
          'Ссылка из письма не сработала. Обычно это значит, что она устарела '
          + 'или ей уже воспользовались — запросите письмо заново.',
        );
        return;
      }

      // Куда вести — знает сервер. Профиля ещё нет (обычный случай для
      // подтверждённой почты) — на дозаполнение, иначе в кабинет.
      try {
        const я = await апи.я();
        router.replace(я.role === null ? '/registraciya' : '/app');
      } catch (сбой) {
        if (сбой instanceof ОшибкаApi && сбой.статус === 401) {
          router.replace('/vhod');
          return;
        }
        // Сервер молчит, но вход состоялся: пусть человек идёт в
        // кабинет, там ошибка объяснится на месте.
        router.replace('/app');
      }
    })();
  }, [router]);

  return (
    <AuthShell
      заголовок={<>Ещё секунду —<br />заканчиваем вход.</>}
      подзаголовок="Проверяем ссылку из письма и открываем кабинет."
      ширинаФормы={520}
    >
      {ошибка ? (
        <>
          <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Ссылка не сработала</h1>
          <p className="muted" style={{ fontSize: 14, marginTop: 10, lineHeight: 1.6 }}>{ошибка}</p>
          <Button
            size="крупная" arrow onClick={() => router.replace('/vhod')}
            style={{ width: '100%', justifyContent: 'center', marginTop: 20 }}
          >
            Вернуться ко входу
          </Button>
        </>
      ) : (
        <>
          <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Заканчиваем вход</h1>
          <p className="muted" style={{ fontSize: 14, marginTop: 10, lineHeight: 1.6 }}>
            Это занимает пару секунд. Страница закроется сама.
          </p>
        </>
      )}
    </AuthShell>
  );
}
