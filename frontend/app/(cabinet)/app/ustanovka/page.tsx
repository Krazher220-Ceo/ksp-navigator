'use client';

import Image from 'next/image';
import { Card, CardBody } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon, type IconName } from '@/components/Icon';
import { TopBar } from '@/components/TopBar';
import { Установка } from '@/components/pwa/Установка';

/**
 * «Установить на телефон» по артборду MobUstanovka.
 *
 * Инструкции для iPhone и Android разные и стоят обе: на iPhone
 * beforeinstallprompt не приходит вовсе, и кнопки там не будет никогда —
 * значит, надо показать, куда нажать, а не делать вид, что кнопка вот-вот
 * появится.
 */
const ЧТО_ДАЁТ: { знак: IconName; заголовок: string; текст: string }[] = [
  {
    знак: 'wifi',
    заголовок: 'Работает без интернета',
    текст: 'Скачанные документы открываются офлайн. Запись урока уйдёт на расшифровку, когда появится сеть — она сохраняется в браузере и не теряется.',
  },
  {
    знак: 'mic',
    заголовок: 'Запись в один тап с домашнего экрана',
    текст: 'Долгое нажатие на иконку — «Записать урок».',
  },
  {
    знак: 'cloud',
    заголовок: 'Данные общие с ботом и кабинетом',
    текст: 'Где начали — там и продолжите: один аккаунт, одни документы.',
  },
];

export default function UstanovkaPage() {
  return (
    <>
      <TopBar date="Аккаунт" title="Установить на телефон" searchPlaceholder="Тема, класс или код цели" />

      <div className="body" style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 620, overflowY: 'auto' }}>
        <Card>
          <CardBody style={{ display: 'flex', alignItems: 'center', gap: 15 }}>
            <Image className="mark" src="/icon-192.png" width={62} height={62} alt="Mazmun" />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--serif)', fontSize: 17, fontWeight: 700 }}>Mazmun</div>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                Иконка на домашнем экране. Магазин приложений не нужен.
              </div>
            </div>
          </CardBody>
        </Card>

        <Установка />

        <div className="lbl" style={{ marginTop: 4 }}>Что это даёт</div>
        <Card>
          {ЧТО_ДАЁТ.map((пункт, i) => (
            <div key={пункт.заголовок}>
              {i > 0 ? <div className="sep" /> : null}
              <div className="row" style={{ padding: '11px 14px', gap: 12, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--green)', flex: 'none', paddingTop: 1 }}><Icon name={пункт.знак} size={19} /></span>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{пункт.заголовок}</div>
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 1, lineHeight: 1.55 }}>{пункт.текст}</div>
                </div>
              </div>
            </div>
          ))}
        </Card>

        <Card>
          <CardBody>
            <div className="lbl">Если кнопки нет</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 11, marginTop: 10 }}>
              <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
                <Chip tone="серый" style={{ flex: 'none' }}>iPhone</Chip>
                <span className="muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
                  Safari → кнопка «Поделиться» внизу → «На экран «Домой»». Кнопки установки на
                  iPhone не бывает: так устроен сам Safari.
                </span>
              </div>
              <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
                <Chip tone="серый" style={{ flex: 'none' }}>Android</Chip>
                <span className="muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
                  Chrome → меню (три точки) → «Установить приложение» или «Добавить на главный экран».
                </span>
              </div>
            </div>
          </CardBody>
        </Card>

        <Card style={{ background: 'var(--blue-soft)' }}>
          <CardBody style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
            <span style={{ color: 'var(--blue-700)', flex: 'none', paddingTop: 1 }}><Icon name="shield" size={17} /></span>
            <p style={{ fontSize: 12.5, lineHeight: 1.55 }}>
              Офлайн хранятся только скачанные документы. Расшифровки уроков и фотографии тетрадей
              в браузере не сохраняются — им там не место.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
