'use client';

import { useState } from 'react';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';
import { ОшибкаApi, апи } from '@/lib/api';
import { ТЕКСТЫ } from '@/content/texts.generated';

/**
 * Экран согласия — отдельным экраном и до первого действия, а не
 * галочкой в подвале формы.
 *
 * Текст берётся из bot/texts.py и здесь не сочиняется: он законно
 * значим (статья 8 Закона «О персональных данных», трансграничная
 * передача названа прямым текстом), и расходиться у бота с кабинетом ему
 * нельзя. У ученика своя редакция — с упоминанием законного
 * представителя.
 */
type Props = {
  роль: 'teacher' | 'student';
  onПринято: () => void;
  onОтказ?: () => void;
  /**
   * Отправлять ли согласие на сервер прямо сейчас.
   *
   * На регистрации — нет: аккаунта ещё не существует, и записывать
   * согласие некому. Экран всё равно показывается первым, а запись
   * уходит сразу после создания аккаунта, до создания профиля. Сервер
   * это и сторожит: профиля без согласия не появится.
   */
  сохранять?: boolean;
};

export function Соглашение({ роль, onПринято, onОтказ, сохранять = true }: Props) {
  const [ошибка, setОшибка] = useState<string | null>(null);
  const [занято, setЗанято] = useState(false);
  const [отказался, setОтказался] = useState(false);

  const текст = роль === 'student' ? ТЕКСТЫ.STUDENT_CONSENT_TEXT : ТЕКСТЫ.CONSENT_TEXT;

  async function принять() {
    setЗанято(true);
    setОшибка(null);
    try {
      if (сохранять) await апи.согласие();
      onПринято();
    } catch (e) {
      setОшибка(e instanceof ОшибкаApi ? e.message : ТЕКСТЫ.ERROR_UNEXPECTED);
    } finally {
      setЗанято(false);
    }
  }

  if (отказался) {
    return (
      <div className="card" style={{ padding: 24, display: 'flex', gap: 13 }}>
        <span style={{ color: 'var(--ink-3)', flex: 'none' }}><Icon name="lock" size={20} /></span>
        <p style={{ fontSize: 14.5, lineHeight: 1.6 }}>{ТЕКСТЫ.CONSENT_DECLINED}</p>
      </div>
    );
  }

  return (
    <div>
      <h1 style={{ fontSize: 25, letterSpacing: '-0.02em' }}>Условия работы</h1>
      <div className="card" style={{ marginTop: 18, padding: '20px 22px', maxHeight: '46vh', overflowY: 'auto' }}>
        {/* Абзацы разделены пустой строкой — так они и лежат в bot/texts.py. */}
        {текст.split('\n\n').map((абзац) => (
          <p key={абзац} style={{ fontSize: 14.5, lineHeight: 1.65, marginTop: 12, whiteSpace: 'pre-line' }}>
            {абзац}
          </p>
        ))}
      </div>
      {ошибка ? (
        <p style={{ color: 'var(--red)', fontSize: 13, marginTop: 12 }}>{ошибка}</p>
      ) : null}
      <div className="row" style={{ gap: 10, marginTop: 18 }}>
        <Button size="крупная" onClick={принять} disabled={занято}>
          {ТЕКСТЫ.CONSENT_ACCEPT_BUTTON}
        </Button>
        <Button
          size="крупная"
          variant="тихая"
          onClick={() => { setОтказался(true); onОтказ?.(); }}
          disabled={занято}
        >
          {ТЕКСТЫ.CONSENT_DECLINE_BUTTON}
        </Button>
      </div>
    </div>
  );
}
