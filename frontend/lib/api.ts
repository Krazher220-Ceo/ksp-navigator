'use client';

import { ТЕКСТЫ } from '@/content/texts.generated';
import { supabase, входПоПочтеНастроен } from './supabase';
import { входTelegram } from './telegramLogin';

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

  if (входПоПочтеНастроен()) {
    const { data } = await supabase().auth.getSession();
    const токен = data.session?.access_token;
    if (токен) return { ...общие, Authorization: `Bearer ${токен}` };
  }

  // Вторая дверь браузера: вход через Telegram Login Widget. Подпись
  // проверяет сервер — здесь мы её только передаём.
  const телеграм = входTelegram();
  return телеграм ? { ...общие, 'X-Telegram-Login': телеграм } : общие;
}

export async function запрос<T>(путь: string, тело?: unknown, метод?: 'GET' | 'POST' | 'DELETE'): Promise<T> {
  let ответ: Response;
  try {
    ответ = await fetch(`${БАЗА}${путь}`, {
      method: метод ?? (тело === undefined ? 'GET' : 'POST'),
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
  /** Показывать ли пункт «Аналитика»: решает сервер, не браузер. */
  analytics_available?: boolean;
};

/** Аналитика продукта: только числа. */
export type Аналитика = {
  window_days: number;
  active_teachers: number;
  documents: { konspekt: number; ksp: number };
  actions: Record<string, { done: number; failed: number; всего: number }>;
  by_weekday: number[];
  failures: Record<string, number>;
  returned_teachers: number;
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

/** Ответ /api/v1/task/{id}. */
export type Задача = {
  task_id: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  type: string;
  retries: number;
  error?: string;
  // konspekt_task_id — вторая задача ученического режима: расшифровка
  // завершается раньше конспекта и своего konspekt_id не знает.
  result?: {
    transcript_id?: string; konspekt_id?: string | null;
    konspekt_task_id?: string | null; mode?: string;
  } | null;
};

/** Ответ /api/v1/konspekt/{id}. */
/** Реплика урока: время в секундах от начала записи, измеренное xAI. */
export type Реплика = { start: number; end: number; text: string };

export type Конспект = {
  konspekt_id: string;
  tema: string | null;
  mode: string | null;
  content: {
    opornye_repliki?: string[];
    konspekt_uchenika?: {
      tema?: string;
      celi?: string[];
      glavnoe?: string[];
      formuly?: { formula: string; znachenie: string }[];
      primery?: string[];
      terminy?: { termin: string; opredelenie: string }[];
      voprosy_dlya_samoproverki?: string[];
      domashnee_zadanie?: string;
    };
    transcript_text?: string;
    tema?: string;
  };
  transcript: {
    text: string;
    duration_seconds: number | null;
    /** Реплики урока с настоящим временем от начала записи. */
    blocks?: Реплика[];
  } | null;
  has_docx: boolean;
};

/**
 * Заголовки загрузки файла: тип, имя и та дверь, которой вошёл человек.
 *
 * Один сборщик на все три загрузки (запись урока, КТП, фото тетради).
 * Раньше каждая собирала их сама, и результат разошёлся: у записи блок
 * входа через Telegram оказался скопирован дважды, а у фото тетради его
 * не было вовсе — ученик, вошедший на сайте виджетом Telegram, получал
 * 401 на главной своей функции (сверке тетради).
 */
async function заголовкиЗагрузки(
  файл: Blob, имя: string, типПоУмолчанию: string, дополнительные: Record<string, string> = {},
): Promise<Record<string, string>> {
  const шапка: Record<string, string> = {
    'Content-Type': файл.type || типПоУмолчанию,
    'X-Filename': имя,
    ...дополнительные,
  };
  if (входПоПочтеНастроен()) {
    const { data } = await supabase().auth.getSession();
    if (data.session?.access_token) шапка.Authorization = `Bearer ${data.session.access_token}`;
  }
  if (!шапка.Authorization) {
    const телеграм = входTelegram();
    if (телеграм) шапка['X-Telegram-Login'] = телеграм;
  }
  return шапка;
}

/**
 * Отправка записи урока. Идёт телом запроса, а не multipart: так не
 * понадобилась отдельная зависимость на сервере, а браузер отдаёт Blob
 * как есть.
 */
export async function отправитьЗапись(
  файл: Blob, имя: string, режим: 'student' | 'teacher',
): Promise<{ task_id: string; status: string; size_bytes: number }> {
  const шапка = await заголовкиЗагрузки(файл, имя, 'application/octet-stream', {
    'X-Konspekt-Mode': режим,
  });

  let ответ: Response;
  try {
    ответ = await fetch(`${БАЗА}/api/v1/lesson/upload`, { method: 'POST', headers: шапка, body: файл });
  } catch {
    throw new ОшибкаApi('NETWORK', ТЕКСТЫ.API_SERVER_UNAVAILABLE, 0);
  }
  const разобрано = await ответ.json().catch(() => null);
  if (!ответ.ok) {
    const ошибка = (разобрано as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ОшибкаApi(ошибка?.code ?? 'INTERNAL', ошибка?.message ?? ТЕКСТЫ.ERROR_UNEXPECTED, ответ.status);
  }
  return разобрано as { task_id: string; status: string; size_bytes: number };
}

/** Ссылка на файл документа — открывается обычным переходом. */
export function ссылкаНаФайл(вид: 'konspekt' | 'ksp', id: string, формат: 'docx' | 'pdf' = 'docx'): string {
  return `${БАЗА}/api/v1/download/${вид}/${id}?format=${формат}`;
}

/** Ответ /api/v1/ksp/options — справочники мастера сборки КСП. */
export type НастройкиКсп = {
  templates: { id: number; name: string; description: string | null; category: string; is_official: number }[];
  tip_uroka: string[];
  cennosti: { key: string; name: string; goal: string }[];
  adal_azamat_projects: { key: string; name: string; direction: string }[];
  work_forms: string[];
  functional_literacy: string[];
  max_vidy_deyatelnosti: number;
  /** Порядок колонок «Хода урока» задаёт приказ; приходит с сервера. */
  hod_uroka_columns: { key: string; label: string }[];
};

/** Строка КТП — быстрый путь мастера. */
export type ТемаКтп = {
  id: number;
  lesson_number: number | null;
  section: string | null;
  topic: string | null;
  objective_code: string | null;
  hours: number | null;
  planned_date: string | null;
  quarter: number | null;
};

export type ОпцииУрока = {
  tip_uroka?: string | null;
  cennost_key?: string | null;
  adal_azamat_project_key?: string | null;
  vidy_deyatelnosti?: string[];
  ima_oop?: boolean;
  sor_instead_of_reflection?: boolean;
  fizkultminutka?: boolean;
  page_orientation?: 'book' | 'album';
};

/** Класс педагога. */
export type Класс = {
  id: number;
  name: string;
  subject: string | null;
  invite_code: string;
  created_at: string | null;
  students_count: number;
};

/** Ученик класса. Больше о нём не хранится ничего. */
export type Ученик = {
  id: number;
  name: string | null;
  joined_at: string | null;
  sverki: number;
  last_activity: string | null;
  /** Можно ли отправить конспект: доставляет его бот. */
  can_receive: boolean;
};

/** Строка истории: собранный документ либо провалившаяся задача. */
export type ЗаписьИстории = {
  kind: 'konspekt' | 'ksp' | 'failed';
  id: string;
  title: string;
  objective_code: string | null;
  created_at: string | null;
  status: 'ready' | 'draft' | 'failed';
  duration_seconds?: number | null;
  task_type?: string;
  error?: string | null;
  can_retry?: boolean;
  has_docx: boolean;
  has_pdf: boolean;
};

/** Класс, в котором состоит ученик. */
export type КлассУченика = { id: number; name: string; subject: string | null; teacher_name: string };

/** Урок для сверки. Расшифровки здесь нет и быть не может. */
export type УрокУченика = {
  transcript_id: string;
  created_at: string | null;
  topic: string;
  homework: string | null;
};

/** Результат сверки: только разница. Оценки нет. */
export type РезультатСверки = {
  missing_items: string[];
  too_much_missing?: boolean;
  notebook_unreadable?: boolean;
};

/** Загрузка готового КТП файлом — тем же способом, что запись урока. */
export async function отправитьКтп(
  файл: Blob, имя: string,
): Promise<{ inserted: number; replaced: number }> {
  const шапка = await заголовкиЗагрузки(файл, имя, 'application/octet-stream');
  let ответ: Response;
  try {
    ответ = await fetch(`${БАЗА}/api/v1/ktp/upload`, { method: 'POST', headers: шапка, body: файл });
  } catch {
    throw new ОшибкаApi('NETWORK', ТЕКСТЫ.API_SERVER_UNAVAILABLE, 0);
  }
  const разобрано = await ответ.json().catch(() => null);
  if (!ответ.ok) {
    const ошибка = (разобрано as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ОшибкаApi(ошибка?.code ?? 'INTERNAL', ошибка?.message ?? ТЕКСТЫ.ERROR_UNEXPECTED, ответ.status);
  }
  return разобрано as { inserted: number; replaced: number };
}

/** Отправка фото тетради — тем же способом, что запись урока. */
export async function отправитьФотоТетради(
  фото: Blob, имя: string, transcript_id: string,
): Promise<{ task_id: string; status: string }> {
  const шапка = await заголовкиЗагрузки(фото, имя, 'image/jpeg', {
    'X-Transcript-Id': transcript_id,
  });
  let ответ: Response;
  try {
    ответ = await fetch(`${БАЗА}/api/v1/student/sverka`, { method: 'POST', headers: шапка, body: фото });
  } catch {
    throw new ОшибкаApi('NETWORK', ТЕКСТЫ.API_SERVER_UNAVAILABLE, 0);
  }
  const разобрано = await ответ.json().catch(() => null);
  if (!ответ.ok) {
    const ошибка = (разобрано as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ОшибкаApi(ошибка?.code ?? 'INTERNAL', ошибка?.message ?? ТЕКСТЫ.ERROR_UNEXPECTED, ответ.status);
  }
  return разобрано as { task_id: string; status: string };
}

export const апи = {
  дэшборд: () => запрос<Дэшборд>('/api/v1/dashboard'),
  аналитика: () => запрос<Аналитика>('/api/v1/analytics'),
  классыУченика: () => запрос<{ classes: КлассУченика[] }>('/api/v1/student/classes'),
  урокиУченика: (class_id: number) =>
    запрос<{ lessons: УрокУченика[] }>(`/api/v1/student/lessons?class_id=${class_id}`),
  история: (фильтр?: { kind?: string; since?: string; until?: string }) => {
    const параметры = new URLSearchParams();
    if (фильтр?.kind) параметры.set('kind', фильтр.kind);
    if (фильтр?.since) параметры.set('since', фильтр.since);
    if (фильтр?.until) параметры.set('until', фильтр.until);
    const хвост = параметры.toString();
    return запрос<{ items: ЗаписьИстории[]; counts: { konspekt: number; ksp: number; failed: number } }>(
      `/api/v1/history${хвост ? `?${хвост}` : ''}`,
    );
  },
  повторить: (task_id: string) => запрос<{ task_id: string; status: string }>(`/api/v1/task/${task_id}/retry`, {}),
  классы: () => запрос<{ classes: Класс[] }>('/api/v1/classes'),
  создатьКласс: (name: string, subject?: string) =>
    запрос<{ class: Класс }>('/api/v1/classes', { name, subject }),
  перевыпуститьКод: (class_id: number) =>
    запрос<{ invite_code: string }>(`/api/v1/classes/${class_id}/code`, {}),
  ученики: (class_id: number) =>
    запрос<{ students: Ученик[] }>(`/api/v1/classes/${class_id}/students`),
  удалитьКласс: (class_id: number) =>
    запрос<{ deleted: boolean; name: string }>(`/api/v1/classes/${class_id}`, undefined, 'DELETE'),
  отправитьКонспект: (class_id: number, student_id: number, konspekt_id: string) =>
    запрос<{ sent: boolean; message: string }>(
      `/api/v1/classes/${class_id}/send-konspekt`, { student_id, konspekt_id },
    ),
  настройкиКсп: () => запрос<НастройкиКсп>('/api/v1/ksp/options'),
  темыКтп: () => запрос<{ entries: ТемаКтп[] }>('/api/v1/ktp/entries'),
  собратьКтп: (данные: {
    predmet: string; klass: string; chasov_v_nedelu: number; chasov_v_god: number; topics?: string | null;
  }) => запрос<{ task_id: string; status: string }>('/api/v1/ktp/generate', данные),
  кодЦели: (topic: string) =>
    запрос<{ objective_code: string | null }>(`/api/v1/ktp/objective?topic=${encodeURIComponent(topic)}`),
  собратьКсп: (данные: {
    topic: string; razdel: string; subject: string; klass: string; duration_minutes: number;
    template_id: number; objective_code?: string | null; ktp_entry_id?: number | null;
    options?: ОпцииУрока; konspekt_id?: string | null;
  }) => запрос<{ task_id: string; status: string }>('/api/v1/ksp/generate', данные),
  задача: (task_id: string) => запрос<Задача>(`/api/v1/task/${task_id}`),
  конспект: (konspekt_id: string) => запрос<Конспект>(`/api/v1/konspekt/${konspekt_id}`),
  /** Заводит аккаунт с уже подтверждённой почтой — без письма. */
  зарегистрировать: (email: string, password: string) =>
    запрос<{ created: boolean }>('/api/v1/auth/register', { email, password }),
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
