import Link from 'next/link';
import { Bar } from '@/components/Bar';
import { Chip } from '@/components/Chip';
import type { Дэшборд } from '@/lib/api';

/**
 * Расход тарифа за сегодня.
 *
 * Показывается в операциях, а не в тенге — решение автора от 01.09.
 * Числа и потолки приходят из core.dashboard, который берёт их из
 * core/limits.py. Здесь не считается ничего.
 *
 * Пилот безлимитный: полоска показывает расход, но ничего не запрещает.
 *
 * Отдельный случай — педагог, зарегистрированный только через сайт.
 * Расход и очередь в core/limits.py ключуются telegram_user_id, и у
 * такого профиля их просто нет: приходит 0 из 0. Рисовать пустую
 * полоску «0 / 0» нельзя — она читается как «лимит ноль». Вместо неё
 * говорим прямо, чего не хватает и что с этим делать.
 */

export function Расход({ данные }: { данные: Дэшборд }) {
  const { generate_ksp, generate_ktp, generate_ksp_limit, generate_ktp_limit } = данные.usage_today;
  // Потолок 0 значит не «нельзя ничего», а «расход по этому профилю не
  // считается»: он привязан к аккаунту Telegram, которого здесь нет.
  const расходИзвестен = generate_ksp_limit > 0 || generate_ktp_limit > 0;

  if (!расходИзвестен) {
    return (
      <section className="card" style={{ padding: '16px 18px', flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div className="row">
          <span className="lbl">Расход за сегодня</span>
          <Chip tone="серый" style={{ marginLeft: 'auto' }}>не считается</Chip>
        </div>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.6, marginTop: 12 }}>
          Расход считается по аккаунту в Telegram, а этот профиль заведён через сайт. Привяжите
          Telegram — и счётчики появятся здесь же.
        </p>
        <div className="row" style={{ marginTop: 'auto', paddingTop: 14, gap: 8 }}>
          <span className="muted" style={{ fontSize: 12 }}>Пилот безлимитный — ничего не ограничено</span>
          <Link href="/#tarify" style={{ marginLeft: 'auto', fontSize: 12.5, fontWeight: 600 }}>Тарифы</Link>
        </div>
      </section>
    );
  }

  return (
    <section className="card" style={{ padding: '16px 18px', flex: 1, display: 'flex', flexDirection: 'column' }}>
      <div className="row">
        <span className="lbl">Расход за сегодня</span>
        <Chip tone="зелёный" icon="seal" style={{ marginLeft: 'auto' }}>пилот без ограничений</Chip>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 13, marginTop: 14 }}>
        <Bar расход={generate_ksp} потолок={generate_ksp_limit} label="Черновики КСП сегодня" />
        <Bar расход={generate_ktp} потолок={generate_ktp_limit} tone="голубой" label="КТП за неделю" />
      </div>
      <div className="row" style={{ marginTop: 'auto', paddingTop: 14, gap: 8 }}>
        <span className="muted" style={{ fontSize: 12 }}>Школа подключит вас бесплатно</span>
        <Link href="/#tarify" style={{ marginLeft: 'auto', fontSize: 12.5, fontWeight: 600 }}>Тарифы</Link>
      </div>
    </section>
  );
}
