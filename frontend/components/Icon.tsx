import type { CSSProperties } from 'react';
import {
  ArrowDown, ArrowRight, ArrowsClockwise, ArrowUp, Bell, BookOpen, CalendarBlank, Camera,
  CaretDown, CaretLeft, CaretRight, ChartLineUp, Check, Clock, CloudCheck, CreditCard,
  CrownSimple, DeviceMobile, DownloadSimple, EnvelopeSimple, Eye, File, FileText, GearSix,
  GraduationCap, House, Key, Laptop, Lightning, LockSimple, MagnifyingGlass, MapPin,
  Microphone, PaperPlaneTilt, Play, Plus, Quotes, SealCheck, ShieldCheck, SignOut, Sparkle,
  SquaresFour, StackSimple, UploadSimple, UserPlus, UsersThree, Warning, Waveform, WifiHigh, X,
} from '@phosphor-icons/react/dist/ssr';

/**
 * Иконки кабинета — Phosphor Light, ровно тот набор, что в макетах.
 *
 * Короткие имена ниже — те же ключи, что в design/src/_icons.json, чтобы
 * вёрстку артборда можно было переносить не переименовывая. Совпадение
 * контуров сторожит scripts/check-icons.mjs: подменили иконку или вес —
 * прогон краснеет. Новую иконку добавлять сюда и в макеты одновременно,
 * иначе кабинет и артборды разойдутся.
 *
 * Чего модуль не делает: не подбирает иконку по смыслу и не знает про
 * другие веса Phosphor — в проекте есть только light.
 */
export const ICONS = {
  home: House,
  mic: Microphone,
  doc: FileText,
  cal: CalendarBlank,
  chart: ChartLineUp,
  users: UsersThree,
  gear: GearSix,
  bell: Bell,
  search: MagnifyingGlass,
  plus: Plus,
  chev: CaretRight,
  chevl: CaretLeft,
  chevd: CaretDown,
  check: Check,
  clock: Clock,
  up: ArrowUp,
  down: ArrowDown,
  play: Play,
  lock: LockSimple,
  shield: ShieldCheck,
  book: BookOpen,
  cam: Camera,
  layers: StackSimple,
  grid: SquaresFour,
  out: SignOut,
  arrow: ArrowRight,
  file: File,
  wave: Waveform,
  key: Key,
  warn: Warning,
  cloud: CloudCheck,
  wifi: WifiHigh,
  pin: MapPin,
  quote: Quotes,
  refresh: ArrowsClockwise,
  send: PaperPlaneTilt,
  eye: Eye,
  spark: Sparkle,
  crown: CrownSimple,
  bolt: Lightning,
  userplus: UserPlus,
  mail: EnvelopeSimple,
  phone: DeviceMobile,
  laptop: Laptop,
  card: CreditCard,
  seal: SealCheck,
  cap: GraduationCap,
  dl: DownloadSimple,
  ul: UploadSimple,
  x: X,
} as const;

export type IconName = keyof typeof ICONS;

type Props = {
  name: IconName;
  /** Размер в пикселях. В макетах он всегда проставлен явно — так же и здесь. */
  size: number;
  className?: string;
  style?: CSSProperties;
};

export function Icon({ name, size, className, style }: Props) {
  const Glyph = ICONS[name];
  // aria-hidden: иконки в кабинете сопровождают текст, а не заменяют его.
  return <Glyph weight="light" size={size} aria-hidden className={className} style={{ flex: 'none', ...style }} />;
}
