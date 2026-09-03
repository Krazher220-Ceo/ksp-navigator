import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Card } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon } from '@/components/Icon';
import { DocsNav } from '@/components/landing/DocsNav';
import { РазделыСлева } from '@/components/landing/РазделыСлева';
import { ОглавлениеРаздела } from '@/components/landing/ОглавлениеРаздела';
import { РАЗДЕЛЫ, РАЗДЕЛ_ПО_КЛЮЧУ, type Блок } from '@/content/docs-razdely';

/**
 * Раздел документации.
 *
 * Один шаблон на все разделы: содержимое лежит в content/docs-razdely.ts.
 * До 02.09.2026 написан был ровно один раздел из восьми, остальные семь
 * стояли в списке неактивными — читателю это выглядит как недоделанный
 * продукт.
 *
 * Раздел «Аналитика продукта» остался отдельной страницей по адресу
 * /docs: он свёрстан по своему артборду, и переливать его в общий
 * шаблон значило бы потерять утверждённую вёрстку ради единообразия.
 */
const ОБНОВЛЕНО = '2 сентября 2026';

const ВРЕЗКИ = {
  важно: { фон: 'var(--blue-soft)', цвет: 'var(--blue-800)', значок: 'warn' as const, подпись: 'Важно' },
  честно: { фон: 'var(--green-soft)', цвет: 'var(--green)', значок: 'check' as const, подпись: 'Как есть' },
  нельзя: { фон: 'var(--gold-soft)', цвет: '#7A5A12', значок: 'lock' as const, подпись: 'Так не бывает' },
};

export function generateStaticParams() {
  return РАЗДЕЛЫ.map((р) => ({ razdel: р.ключ }));
}

export async function generateMetadata(
  { params }: { params: Promise<{ razdel: string }> },
): Promise<Metadata> {
  const { razdel } = await params;
  const раздел = РАЗДЕЛ_ПО_КЛЮЧУ.get(razdel);
  if (!раздел) return {};
  return { title: `${раздел.название} — документация Mazmun`, description: раздел.подзаголовок };
}

function Кусок({ блок }: { блок: Блок }) {
  if (блок.тип === 'абзац') {
    return <p style={{ fontSize: 15.5, color: 'var(--ink-2)', lineHeight: 1.7, marginTop: 12 }}>{блок.текст}</p>;
  }
  if (блок.тип === 'список') {
    return (
      <ul style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 9 }}>
        {блок.пункты.map((пункт) => (
          <li key={пункт} style={{ display: 'flex', gap: 11, fontSize: 15, lineHeight: 1.65 }}>
            <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 3 }}><Icon name="check" size={15} /></span>
            <span>{пункт}</span>
          </li>
        ))}
      </ul>
    );
  }
  if (блок.тип === 'шаги') {
    return (
      <ol style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 11 }}>
        {блок.пункты.map((пункт, i) => (
          <li key={пункт} style={{ display: 'flex', gap: 12, fontSize: 15, lineHeight: 1.65 }}>
            <span style={{
              flex: 'none', width: 24, height: 24, borderRadius: '50%', background: 'var(--blue-soft)',
              color: 'var(--blue-800)', fontSize: 12.5, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center', marginTop: 1,
            }}>{i + 1}</span>
            <span>{пункт}</span>
          </li>
        ))}
      </ol>
    );
  }
  const вид = ВРЕЗКИ[блок.врезка.вид];
  return (
    <Card style={{ marginTop: 16, padding: '15px 17px', background: вид.фон, display: 'flex', gap: 12 }}>
      <span style={{ color: вид.цвет, flex: 'none', paddingTop: 1 }}><Icon name={вид.значок} size={17} /></span>
      <div>
        <div className="lbl" style={{ color: вид.цвет }}>{вид.подпись}</div>
        <p style={{ fontSize: 14.5, lineHeight: 1.6, marginTop: 5 }}>{блок.врезка.текст}</p>
      </div>
    </Card>
  );
}

export default async function RazdelPage({ params }: { params: Promise<{ razdel: string }> }) {
  const { razdel } = await params;
  const раздел = РАЗДЕЛ_ПО_КЛЮЧУ.get(razdel);
  if (!раздел) notFound();

  return (
    <>
      <DocsNav />
      <div className="docs-grid" style={{ display: 'grid', gap: 'clamp(18px, 3.4vw, 40px)', padding: 'clamp(24px, 4vw, 38px) var(--pad-x) clamp(40px, 6vw, 70px)' }}>
        <РазделыСлева активный={раздел.ключ} />

        <main style={{ maxWidth: 720 }}>
          <div className="row" style={{ gap: 8 }}>
            <Chip icon={раздел.значок}>{раздел.название}</Chip>
            <span className="muted" style={{ fontSize: 12.5 }}>обновлено {ОБНОВЛЕНО}</span>
          </div>
          <h1 style={{ fontFamily: 'var(--serif)', fontSize: 'clamp(28px, 5vw, 40px)', lineHeight: 1.15, letterSpacing: '-0.025em', marginTop: 14 }}>
            {раздел.название}
          </h1>
          <p style={{ fontSize: 17, color: 'var(--ink-2)', lineHeight: 1.65, marginTop: 14 }}>
            {раздел.подзаголовок}
          </p>

          {раздел.главы.map((глава) => (
            <section key={глава.якорь}>
              <h2 id={глава.якорь} style={{ fontFamily: 'var(--serif)', fontSize: 26, marginTop: 44, letterSpacing: '-0.015em', scrollMarginTop: 84 }}>
                {глава.заголовок}
              </h2>
              {глава.блоки.map((блок, i) => <Кусок key={i} блок={блок} />)}
            </section>
          ))}
        </main>

        <ОглавлениеРаздела главы={раздел.главы.map((г) => ({ якорь: г.якорь, текст: г.заголовок }))} />
      </div>
    </>
  );
}
