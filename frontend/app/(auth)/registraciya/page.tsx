'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { RoleSwitch, type Роль } from '@/components/auth/RoleSwitch';
import { Соглашение } from '@/components/auth/Соглашение';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';
import { Input } from '@/components/Input';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи } from '@/lib/api';
import { supabase, адресВозврата, входПоПочтеНастроен } from '@/lib/supabase';

/**
 * Регистрация педагога по макету Registraciya.
 *
 * Шаг первый — условия работы, отдельным экраном и до всего остального.
 * Шаг второй — форма. Порядок именно такой: согласие спрашивается до
 * первого действия, а не галочкой под кнопкой. Сервер это и сторожит —
 * профиля без согласия он не создаст.
 *
 * Ученик регистрации не проходит: он вступает в класс по коду, и
 * переключатель уводит его на этот экран.
 */
const ПРЕИМУЩЕСТВА: [('mic' | 'users' | 'card'), string, string][] = [
  ['mic', 'Одна запись урока', 'конспект вашими словами и черновик КСП по нему'],
  ['users', 'Один класс до 15 учеников', 'код приглашения диктуется вслух на уроке'],
  ['card', 'Карта не нужна', 'плюс три черновика КСП каждый месяц'],
];

export default function RegistraciyaPage() {
  const router = useRouter();
  const [роль, setРоль] = useState<Роль>('teacher');
  const [условияПриняты, setУсловияПриняты] = useState(false);
  const [поля, setПоля] = useState({ name: '', subject: '', school: '', city: '', email: '', password: '' });
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [подтвердитеПочту, setПодтвердитеПочту] = useState(false);

  const менять = (ключ: keyof typeof поля) => (е: React.ChangeEvent<HTMLInputElement>) =>
    setПоля((прежние) => ({ ...прежние, [ключ]: е.target.value }));

  async function создать(событие: React.FormEvent) {
    событие.preventDefault();
    setОшибка(null);
    if (!входПоПочтеНастроен()) {
      setОшибка('Регистрация по почте ещё не настроена на этом сервере.');
      return;
    }
    setЗанято(true);
    try {
      const клиент = supabase();

      // Человек мог уже войти — например, подтвердил почту по ссылке из
      // письма и вернулся. Тогда аккаунт есть, а профиля нет, и заводить
      // второй аккаунт не нужно: достаточно дозаполнить профиль.
      const { data: текущая } = await клиент.auth.getSession();
      if (текущая.session) {
        await апи.согласие();
        await апи.регистрацияПедагога({
          name: поля.name, subject: поля.subject, school: поля.school, city: поля.city,
        });
        router.push('/app');
        return;
      }

      const { data, error } = await клиент.auth.signUp({
        email: поля.email,
        password: поля.password,
        // Без этого письмо приводит на Site URL, то есть на лендинг,
        // где токены из адреса разбирать некому.
        options: { emailRedirectTo: адресВозврата() },
      });
      if (error) {
        // Текст Supabase английский; человеку показываем свой, но
        // разводим два разных случая — иначе «проверьте почту и пароль»
        // говорится и тому, кто уже зарегистрирован.
        const уже = /already|registered|exists/i.test(error.message);
        setОшибка(уже
          ? 'Такая почта уже зарегистрирована. Войдите — ссылка «Войти» выше.'
          : 'Не удалось создать аккаунт: проверьте почту и пароль (не короче восьми знаков).');
        return;
      }

      // Ключевое место. Если в проекте Supabase включено подтверждение
      // почты, signUp НЕ выдаёт сессию: data.session === null. Дальше
      // некому предъявить токен, и /consent с /teacher честно отвечают
      // 401 — со стороны это выглядит как «регистрация не работает».
      // Поэтому: сессии нет — пробуем войти сразу, а если и вход не
      // прошёл, объясняем человеку, что письмо ждёт подтверждения.
      let сессия = data.session;
      if (!сессия) {
        const вход = await клиент.auth.signInWithPassword({
          email: поля.email, password: поля.password,
        });
        сессия = вход.data.session;
      }
      if (!сессия) {
        setПодтвердитеПочту(true);
        return;
      }

      // Согласие записывается сразу, как только появился аккаунт, и
      // строго до профиля: в профиле уже персональные данные педагога.
      await апи.согласие();
      await апи.регистрацияПедагога({
        name: поля.name, subject: поля.subject, school: поля.school, city: поля.city,
      });
      router.push('/app');
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  return (
    <AuthShell
      ширинаФормы={640}
      заголовок={<>Первый урок —<br />бесплатно и без карты.</>}
      подзаголовок="Регистрация занимает минуту. Школа не нужна: любой педагог заводит свой класс сам и приглашает учеников кодом."
      слева={(
        <div className="auth-promo-tiles" style={{ flexDirection: 'column', gap: 12, marginTop: 30 }}>
          {ПРЕИМУЩЕСТВА.map(([знак, заголовок, подпись]) => (
            <div key={заголовок} className="row" style={{ gap: 13, background: 'rgba(255,255,255,.06)', borderRadius: 14, padding: '14px 16px', boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)' }}>
              <span style={{ color: '#7FD9E8' }}><Icon name={знак} size={20} /></span>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{заголовок}</div>
                <div style={{ color: '#8FB6D2', fontSize: 12.5, marginTop: 1 }}>{подпись}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      сноска={(
        <span className="auth-promo-note row" style={{ gap: 10 }}>
          <Icon name="shield" size={16} />
          <span>Аудиозапись удаляется сразу после расшифровки. Об ученике храним только имя.</span>
        </span>
      )}
    >
      {подтвердитеПочту ? (
        <div>
          <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Подтвердите почту</h1>
          <p style={{ fontSize: 14.5, lineHeight: 1.65, marginTop: 14 }}>
            Аккаунт создан, но в вашем проекте Supabase включено подтверждение почты: мы отправили
            письмо на {поля.email}. Откройте ссылку из письма и вернитесь сюда — профиль педагога
            создастся при первом входе.
          </p>
          <Link href="/vhod">
            <Button size="крупная" arrow style={{ marginTop: 18 }}>Перейти ко входу</Button>
          </Link>
        </div>
      ) : !условияПриняты ? (
        <Соглашение роль={роль} сохранять={false} onПринято={() => setУсловияПриняты(true)} />
      ) : (
        <>
          <h1 style={{ fontSize: 26, letterSpacing: '-0.025em' }}>Создать аккаунт</h1>
          <p className="muted" style={{ fontSize: 14, marginTop: 6 }}>
            Уже есть? <Link href="/vhod" style={{ fontWeight: 600 }}>Войти</Link>
          </p>

          <div style={{ marginTop: 22 }}>
            <RoleSwitch значение={роль} onChange={(р) => { setРоль(р); if (р === 'student') router.push('/klass'); }} высота={40} />
          </div>

          <form onSubmit={создать} style={{ display: 'flex', flexDirection: 'column', gap: 13, marginTop: 20 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 13 }}>
              <Input id="reg-name" label="Имя и фамилия" required autoComplete="name"
                     placeholder="Айгүл Тлеубаева" value={поля.name} onChange={менять('name')} />
              <Input id="reg-subject" label="Предмет" required
                     placeholder="Физика" value={поля.subject} onChange={менять('subject')} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 190px), 1fr))', gap: 13 }}>
              <Input id="reg-school" label="Школа" placeholder="Школа №12"
                     value={поля.school} onChange={менять('school')} />
              <Input id="reg-city" label="Город" placeholder="Костанай"
                     value={поля.city} onChange={менять('city')} />
            </div>
            <Input id="reg-mail" label="Почта" type="email" required autoComplete="email"
                   placeholder="вы@почта.kz" value={поля.email} onChange={менять('email')} />
            <Input id="reg-pass" label="Пароль" type="password" required autoComplete="new-password"
                   minLength={8} placeholder="••••••••••"
                   value={поля.password} onChange={менять('password')} />

            <div className="row" style={{ gap: 11, marginTop: 5, alignItems: 'flex-start' }}>
              <div style={{ width: 19, height: 19, borderRadius: 6, background: 'linear-gradient(180deg,#2C86CE,#14548C)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', flex: 'none', marginTop: 1, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.3)' }}>
                <Icon name="check" size={12} />
              </div>
              <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
                Условия работы приняты на предыдущем шаге. Черновики формирует нейросеть — каждый файл требует вашей проверки.
              </p>
            </div>

            {ошибка ? <p style={{ color: 'var(--red)', fontSize: 13 }}>{ошибка}</p> : null}

            <Button type="submit" size="крупная" arrow disabled={занято} style={{ width: '100%', justifyContent: 'center', marginTop: 5 }}>
              Создать аккаунт и записать урок
            </Button>
          </form>
        </>
      )}
    </AuthShell>
  );
}
