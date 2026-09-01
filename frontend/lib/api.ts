'use client';

import { ТЕКСТЫ } from '@/content/texts.generated';
import { supabase, входПоПочтеНастроен } from './supabase';

/**
 * Обращения к FastAPI из кабинета.
 *
 * Что делает: подставляет JWT в заголовок Authorization и разбирает
 * единый формат ошибки — {"error": {"code", "message"}}, где message уже
 * готовый русский текст для показа человеку.
 *
 * Чего осознанно не делает: ничего не считает и не решает. Роль, лимиты,
 * числа дэшборда приходят готовыми — любая формула, появившаяся здесь,
 * разведёт кабинет с ботом при первой же правке.
 */
const БАЗА = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export class ОшибкаApi extends Error {
  readonly код: string;
  readonly статус: number;

  constructor(код: string, сообщение: string, статус: number) {
    super(сообщение);
    this.код = код;
    this.статус = статус;
  }
}

async function заголовки(): Promise<Record<string, string>> {
  const общие: Record<string, string> = { 'Content-Type': 'application/json' };
  if (!входПоПочтеНастроен()) return общие;
  const { data } = await supabase().auth.getSession();
  const токен = data.session?.access_token;
  return токен ? { ...общие, Authorization: `Bearer ${токен}` } : общие;
}

export async function запрос<T>(путь: string, тело?: unknown): Promise<T> {
  let ответ: Response;
  try {
    ответ = await fetch(`${БАЗА}${путь}`, {
      method: тело === undefined ? 'GET' : 'POST',
      headers: await заголовки(),
      body: тело === undefined ? undefined : JSON.stringify(тело),
    });
  } catch {
    // До сервера не достучались: сети нет или он не поднят. Текст тот
    // же, что у бота на недоступный сервер.
    throw new ОшибкаApi('NETWORK', ТЕКСТЫ.API_SERVER_UNAVAILABLE, 0);
  }

  const разобрано = await ответ.json().catch(() => null);
  if (!ответ.ok) {
    const ошибка = (разобрано as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ОшибкаApi(
      ошибка?.code ?? 'INTERNAL',
      ошибка?.message ?? ТЕКСТЫ.ERROR_UNEXPECTED,
      ответ.status,
    );
  }
  return разобрано as T;
}

export type Я = {
  user_id: string;
  telegram_user_id: number | null;
  role: 'teacher' | 'student' | null;
  consent_given: boolean;
  profile: { name: string | null; subject: string | null; school: string | null; city: string | null } | null;
};

/** Ответ /api/v1/dashboard — ровно то, что посчитал core.dashboard. */
export type Дэшборд = {
  has_profile: boolean;
  queue: { pending: number; processing: number; failed_7d: number };
  generated_ksp: { total: number; last_7d: number; last_30d: number };
  ktp_coverage: { covered: number; not_covered: number };
  upcoming_lessons_without_ksp: { topic: string; planned_date: string }[];
  unparsed_planned_dates: number;
  style_profile: { exists: boolean; samples_count: number | null };
  usage_today: {
    generate_ksp: number; generate_ktp: number;
    generate_ksp_limit: number; generate_ktp_limit: number;
  };
  uptime: {
    days_tracked?: number;
    incidents_7d?: number;
    downtime_7d_minutes?: number;
    last_incident?: { started_at: string; ended_at: string | null; reason: string } | null;
    [ключ: string]: unknown;
  };
  generated_at: string;
  generated_at_label: string;
};

export const апи = {
  дэшборд: () => запрос<Дэшборд>('/api/v1/dashboard'),
  я: () => запрос<Я>('/api/v1/me'),
  согласие: () => запрос<{ consent_given: boolean }>('/api/v1/consent', {}),
  регистрацияПедагога: (данные: { name: string; subject: string; school?: string; city?: string }) =>
    запрос<{ role: string; profile: Я['profile'] }>('/api/v1/teacher', данные),
  предпросмотрКласса: (code: string) =>
    запрос<{ class_name: string; teacher_name: string }>('/api/v1/class/preview', { code }),
  вступитьВКласс: (code: string, name?: string) =>
    запрос<{ joined: boolean; class_name: string; teacher_name: string; message: string }>(
      '/api/v1/class/join', { code, name },
    ),
};
