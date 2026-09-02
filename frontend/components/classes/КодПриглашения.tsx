'use client';

import { useState } from 'react';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';

/**
 * Код приглашения — крупно, чтобы его было видно с последней парты.
 *
 * Ссылки нет намеренно: код диктуется вслух на уроке, и это единственный
 * способ вступить в класс. Ссылка ушла бы дальше класса первым же
 * репостом.
 *
 * «Показать на весь экран» открывает код во весь экран — учитель
 * выводит его на проектор, а не переписывает на доску.
 */
type Props = { класс: string; код: string; onПеревыпустить: () => void; занято?: boolean };

export function КодПриглашения({ класс, код, onПеревыпустить, занято }: Props) {
  const [воВесьЭкран, setВоВесьЭкран] = useState(false);

  if (воВесьЭкран) {
    return (
      <div
        onClick={() => setВоВесьЭкран(false)}
        style={{
          position: 'fixed', inset: 0, zIndex: 50, background: 'var(--navy-2)', color: '#fff',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: 24, cursor: 'pointer', padding: 40, textAlign: 'center',
        }}
      >
        <div className="lbl" style={{ color: '#8FB6D2' }}>Код приглашения в {класс}</div>
        <div className="mono" style={{ fontSize: 'min(18vw, 190px)', fontWeight: 600, letterSpacing: '.12em', lineHeight: 1 }}>
          {код}
        </div>
        <div style={{ color: '#A9C6DE', fontSize: 16 }}>Нажмите, чтобы закрыть</div>
      </div>
    );
  }

  return (
    <section
      className="card"
      style={{
        background: 'linear-gradient(115deg,#12406B,#0D2E4E)', color: '#fff',
        padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 26, flexWrap: 'wrap',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div className="lbl" style={{ color: '#8FB6D2' }}>Код приглашения в {класс}</div>
        <div className="mono" style={{ fontSize: 42, fontWeight: 600, letterSpacing: '.14em', marginTop: 6, lineHeight: 1 }}>
          {код}
        </div>
        <div style={{ color: '#A9C6DE', fontSize: 12.5, marginTop: 6 }}>
          Продиктуйте вслух на уроке — ученик вводит его в приложении. Ссылка не нужна.
        </div>
      </div>
      <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', gap: 8, flex: 'none' }}>
        <Button size="малая" icon="refresh" disabled={занято} onClick={onПеревыпустить}
                style={{ background: 'var(--sky)', color: 'var(--navy)' }}>
          Перевыпустить
        </Button>
        <Button size="малая" icon="eye" onClick={() => setВоВесьЭкран(true)}
                style={{ background: 'rgba(255,255,255,.12)', color: '#fff' }}>
          Показать на весь экран
        </Button>
      </div>
      <p style={{ flexBasis: '100%', color: '#7FA6C4', fontSize: 11.5, margin: 0 }}>
        <Icon name="warn" size={12} style={{ display: 'inline-block', verticalAlign: -1, marginRight: 5 }} />
        После перевыпуска старый код перестаёт действовать сразу.
      </p>
    </section>
  );
}
