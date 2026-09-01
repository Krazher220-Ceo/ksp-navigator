'use client';

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

/**
 * Клиент Supabase Auth — вход по почте и паролю.
 *
 * Здесь только вход: данными продукта Supabase из браузера не отдаёт,
 * их отдаёт FastAPI. Поэтому ключ нужен ровно один, публичный anon.
 * SUPABASE_SERVICE_ROLE_KEY во фронтенд не попадает никогда — он даёт
 * полный доступ к базе мимо всех проверок и живёт в .env сервера.
 *
 * Чего модуль осознанно не делает: не хранит роль и не решает, что
 * человеку показывать. Роль приходит от FastAPI из /api/v1/me — иначе
 * браузер и сервер разойдутся в том, кто перед ними.
 */
let клиент: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (клиент) return клиент;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anon) {
    // Не настроено — говорим прямо. Молчаливая заглушка здесь означала
    // бы форму входа, которая нажимается и ничего не делает.
    throw new Error('Вход по почте не настроен: не заданы NEXT_PUBLIC_SUPABASE_URL и NEXT_PUBLIC_SUPABASE_ANON_KEY.');
  }
  клиент = createClient(url, anon, { auth: { persistSession: true, autoRefreshToken: true } });
  return клиент;
}

/** Настроен ли вход по почте — чтобы форма не притворялась рабочей. */
export function входПоПочтеНастроен(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);
}

/**
 * Возвращает текущую сессию, а если её нет — заводит анонимную.
 *
 * Зачем: ученик приходит по коду, продиктованному на уроке, и по макету
 * «отдельная регистрация не нужна». В Telegram личность приходит от
 * самого Telegram, в браузере её взять неоткуда — а без неё сервер не
 * знает, кого записывать в класс, и не должен знать.
 *
 * Анонимный вход Supabase решает это, не показывая ребёнку ни одной
 * формы: получается настоящий аккаунт с обычным токеном, только без
 * почты и пароля. Персональных данных в нём нет — имя ученик вписывает
 * сам на шаге подтверждения, и кроме имени о нём ничего не хранится.
 *
 * Если анонимный вход в проекте выключен, функция честно скажет об
 * этом, а не оставит экран молча неработающим.
 */
export async function убедитьсяЧтоЕстьСессия(): Promise<void> {
  const клиент = supabase();
  const { data } = await клиент.auth.getSession();
  if (data.session) return;
  const { error } = await клиент.auth.signInAnonymously();
  if (error) {
    throw new Error(
      'Вход по коду класса недоступен: в проекте Supabase не включён анонимный вход. '
      + 'Попросите учителя сообщить об этом — или войдите по почте.',
    );
  }
}
