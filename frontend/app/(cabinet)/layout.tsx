import { Sidebar } from '@/components/Sidebar';

/**
 * Оболочка кабинета: тёмная рама, боковое меню и вложенное светлое ядро.
 *
 * Имя, инициалы и тариф пока заданы здесь строками из макета: настоящий
 * профиль приходит вместе с аутентификацией (блок Ф3) и дэшбордом
 * (блок Ф5). Выдумывать по ним никакой логики не нужно — это подпись под
 * аватаром и ничего больше.
 */
export default function CabinetLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="app">
      <Sidebar userName="Айгүл Тлеубаева" userInitials="АТ" userTariff="Тариф «Учитель»" />
      <div className="main">{children}</div>
    </div>
  );
}
