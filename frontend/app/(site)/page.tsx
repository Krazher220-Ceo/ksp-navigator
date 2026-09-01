import type { Metadata } from 'next';
import { Audiences } from '@/components/landing/Audiences';
import { CallToAction } from '@/components/landing/CallToAction';
import { DataHonesty } from '@/components/landing/DataHonesty';
import { Hero } from '@/components/landing/Hero';
import { HowItWorks } from '@/components/landing/HowItWorks';
import { Problem } from '@/components/landing/Problem';
import { Review } from '@/components/landing/Review';
import { SiteFooter } from '@/components/landing/SiteFooter';
import { SiteNav } from '@/components/landing/SiteNav';
import { Stats } from '@/components/landing/Stats';
import { Tariffs } from '@/components/landing/Tariffs';

export const metadata: Metadata = {
  title: 'Mazmun — конспект урока и КСП по форме №130',
  description:
    'Запись урока превращается в конспект и черновик КСП по форме приказа МОН РК №130. '
    + 'Для учителей Казахстана: первая запись урока бесплатно.',
};

/**
 * Лендинг по макету design/Lending.dc.html.
 *
 * Порядок секций из макета не переставлен: результат целиком, числа,
 * проблема, три шага, две аудитории, честно о данных вместе с блоком
 * про голоса детей, тарифы, место под отзыв, призыв.
 */
export default function LandingPage() {
  return (
    <>
      <SiteNav />
      <main>
        <Hero />
        <Stats />
        <Problem />
        <HowItWorks />
        <Audiences />
        <DataHonesty />
        <Tariffs />
        <Review />
        <CallToAction />
      </main>
      <SiteFooter />
    </>
  );
}
