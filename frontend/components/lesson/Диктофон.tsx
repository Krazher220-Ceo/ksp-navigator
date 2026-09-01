'use client';

import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';

/**
 * Запись урока прямо в браузере через MediaRecorder.
 *
 * Что осознанно не делает: не шлёт запись сам и ничего не хранит между
 * визитами. Пишет в память вкладки и отдаёт готовый Blob наверх —
 * решение «отправлять или переписать» принимает человек.
 *
 * Микрофон в классе слышит и детей: об этом сказано на лендинге и в
 * согласии, а здесь — в подписи под кнопкой, до нажатия, а не после.
 */
type Props = { onГотово: (запись: Blob, длительность: number) => void; выключен?: boolean };

function времяСтрокой(секунд: number): string {
  const м = Math.floor(секунд / 60);
  const с = секунд % 60;
  return `${String(м).padStart(2, '0')}:${String(с).padStart(2, '0')}`;
}

export function Диктофон({ onГотово, выключен }: Props) {
  const [идёт, setИдёт] = useState(false);
  const [секунд, setСекунд] = useState(0);
  const [ошибка, setОшибка] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const куски = useRef<Blob[]>([]);

  useEffect(() => {
    if (!идёт) return;
    const таймер = setInterval(() => setСекунд((с) => с + 1), 1000);
    return () => clearInterval(таймер);
  }, [идёт]);

  // Вкладку закрыли или ушли со страницы — микрофон обязан погаснуть.
  useEffect(() => () => {
    recorder.current?.stream.getTracks().forEach((дорожка) => дорожка.stop());
  }, []);

  async function начать() {
    setОшибка(null);
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices) {
      setОшибка('Этот браузер не умеет записывать звук. Запишите урок диктофоном и пришлите файл.');
      return;
    }
    try {
      const поток = await navigator.mediaDevices.getUserMedia({ audio: true });
      куски.current = [];
      const запись = new MediaRecorder(поток);
      запись.ondataavailable = (событие) => {
        if (событие.data.size) куски.current.push(событие.data);
      };
      запись.onstop = () => {
        поток.getTracks().forEach((дорожка) => дорожка.stop());
        onГотово(new Blob(куски.current, { type: запись.mimeType || 'audio/webm' }), секунд);
      };
      recorder.current = запись;
      запись.start(1000);
      setСекунд(0);
      setИдёт(true);
    } catch {
      setОшибка('Браузер не дал доступ к микрофону. Разрешите запись звука в настройках сайта.');
    }
  }

  function закончить() {
    recorder.current?.stop();
    setИдёт(false);
  }

  return (
    <div>
      <div className="row" style={{ gap: 14 }}>
        {идёт ? (
          <>
            <div style={{ width: 34, height: 34, borderRadius: '50%', background: '#E1665C', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <i style={{ width: 12, height: 12, borderRadius: 3, background: '#fff' }} />
            </div>
            <div className="mono" style={{ fontSize: 22, fontWeight: 600 }}>{времяСтрокой(секунд)}</div>
            <Button variant="тихая" onClick={закончить} style={{ marginLeft: 'auto' }}>
              Остановить запись
            </Button>
          </>
        ) : (
          <Button icon="mic" onClick={начать} disabled={выключен}>Записать урок сейчас</Button>
        )}
      </div>
      <p className="muted" style={{ fontSize: 12.5, marginTop: 10, lineHeight: 1.55 }}>
        <Icon name="warn" size={13} style={{ display: 'inline-block', verticalAlign: -2, marginRight: 5 }} />
        Микрофон слышит и учеников. Запись уходит на расшифровку и удаляется сразу после неё —
        на дисках она не остаётся.
      </p>
      {ошибка ? <p style={{ color: 'var(--red)', fontSize: 13, marginTop: 8 }}>{ошибка}</p> : null}
    </div>
  );
}
