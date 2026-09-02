'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { ВходTelegram } from '@/components/auth/ВходTelegram';
import { RoleSwitch, type Роль } from '@/components/auth/RoleSwitch';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';
import { Input } from '@/components/Input';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { supabase, адресВозврата, входПоПочтеНастроен } from '@/lib/supabase';

/**
 * Экран входа по макету Vhod.
 *
 * Три двери, как и договаривались: Telegram, почта с паролем и код
 * класса для ученика. Восстановление пароля — средствами Supabase Auth,
 * своей формы «придумайте новый пароль» здесь нет и не нужно.
 *
 * Пароль всегда type="password" с автозаполнением current-password.
 * Кнопки «показать пароль» по умолчанию нет: экран входа в школе
 * открывают при классе.
 */
const ЧИСЛА = [
  ['≈12 сек', 'расшифровка урока'],
  ['№130', 'форма КСП соблюдена'],
  ['1 урок', 'записать бесплатно, без карты'],
];

export default function VhodPage() {
  const router = useRouter();
  const [роль, setРоль] = useState<Роль>('teacher');
  const [почта, setПочта] = useState('');
  const [пароль, setПароль] = useState('');
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [письмоОтправлено, setПисьмоОтправлено] = useState(false);

  const телеграм = process.env.NEXT_PUBLIC_TELEGRAM_BOT_URL;

  async function войти(событие: React.FormEvent) {
    событие.preventDefault();
    setОшибка(null);
    if (!входПоПочтеНастроен()) {
      setОшибка('Вход по почте ещё не настроен на этом сервере.');
      return;
    }
    setЗанято(true);
    try {
      const { error } = await supabase().auth.signInWithPassword({ email: почта, password: пароль });
      if (error) {
        // Supabase отвечает по-английски; человеку показываем свой текст
        // и не подсказываем, что именно не подошло — почта или пароль.
        setОшибка('Не удалось войти: проверьте почту и пароль.');
        return;
      }
      router.push('/app');
    } finally {
      setЗанято(false);
    }
  }

  async function восстановить() {
    setОшибка(null);
    if (!почта) {
      setОшибка('Впишите почту — на неё придёт ссылка для смены пароля.');
      return;
    }
    if (!входПоПочтеНастроен()) {
      setОшибка('Вход по почте ещё не настроен на этом сервере.');
      return;
    }
    await supabase().auth.resetPasswordForEmail(почта, {
      // Ссылка ведёт на служебную страницу возврата, а та уже
      // отправит человека вписывать новый пароль.
      redirectTo: адресВозврата(),
    });
    // Ответ одинаковый и для существующей почты, и для чужой: иначе форма
    // превращается в способ узнать, кто зарегистрирован.
    setПисьмоОтправлено(true);
  }

  return (
    <AuthShell
      заголовок={<>Урок прошёл —<br />документы уже собраны.</>}
      подзаголовок="Запись урока превращается в конспект, а конспект — в черновик КСП по форме приказа МОН РК №130. Любой педагог — сам, без школы: завёл класс, продиктовал код, работает."
      слева={(
        <div className="auth-promo-tiles" style={{ gap: 26, marginTop: 30 }}>
          {ЧИСЛА.map(([число, подпись], i) => (
            <div key={число} style={{ display: 'flex', gap: 26 }}>
              {i > 0 ? <div style={{ width: 1, background: 'rgba(255,255,255,.14)' }} /> : null}
              <div>
                <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em' }}>{число}</div>
                <div style={{ color: '#7FA6C4', fontSize: 12, marginTop: 2 }}>{подпись}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      сноска={(
        <span className="auth-promo-note row" style={{ gap: 9 }}>
          <Icon name="shield" size={15} />
          <span>Любой педагог регистрируется сам. Школа может подключить всех разом — тогда педагог не платит.</span>
        </span>
      )}
    >
      <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Вход в кабинет</h1>
      <p className="muted" style={{ fontSize: 14, marginTop: 6 }}>
        Нет аккаунта? <Link href="/registraciya" style={{ fontWeight: 600 }}>Зарегистрироваться за минуту</Link> — первая запись урока бесплатно.
      </p>

      <div style={{ marginTop: 24 }}>
        <RoleSwitch значение={роль} onChange={setРоль} />
      </div>

      {роль === 'student' ? (
        <>
          <p className="muted" style={{ fontSize: 14, marginTop: 20, lineHeight: 1.6 }}>
            {ТЕКСТЫ.STUDENT_JOIN_ASK_CODE}
          </p>
          <Link href="/klass">
            <Button size="крупная" icon="key" arrow style={{ width: '100%', justifyContent: 'center', marginTop: 12 }}>
              Ввести код класса
            </Button>
          </Link>
        </>
      ) : (
        <>
          {/* Настоящий вход через Telegram: виджет отдаёт подписанные
              данные, сервер сверяет подпись. Кнопка-ссылка на бота
              осталась ниже — она никуда не пускала, но остаётся
              запасным путём, если у бота не задан домен. */}
          <ВходTelegram onВошёл={() => router.push('/app')} />

          {телеграм ? (
            <a href={телеграм} target="_blank" rel="noreferrer">
              <Button size="крупная" icon="send" variant="тихая" style={{ width: '100%', justifyContent: 'center', marginTop: 12 }}>
                Открыть бота в Telegram
              </Button>
            </a>
          ) : null}

          <div className="row" style={{ margin: '22px 0', gap: 12 }}>
            <div style={{ flex: 1, height: 1, background: 'var(--hair)' }} />
            <span className="muted" style={{ fontSize: 11.5 }}>или по почте школы</span>
            <div style={{ flex: 1, height: 1, background: 'var(--hair)' }} />
          </div>

          <form onSubmit={войти} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Input
              id="vhod-mail" label="Рабочая почта" type="email" autoComplete="email" required
              placeholder="a.tleubaeva@school12.kst.edu.kz"
              value={почта} onChange={(e) => setПочта(e.target.value)}
            />
            <div>
              <div className="row"><span className="lbl">Пароль</span>
                <button type="button" onClick={восстановить} className="btn btn-3" style={{ marginLeft: 'auto', height: 'auto', padding: 0, fontSize: 12 }}>
                  Забыли?
                </button>
              </div>
              <div style={{ marginTop: 7 }}>
                <Input
                  id="vhod-pass" type="password" autoComplete="current-password" required
                  placeholder="••••••••••"
                  value={пароль} onChange={(e) => setПароль(e.target.value)}
                />
              </div>
            </div>
            {письмоОтправлено ? (
              <p className="muted" style={{ fontSize: 13 }}>
                Если такая почта зарегистрирована, письмо со ссылкой уже отправлено. Проверьте ящик.
              </p>
            ) : null}
            {ошибка ? <p style={{ color: 'var(--red)', fontSize: 13 }}>{ошибка}</p> : null}
            <Button type="submit" size="крупная" variant="тихая" disabled={занято} style={{ width: '100%', justifyContent: 'center' }}>
              Войти
            </Button>
          </form>
        </>
      )}

      <div className="card" style={{ marginTop: 24, background: 'var(--blue-soft)', boxShadow: '0 0 0 1px rgba(28,116,188,.14)', padding: '13px 15px', display: 'flex', gap: 12 }}>
        <span style={{ color: 'var(--blue-700)' }}><Icon name="key" size={18} /></span>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Ученик заходит по коду класса</div>
          <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>Учитель диктует код на уроке — отдельная регистрация не нужна.</div>
        </div>
      </div>

      <p className="muted" style={{ fontSize: 11.5, marginTop: 22, lineHeight: 1.5 }}>
        Продолжая, вы соглашаетесь с условиями обработки данных. Черновики формирует нейросеть — каждый файл требует вашей проверки.
      </p>
    </AuthShell>
  );
}
