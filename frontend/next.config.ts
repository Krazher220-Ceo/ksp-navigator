import type { NextConfig } from 'next';

/**
 * Настройки Next.js кабинета.
 *
 * Осознанно пусто: адрес FastAPI приходит переменной окружения
 * NEXT_PUBLIC_API_URL и читается в коде, а не прошивается сюда, чтобы
 * сборка на Vercel и запуск против MacBook отличались только окружением.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
