import Link from 'next/link';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';

/**
 * Страница 404 — своя, по-русски.
 *
 * Зачем модуль: без него Next.js показывает собственную заглушку
 * «404 — This page could not be found». На продукте для казахстанских
 * педагогов английская системная ошибка выглядит как поломка сайта, а
 * не как «такой страницы нет». Автор наткнулся ровно на неё 02.09.2026,
 * когда ссылка из письма привела в никуда.
 *
 * Чего осознанно не делает: не угадывает, куда человек шёл, и не
 * перенаправляет сам. Догадка, отправившая не туда, хуже честного
 * «страницы нет» с двумя понятными выходами.
 */
export default function NotFound() {
  return (
    <div style={{
      minHeight: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '40px 24px', background: 'var(--paper)',
    }}>
      <div style={{ maxWidth: 460, textAlign: 'center' }}>
        <span style={{ color: 'var(--blue-700)' }}><Icon name="file" size={34} /></span>
        <h1 style={{
          fontFamily: 'var(--serif)', fontSize: 34, lineHeight: 1.15,
          letterSpacing: '-0.025em', marginTop: 14,
        }}>
          Такой страницы нет
        </h1>
        <p className="muted" style={{ fontSize: 15, lineHeight: 1.65, marginTop: 12 }}>
          Возможно, ссылка устарела или в адресе опечатка. Если вы пришли сюда по
          ссылке из письма — запросите письмо заново, старые ссылки действуют недолго.
        </p>
        <div className="row" style={{ gap: 10, marginTop: 22, justifyContent: 'center' }}>
          <Link href="/vhod"><Button size="крупная" arrow>Войти в кабинет</Button></Link>
          <Link href="/"><Button size="крупная" variant="тихая">На главную</Button></Link>
        </div>
      </div>
    </div>
  );
}
