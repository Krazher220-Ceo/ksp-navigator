'use client';

import { useState } from 'react';
import { Bar } from '@/components/Bar';
import { Button } from '@/components/Button';
import { Card, CardBody, CardHead } from '@/components/Card';
import { Chip } from '@/components/Chip';
import { Icon, ICONS, type IconName } from '@/components/Icon';
import { Input } from '@/components/Input';
import { StatTile } from '@/components/StatTile';
import { Switch } from '@/components/Switch';
import { TabBar } from '@/components/TabBar';
import { ТЕКСТЫ } from '@/content/texts.generated';

/**
 * Служебная витрина базовых элементов.
 *
 * Нужна затем, чтобы глазами сверять кнопки, карточки, чипы и иконки с
 * артбордами, не открывая ради этого весь кабинет. Пользователю она не
 * показывается и в меню не значится; числа и подписи здесь — образцы, а
 * не данные.
 */
export default function KitPage() {
  const [sound, setSound] = useState(true);
  const [oop, setOop] = useState(false);
  return (
    <div style={{ padding: 32, display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 980, margin: '0 auto' }}>
      <h1 style={{ fontFamily: 'var(--serif)', fontSize: 26 }}>Элементы кабинета</h1>

      <Card>
        <CardHead icon="doc" iconColor="var(--blue-700)" title="Кнопки" />
        <CardBody>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <Button icon="mic" arrow>Записать урок</Button>
            <Button variant="тихая" icon="doc">Собрать КСП</Button>
            <Button variant="строкой">Что случилось</Button>
            <Button size="малая">Собрать</Button>
            <Button size="малая" variant="тихая">Собрать</Button>
            <Button size="крупная" icon="mic" arrow>Записать первый урок</Button>
            <Button disabled>Недоступно</Button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHead icon="seal" iconColor="var(--green)" title="Чипы" aside={<Chip tone="золотой">черновик</Chip>} />
        <CardBody>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <Chip icon="clock">через 40 минут</Chip>
            <Chip tone="золотой">черновик</Chip>
            <Chip tone="зелёный" icon="seal">активен до 28.09</Chip>
            <Chip tone="красный" icon="warn">не собрался</Chip>
            <Chip tone="серый">без плана</Chip>
          </div>
        </CardBody>
      </Card>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}>
        <StatTile label="Черновиков КСП" value="3" hint="сегодня" icon="doc" iconColor="var(--blue-700)" />
        <StatTile label="Конспектов" value="1" hint="сегодня" icon="mic" iconColor="var(--sky)" />
        <StatTile label="Учеников" value="47" hint="в трёх классах" icon="users" iconColor="var(--green)" />
      </div>

      <Card>
        <CardHead icon="card" iconColor="var(--blue-700)" title="Полоски расхода" />
        <CardBody>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
            <Bar fill={0.6} label="Черновики КСП сегодня" value="3 / 5" />
            <Bar fill={0.5} tone="голубой" label="Конспекты из записи" value="1 / 2" />
            <Bar fill={0.52} label="Учеников в классах" value="47 / 90" />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHead icon="key" iconColor="var(--blue-700)" title="Поля и переключатели" />
        <CardBody>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Input id="kit-mail" label="Почта" icon="mail" type="email" autoComplete="email" placeholder="uchitel@school.kz" />
            <Input id="kit-pass" label="Пароль" icon="lock" type="password" autoComplete="current-password" placeholder="••••••••" />
            <Input id="kit-code" label="Код приглашения" icon="users" placeholder="KZ-4H7M" error={ТЕКСТЫ.STUDENT_JOIN_CODE_NOT_FOUND} />
            <Switch checked={sound} onChange={setSound} label="Тип урока: изучение нового" />
            <Switch checked={oop} onChange={setOop} label="Учесть особые образовательные потребности" />
            <Switch checked={false} onChange={() => {}} label="Оплата (пилот безлимитный, включать нечего)" disabled />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHead icon="grid" iconColor="var(--blue-700)" title="Иконки" aside={<span className="muted" style={{ fontSize: 12.5 }}>{Object.keys(ICONS).length} шт.</span>} />
        <CardBody>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(88px, 1fr))', gap: 14 }}>
            {(Object.keys(ICONS) as IconName[]).map((name) => (
              <div key={name} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, color: 'var(--ink-2)' }}>
                <Icon name={name} size={22} />
                <span className="mono muted" style={{ fontSize: 10.5 }}>{name}</span>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHead icon="phone" iconColor="var(--blue-700)" title="Нижняя панель телефона" />
        <div style={{ maxWidth: 380, margin: '0 auto', width: '100%' }}><TabBar /></div>
      </Card>
    </div>
  );
}
