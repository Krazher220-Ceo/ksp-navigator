import type { Metadata, Viewport } from 'next';
import { Golos_Text, JetBrains_Mono, Literata } from 'next/font/google';
import { РегистрацияSW } from '@/components/pwa/РегистрацияSW';
import './globals.css';

/**
 * Корневая раскладка кабинета Mazmun.
 *
 * Шрифты те же три, что в макетах, и все с кириллицей: Literata —
 * заголовки, Golos Text — интерфейс, JetBrains Mono — коды целей и суммы.
 * Раздаёт их next/font со своего домена, поэтому имя семейства
 * генерируется, а в токенах стоит переменная. Хвост каждой стопки в
 * app/tokens.css оставлен как в макете — на случай, если шрифт не приедет.
 */
const golos = Golos_Text({ subsets: ['cyrillic', 'latin'], weight: ['400', '500', '600', '700', '900'], variable: '--font-golos', display: 'swap' });
// Literata берётся переменной начертанием: ось opsz в макетах задана, а
// next/font разрешает оси только у переменного шрифта. Нужные 400, 600 и
// 700 в диапазон входят.
const literata = Literata({ subsets: ['cyrillic', 'latin'], axes: ['opsz'], variable: '--font-literata', display: 'swap' });
const jetbrains = JetBrains_Mono({ subsets: ['cyrillic', 'latin'], weight: ['400', '500', '600'], variable: '--font-jetbrains', display: 'swap' });

export const metadata: Metadata = {
  title: 'Mazmun',
  description: 'Конспект урока из записи и черновик КСП по форме приказа МОН РК №130.',
  manifest: '/manifest.webmanifest',
  // Иконка на домашнем экране iPhone берётся отсюда: манифест Safari
  // читает не полностью.
  appleWebApp: { capable: true, title: 'Mazmun', statusBarStyle: 'black-translucent' },
  icons: { icon: '/icon-192.png', apple: '/icon-192.png' },
};

export const viewport: Viewport = {
  themeColor: '#0C2B49',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${golos.variable} ${literata.variable} ${jetbrains.variable}`}>
      <body>
        <РегистрацияSW />
        {children}
      </body>
    </html>
  );
}
