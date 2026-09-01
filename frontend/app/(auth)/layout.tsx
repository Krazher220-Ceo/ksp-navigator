/**
 * Оболочка экранов входа: своя рама, без шапки сайта и без меню кабинета.
 * Человек здесь занят одним делом, и уводить его некуда.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
