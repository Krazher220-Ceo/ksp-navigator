import Link from 'next/link';
import { Button } from '@/components/Button';
import { КОНТАКТЫ } from '@/content/contacts';
import { Reveal } from './Reveal';

/**
 * Последний экран. Главная кнопка — та же «Записать первый урок»,
 * третий и последний раз на странице.
 *
 * Строка контактов рисуется только из того, что действительно есть:
 * почта и Telegram в content/contacts.ts пока null, и вместо них здесь
 * не появляется ничего. Квадратные скобки из макета — это дырка, а не
 * текст, и публиковать её нельзя.
 */
export function CallToAction() {
  const контакты = [КОНТАКТЫ.почта, КОНТАКТЫ.телеграм, КОНТАКТЫ.город].filter(Boolean) as string[];
  return (
    <Reveal className="bg-kz" style={{ color: '#fff', padding: 'clamp(48px, 7vw, 80px) var(--pad-x)', textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
      <div style={{ position: 'relative', maxWidth: 620, margin: '0 auto' }}>
        <h2 className="an" style={{ fontFamily: 'var(--serif)', fontSize: 'clamp(27px, 5vw, 40px)', lineHeight: 1.18, letterSpacing: '-0.025em', color: '#fff' }}>
          Запишите свой первый урок сегодня
        </h2>
        <p className="an d1" style={{ color: '#A9C6DE', fontSize: 'clamp(15px, 1.8vw, 16.5px)', lineHeight: 1.6, marginTop: 14 }}>
          Регистрация занимает минуту, карта не нужна. Начать можно с одного класса — расширить
          всегда успеете. Если школа подключит вас по договору, тариф снимется автоматически.
        </p>
        <div className="an d2 row actions" style={{ justifyContent: 'center', gap: 12, marginTop: 28 }}>
          <Link href="/registraciya">
            <Button size="крупная" arrow style={{
              background: 'linear-gradient(180deg,#1ECBE4,#009FBB)', color: '#052A38',
              boxShadow: 'inset 0 1px 0 rgba(255,255,255,.5),0 12px 26px -12px rgba(0,175,202,.9)',
            }}>
              Записать первый урок
            </Button>
          </Link>
          {/* Школьная смета — разговор письмом. Пока почта для заявок не
              заведена (content/contacts.ts), кнопка нарисована
              выключенной: ссылка в никуда хуже отсутствующей ссылки, а
              выдумать адрес нельзя. Тот же приём, что у переключателя
              языка в шапке. */}
          {КОНТАКТЫ.почта ? (
            <a href={`mailto:${КОНТАКТЫ.почта}?subject=${encodeURIComponent('Смета для школы')}`}>
              <Button size="крупная" style={{ background: 'rgba(255,255,255,.11)', color: '#fff', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.14)' }}>
                Я из школы — нужна смета
              </Button>
            </a>
          ) : (
            <Button
              size="крупная" disabled aria-disabled="true"
              title="Почта для заявок школ ещё не заведена"
              style={{ background: 'rgba(255,255,255,.11)', color: '#fff', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.14)', opacity: 0.5 }}
            >
              Я из школы — нужна смета
            </Button>
          )}
        </div>
        <div className="an d3 row" style={{ justifyContent: 'center', gap: 22, marginTop: 22, color: '#7FA6C4', fontSize: 13 }}>
          {контакты.map((к, i) => (
            <span key={к} className="row" style={{ gap: 22 }}>
              {i > 0 ? <span>·</span> : null}
              <span>{к}</span>
            </span>
          ))}
        </div>
      </div>
    </Reveal>
  );
}
