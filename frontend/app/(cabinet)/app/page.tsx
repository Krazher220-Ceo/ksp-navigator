import { Button } from '@/components/Button';
import { TopBar } from '@/components/TopBar';

/**
 * Дэшборд педагога — пока только каркас.
 *
 * Шапка и боковое меню повторяют артборд, тело оставлено пустым
 * намеренно: бенто-сетка наполняется данными из core.dashboard в блоке
 * Ф5. Раскладывать здесь придуманные карточки нельзя — числа в кабинете
 * и в боте обязаны совпадать, а считает их один и тот же модуль.
 */
export default function DashboardPage() {
  return (
    <>
      <TopBar
        date="Понедельник, 1 сентября"
        title="Доброе утро, Айгүл"
        searchPlaceholder="Тема, класс или код цели"
        hasAlerts
        action={<Button icon="mic" arrow>Записать урок</Button>}
      />
      <div className="body" />
    </>
  );
}
