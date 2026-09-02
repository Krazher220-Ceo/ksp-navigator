'use client';

import { useEffect, useState } from 'react';
import { Bar } from '@/components/Bar';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon, type IconName } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { ПОЧЕМУ_ЕСТЬ_ПРЕДЕЛ, ТАРИФЫ_ПЕДАГОГА, ТАРИФЫ_УЧЕНИКА, ТАРИФ_ШКОЛЫ } from '@/content/tariffs';
import { ТЕКСТЫ } from '@/content/texts.generated';
import { ОшибкаApi, апи, type Дэшборд } from '@/lib/api';

/**
 * Тариф и оплата — витрина по артборду Tarify.
 *
 * Оплаты здесь нет и в пилоте не будет: кнопки ведут на объяснение, а не
 * на платёжного провайдера. Ни одной строки, которая что-то ограничивала
 * бы, в коде тоже нет — пилот безлимитный, а тарифы в `core/limits.py`
 * остаются выключенной заглушкой.
 *
 * Текущее потребление показывается в операциях, а не в тенге: решение
 * автора от 01.09. Числа приходят из core.dashboard — того же расчёта,
 * что видит бот.
 */
const ЗНАЧКИ: Record<string, { знак: IconName; цвет: string }> = {
  free: { знак: 'spark', цвет: 'var(--ink-3)' },
  teacher: { знак: 'seal', цвет: 'var(--blue-600)' },
  teacher_pro: { знак: 'crown', цвет: 'var(--gold)' },
};

export default function TarifPage() {
  const [данные, setДанные] = useState<Дэшборд | null>(null);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [спросили, setСпросили] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setДанные(await апи.дэшборд());
      } catch (e) {
        setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
      }
    })();
  }, []);

  const расход = данные?.usage_today;
  const расходИзвестен = Boolean(расход && (расход.generate_ksp_limit > 0 || расход.generate_ktp_limit > 0));

  return (
    <>
      <TopBar date="Аккаунт" title="Тариф и оплата" searchPlaceholder="Тема, класс или код цели" />

      <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 14, overflowY: 'auto' }}>
        {ошибка ? (
          <Card style={{ padding: '13px 16px', display: 'flex', gap: 12, background: 'var(--red-soft)' }}>
            <span style={{ color: 'var(--red)', flex: 'none' }}><Icon name="warn" size={18} /></span>
            <div style={{ fontSize: 13.5, lineHeight: 1.6 }}>{ошибка}</div>
          </Card>
        ) : null}

        <Card>
          <CardHead icon="seal" iconColor="var(--green)" title="Сейчас у вас пилот"
                    aside={<Chip tone="зелёный">без ограничений</Chip>} />
          <CardBody>
            <p style={{ fontSize: 14, lineHeight: 1.65 }}>
              Пока идёт пилот, ничего не ограничено и платить не нужно. Числа ниже показывают
              расход в операциях — чтобы было видно нагрузку, а не чтобы что-то запретить.
            </p>
            {расходИзвестен && расход ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 13, marginTop: 16, maxWidth: 460 }}>
                <Bar расход={расход.generate_ksp} потолок={расход.generate_ksp_limit} label="Черновики КСП сегодня" />
                <Bar расход={расход.generate_ktp} потолок={расход.generate_ktp_limit} tone="голубой" label="КТП за неделю" />
              </div>
            ) : (
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.6, marginTop: 12 }}>
                Расход считается по аккаунту в Telegram. Привяжите его — и счётчики появятся здесь же.
              </p>
            )}
          </CardBody>
        </Card>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 14, alignItems: 'start' }}>
          {ТАРИФЫ_ПЕДАГОГА.map((тариф) => {
            const значок = ЗНАЧКИ[тариф.ключ];
            const главный = тариф.ключ === 'teacher';
            return (
              <Card key={тариф.ключ} style={{ padding: 24, display: 'flex', flexDirection: 'column',
                                              boxShadow: главный ? '0 0 0 2px var(--blue-soft), var(--lift-2)' : undefined }}>
                <div className="row" style={{ gap: 9 }}>
                  <span style={{ color: значок.цвет }}><Icon name={значок.знак} size={20} /></span>
                  <h3 style={{ fontSize: 18 }}>{тариф.название}</h3>
                  {главный ? <Chip style={{ marginLeft: 'auto' }}>берут чаще всего</Chip> : null}
                </div>
                <div className="row" style={{ alignItems: 'baseline', gap: 8, marginTop: 16 }}>
                  <span style={{ fontSize: 34, fontWeight: 700, letterSpacing: '-0.04em' }}>{тариф.ценаВМесяц}</span>
                  {тариф.ценаЗаГод ? <span className="muted" style={{ fontSize: 14 }}>/ мес</span> : null}
                </div>
                <p className="muted" style={{ fontSize: 13.5, marginTop: 7, lineHeight: 1.55 }}>
                  {тариф.ценаЗаГод ? `За учебный год — ${тариф.ценаЗаГод}. ` : ''}{тариф.пояснение}
                </p>
                <div className="sep" style={{ margin: '16px 0' }} />
                <ul style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 14 }}>
                  {тариф.строки.map((строка) => (
                    <li key={строка} className="row" style={{ gap: 11, alignItems: 'flex-start' }}>
                      <span style={{ color: 'var(--green)' }}><Icon name="check" size={16} /></span>
                      <span>{строка}</span>
                    </li>
                  ))}
                </ul>
                <Button
                  variant={главный ? 'основная' : 'тихая'}
                  style={{ marginTop: 20, width: '100%', justifyContent: 'center' }}
                  onClick={() => setСпросили(true)}
                >
                  Выбрать «{тариф.название}»
                </Button>
              </Card>
            );
          })}
        </div>

        {спросили ? (
          // Кнопка ведёт сюда, а не к платёжному провайдеру: оплаты в
          // пилоте нет, и делать вид, что она есть, нельзя.
          <Card style={{ background: 'var(--blue-soft)' }}>
            <CardBody style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 1 }}><Icon name="clock" size={18} /></span>
              <div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>Оплата пока не включена</div>
                <p style={{ fontSize: 13.5, lineHeight: 1.6, marginTop: 4 }}>
                  Идёт пилот: всё работает без ограничений и без карты. Когда оплата появится, мы
                  напишем заранее — списаний без вашего согласия не будет.
                </p>
              </div>
            </CardBody>
          </Card>
        ) : null}

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.1fr) minmax(0,1fr)', gap: 14 }}>
          <Card style={{ padding: '22px 24px', display: 'flex', alignItems: 'center', gap: 20,
                         background: 'linear-gradient(140deg,#124066,#08203A)', color: '#fff' }}>
            <span style={{ color: '#7FD9E8', flex: 'none' }}><Icon name="cap" size={26} /></span>
            <div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>
                Школа подключает всех разом — {ТАРИФ_ШКОЛЫ.цена} {ТАРИФ_ШКОЛЫ.единица}
              </div>
              <div style={{ color: '#A9C6DE', fontSize: 13.5, marginTop: 4, lineHeight: 1.55 }}>
                {ТАРИФ_ШКОЛЫ.выгода} Если школа подключила класс, ученики этого класса не платят ничего.
              </div>
            </div>
          </Card>

          <Card style={{ padding: '22px 24px' }}>
            <div className="row" style={{ gap: 10 }}>
              <span style={{ color: 'var(--blue-700)' }}><Icon name="userplus" size={20} /></span>
              <h3 style={{ fontSize: 16 }}>Тарифы учеников</h3>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,minmax(0,1fr))', gap: 12, marginTop: 16 }}>
              {ТАРИФЫ_УЧЕНИКА.map((т) => (
                <div key={т.ключ} style={{
                  background: т.ключ === 'student' ? 'var(--blue-soft)' : 'rgba(11,26,43,.035)',
                  borderRadius: 14, padding: 14,
                }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{т.название}</div>
                  <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.03em', marginTop: 6 }}>{т.цена}</div>
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>{т.норма}</div>
                </div>
              ))}
            </div>
            <p className="muted" style={{ fontSize: 12.5, marginTop: 14, lineHeight: 1.55 }}>
              Полный конспект не даёт ни один тариф — его присылает учитель, вручную.
            </p>
          </Card>
        </div>

        <Card>
          <CardBody style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <span style={{ color: 'var(--ink-3)', flex: 'none', paddingTop: 1 }}><Icon name="chart" size={17} /></span>
            <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
              Почему у тарифов есть дневной предел: {ПОЧЕМУ_ЕСТЬ_ПРЕДЕЛ}
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
