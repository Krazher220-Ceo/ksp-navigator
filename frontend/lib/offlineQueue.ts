'use client';

import { отправитьЗапись } from './api';

/**
 * Очередь записей урока, снятых без сети (блок Ф12).
 *
 * Запись урока — единственное, что нельзя потерять: урок уже прошёл и
 * второй раз его не запишешь. Поэтому запись, которую не удалось
 * отправить, ложится в IndexedDB и уходит, как только появится сеть.
 *
 * Почему IndexedDB, а не Background Sync: Background Sync не
 * поддерживает Safari, а половина педагогов — на iPhone. Отправка по
 * событию online работает везде и не требует service worker вовсе.
 *
 * Что осознанно не хранится: ничего, кроме самой записи и режима
 * обработки. Ни расшифровок, ни конспектов — они живут на сервере, и
 * складывать их в браузер незачем.
 */
const БАЗА = 'mazmun-offline';
const ХРАНИЛИЩЕ = 'zapisi';
const ВЕРСИЯ = 1;

export type ОтложеннаяЗапись = {
  id?: number;
  файл: Blob;
  имя: string;
  режим: 'student' | 'teacher';
  когда: number;
};

function открыть(): Promise<IDBDatabase> {
  return new Promise((готово, отказ) => {
    const запрос = indexedDB.open(БАЗА, ВЕРСИЯ);
    запрос.onupgradeneeded = () => {
      const бд = запрос.result;
      if (!бд.objectStoreNames.contains(ХРАНИЛИЩЕ)) {
        бд.createObjectStore(ХРАНИЛИЩЕ, { keyPath: 'id', autoIncrement: true });
      }
    };
    запрос.onsuccess = () => готово(запрос.result);
    запрос.onerror = () => отказ(запрос.error);
  });
}

function действие<T>(режим: IDBTransactionMode, работа: (хранилище: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return открыть().then((бд) => new Promise<T>((готово, отказ) => {
    const транзакция = бд.transaction(ХРАНИЛИЩЕ, режим);
    const запрос = работа(транзакция.objectStore(ХРАНИЛИЩЕ));
    запрос.onsuccess = () => готово(запрос.result);
    запрос.onerror = () => отказ(запрос.error);
  }));
}

export async function отложить(запись: Omit<ОтложеннаяЗапись, 'id'>): Promise<void> {
  await действие('readwrite', (хранилище) => хранилище.add(запись));
}

export async function отложенные(): Promise<ОтложеннаяЗапись[]> {
  return действие<ОтложеннаяЗапись[]>('readonly', (хранилище) => хранилище.getAll());
}

export async function забыть(id: number): Promise<void> {
  await действие('readwrite', (хранилище) => хранилище.delete(id));
}

/**
 * Пытается отправить всё отложенное. Возвращает, сколько ушло.
 *
 * Неудача не считается ошибкой: запись остаётся в очереди и попробует
 * уйти в следующий раз. Потерять её нельзя, а торопить — некуда.
 */
export async function отправитьОтложенное(): Promise<number> {
  if (typeof indexedDB === 'undefined') return 0;
  let ушло = 0;
  for (const запись of await отложенные()) {
    try {
      await отправитьЗапись(запись.файл, запись.имя, запись.режим);
      if (запись.id !== undefined) await забыть(запись.id);
      ушло += 1;
    } catch {
      // Сети всё ещё нет либо сервер отказал — пробуем в следующий раз.
      break;
    }
  }
  return ушло;
}
