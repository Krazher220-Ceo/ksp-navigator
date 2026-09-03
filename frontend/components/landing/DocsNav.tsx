import Image from 'next/image';
import Link from 'next/link';
import { Button } from '@/components/Button';
import { Icon } from '@/components/Icon';

/**
 * Шапка документации — своя, как в макете: ниже лендинговой, с
 * хлебной крошкой и полем поиска вместо разделов и тарифов.
 *
 * Поиск пока только выглядит полем: искать будет сервер, и это отдельная
 * работа. Рисовать его как рабочий и молчать было бы враньём в мелочи,
 * поэтому у него подпись-плейсхолдер и он не принимает ввод.
 */
export function DocsNav() {
  return (
    <nav style={{
      height: 64, borderBottom: '1px solid var(--line)', display: 'flex',
      alignItems: 'center', gap: 12, padding: '0 var(--pad-x)',
      position: 'sticky', top: 0, zIndex: 20, background: 'rgba(255,255,255,.9)', backdropFilter: 'blur(14px)',
    }}>
      <div className="row" style={{ gap: 10 }}>
        <Link className="row" style={{ gap: 10 }} href="/">
          <Image className="mark" src="/mazmun-logo.png" width={30} height={30} alt="Mazmun" priority />
          <span className="brand-name" style={{ color: 'var(--ink)', fontSize: 14.5 }}>Mazmun</span>
        </Link>
        <span style={{ color: 'var(--line)', fontSize: 18 }}>/</span>
        <span style={{ fontSize: 14.5, color: 'var(--ink-2)' }}>Документация</span>
      </div>
      <div className="search docs-search" style={{ marginLeft: 'auto', width: 260 }}>
        <Icon name="search" size={15} />
        <span>Поиск по документации</span>
      </div>
      <Link href="/" className="docs-back"><Button variant="тихая" size="малая">Вернуться на сайт</Button></Link>
    </nav>
  );
}
