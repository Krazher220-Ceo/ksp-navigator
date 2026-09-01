'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { Соглашение } from '@/components/auth/Соглашение';
import { Button } from '@/components/Button';
import { Card } from '@/components/Card';
import { Icon } from '@/components/Icon';
import { Input } from '@/components/Input';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи } from '@/lib/api';
import { входПоПочтеНастроен, убедитьсяЧтоЕстьСессия } from '@/lib/supabase';

/**
 * Вступление ученика в класс по коду приглашения.
 *
 * Три шага: условия работы (своя редакция, с упоминанием законного
 * представителя), код, подтверждение класса и педагога. Ошибочный код
 * заканчивается здесь же — «код не найден» и поле для новой попытки, а
 * не выброс в меню. Требование блока У3 дословно.
 *
 * Что осознанно не делает: не подсказывает, «похож ли» код на верный, и
 * не проверяет его на стороне браузера. Ищет класс сервер: код — это
 * ключ к чужому классу, и подбирать его по подсказкам нельзя.
 *
 * Формы регистрации здесь нет и не будет: по макету ученик заходит по
 * коду, и отдельная регистрация ему не нужна. Аккаунт всё равно
 * появляется — анонимный, средствами Supabase (lib/supabase.ts), — иначе
 * сервер не знает, кого записывать в класс. Ребёнок этого не видит.
 */
type Шаг = 'условия' | 'код' | 'подтверждение' | 'готово';

export default function KlassPage() {
  const router = useRouter();
  const [шаг, setШаг] = useState<Шаг>('условия');
  const [код, setКод] = useState('');
  const [имя, setИмя] = useState('');
  const [класс, setКласс] = useState<{ class_name: string; teacher_name: string } | null>(null);
  const [итог, setИтог] = useState<string | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);

  /** Аккаунт заводится молча и только когда он действительно нужен. */
  async function сессия() {
    if (!входПоПочтеНастроен()) {
      throw new Error('Вход по коду класса ещё не настроен на этом сервере.');
    }
    await убедитьсяЧтоЕстьСессия();
  }

  async function проверитьКод(событие: React.FormEvent) {
    событие.preventDefault();
    setОшибка(null);
    setЗанято(true);
    try {
      await сессия();
      await апи.согласие();
      setКласс(await апи.предпросмотрКласса(код));
      setШаг('подтверждение');
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  async function вступить() {
    setОшибка(null);
    setЗанято(true);
    try {
      await сессия();
      const ответ = await апи.вступитьВКласс(код, имя);
      setИтог(ответ.message);
      setШаг('готово');
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  return (
    <AuthShell
      ширинаФормы={560}
      заголовок={<>Класс собирается<br />одним кодом.</>}
      подзаголовок="Учитель диктует код на уроке. Отдельной регистрации не нужно: вступили в класс — и можно присылать фото тетради, чтобы увидеть, чего в ней не хватает по сравнению с уроком."
      сноска={(
        <span className="auth-promo-note row" style={{ gap: 10 }}>
          <Icon name="shield" size={16} />
          <span>Об ученике храним только имя. Ни ИИН, ни даты рождения, ни оценок.</span>
        </span>
      )}
    >
      {шаг === 'условия' ? (
        // Согласие сохраняется не здесь: аккаунта ещё нет. Оно уходит на
        // сервер вместе с первым же действием — а сервер сторожит, чтобы
        // до согласия ничего и не произошло.
        <Соглашение роль="student" сохранять={false} onПринято={() => setШаг('код')} />
      ) : null}

      {шаг === 'код' ? (
        <form onSubmit={проверитьКод}>
          <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Код приглашения</h1>
          <p className="muted" style={{ fontSize: 14, marginTop: 6 }}>{ТЕКСТЫ.STUDENT_JOIN_ASK_CODE}</p>
          <div style={{ marginTop: 20 }}>
            <Input
              id="klass-code" label="Код от учителя" icon="key" required
              placeholder="KZ4H7M" value={код} onChange={(e) => setКод(e.target.value)}
              autoComplete="off" autoCapitalize="characters" spellCheck={false}
              error={ошибка ?? undefined}
            />
          </div>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            Регистр не важен, пробелы и дефисы можно не убирать.
          </p>
          <Button type="submit" size="крупная" arrow disabled={занято} style={{ width: '100%', justifyContent: 'center', marginTop: 18 }}>
            Найти класс
          </Button>
        </form>
      ) : null}

      {шаг === 'подтверждение' && класс ? (
        <div>
          <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Это ваш класс?</h1>
          <Card style={{ marginTop: 18, padding: '20px 22px' }}>
            <div className="row" style={{ gap: 12 }}>
              <span style={{ color: 'var(--blue-700)' }}><Icon name="users" size={22} /></span>
              <div>
                <div style={{ fontSize: 17, fontWeight: 600 }}>{класс.class_name}</div>
                <div className="muted" style={{ fontSize: 13.5, marginTop: 2 }}>педагог {класс.teacher_name}</div>
              </div>
            </div>
          </Card>
          <div style={{ marginTop: 16 }}>
            <Input
              id="klass-name" label="Как вас зовут" placeholder="Алина"
              value={имя} onChange={(e) => setИмя(e.target.value)} autoComplete="given-name"
            />
          </div>
          {ошибка ? <p style={{ color: 'var(--red)', fontSize: 13, marginTop: 12 }}>{ошибка}</p> : null}
          <div className="row" style={{ gap: 10, marginTop: 18 }}>
            <Button size="крупная" onClick={вступить} disabled={занято}>{ТЕКСТЫ.STUDENT_JOIN_CONFIRM_BUTTON}</Button>
            <Button size="крупная" variant="тихая" disabled={занято}
                    onClick={() => { setШаг('код'); setКласс(null); setОшибка(null); }}>
              {ТЕКСТЫ.STUDENT_JOIN_CANCEL_BUTTON}
            </Button>
          </div>
        </div>
      ) : null}

      {шаг === 'готово' ? (
        <div>
          <div className="row" style={{ gap: 12 }}>
            <span style={{ color: 'var(--green)' }}><Icon name="seal" size={26} /></span>
            <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Готово</h1>
          </div>
          <p style={{ fontSize: 15, lineHeight: 1.6, marginTop: 14 }}>{итог}</p>
          <Button size="крупная" arrow style={{ marginTop: 20 }} onClick={() => router.push('/app')}>
            В кабинет
          </Button>
        </div>
      ) : null}
    </AuthShell>
  );
}
