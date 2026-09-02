'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { Button } from '@/components/Button';
import { Input } from '@/components/Input';
import { supabase, входПоПочтеНастроен } from '@/lib/supabase';

/**
 * Новый пароль после письма «забыли пароль».
 *
 * Зачем модуль: кнопка «Забыли?» письмо отправляла, но вписать новый
 * пароль было негде — Supabase присылает человека обратно в приложение
 * с временной сессией и ждёт, что форму покажет оно. Без этой страницы
 * восстановление пароля обрывалось на середине.
 *
 * Чего осознанно не делает: не спрашивает старый пароль. Человек здесь
 * именно потому, что старого не помнит, а право сменить его доказано
 * ссылкой из письма на его почту.
 *
 * Длина в 8 знаков — та же, что на регистрации: два разных требования к
 * одному паролю сбивают с толку сильнее, чем одно строгое.
 */
export default function NovyyParolPage() {
  const router = useRouter();
  const [пароль, setПароль] = useState('');
  const [повтор, setПовтор] = useState('');
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [естьСессия, setЕстьСессия] = useState<boolean | null>(null);

  useEffect(() => {
    (async () => {
      if (!входПоПочтеНастроен()) { setЕстьСессия(false); return; }
      const { data } = await supabase().auth.getSession();
      setЕстьСессия(Boolean(data.session));
    })();
  }, []);

  async function сменить(событие: React.FormEvent) {
    событие.preventDefault();
    setОшибка(null);
    if (пароль !== повтор) {
      setОшибка('Пароли не совпадают.');
      return;
    }
    setЗанято(true);
    try {
      const { error } = await supabase().auth.updateUser({ password: пароль });
      if (error) {
        setОшибка('Не удалось сменить пароль. Возможно, ссылка из письма устарела — запросите новую.');
        return;
      }
      router.replace('/app');
    } finally {
      setЗанято(false);
    }
  }

  return (
    <AuthShell
      заголовок={<>Придумайте<br />новый пароль.</>}
      подзаголовок="Старый вводить не нужно — вы пришли по ссылке с вашей почты."
      ширинаФормы={520}
    >
      <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Новый пароль</h1>

      {естьСессия === false ? (
        <>
          <p className="muted" style={{ fontSize: 14, marginTop: 10, lineHeight: 1.6 }}>
            Эта страница открывается только по ссылке из письма о смене пароля.
            Если вы пришли сюда сами — запросите письмо на экране входа.
          </p>
          <Button
            size="крупная" arrow onClick={() => router.replace('/vhod')}
            style={{ width: '100%', justifyContent: 'center', marginTop: 20 }}
          >
            Вернуться ко входу
          </Button>
        </>
      ) : (
        <form onSubmit={сменить} style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 20 }}>
          <Input
            id="novyy-parol" label="Новый пароль" type="password" autoComplete="new-password"
            required minLength={8} placeholder="не короче 8 знаков"
            value={пароль} onChange={(e) => setПароль(e.target.value)}
          />
          <Input
            id="novyy-parol-2" label="Ещё раз" type="password" autoComplete="new-password"
            required minLength={8} placeholder="повторите пароль"
            value={повтор} onChange={(e) => setПовтор(e.target.value)}
          />
          {ошибка ? <p style={{ color: 'var(--red)', fontSize: 13 }}>{ошибка}</p> : null}
          <Button type="submit" size="крупная" disabled={занято || естьСессия === null}
                  style={{ width: '100%', justifyContent: 'center' }}>
            Сохранить пароль и войти
          </Button>
        </form>
      )}
    </AuthShell>
  );
}
