"""
core/llm_client.py — единый клиент к LLM с цепочкой провайдеров и фоллбэком.

Зачем модуль: весь остальной код зовёт LLM через один метод и не знает,
какой именно провайдер в итоге ответил. Порядок провайдеров и их ключи
берутся из .env (core.config.settings, переменная LLM_PROVIDERS). Если
у первого провайдера кончился лимит, легла сеть или истёк платный баланс,
клиент сам переходит к следующему в списке, а не роняет вызывающий код.

Порядок по умолчанию: deepseek (платный, основной) → gemini → grok →
openai → anthropic (резервные, на бесплатных дневных лимитах). Список
расширяемый — новый провайдер не требует правки этого файла, если его
протокол уже есть в PROVIDER_KIND (большинство новых LLM API совместимы
с форматом OpenAI); принципиально новый протокол — новая пара функций
в _ADAPTERS.

Что осознанно не делает: не выбирает провайдера "по цене" или "по
качеству ответа" — порядок фиксирован человеком в .env, не эвристикой.
Не кеширует ответы. Не проверяет соответствие JSON переданной schema —
только синтаксическую валидность; проверку бизнес-полей делает вызывающий
код (например core/ksp_generator.py в блоке Б6).

На что опирается: только httpx, без вендорских SDK — так тесты мокают
транспорт одним и тем же способом для всех провайдеров (httpx.MockTransport),
и добавление нового OpenAI-совместимого провайдера не тянет новую
зависимость в requirements.txt.
"""

import asyncio
import base64
import functools
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Базовое исключение клиента LLM."""


class LLMConfigError(LLMError):
    """Ошибка конфигурации провайдера: например, задан ключ без модели,
    или в LLM_PROVIDERS указано имя, для которого нет адаптера протокола."""


class LLMUnavailable(LLMError):
    """Все провайдеры в цепочке отказали (или ни один не настроен)."""


class LLMBadResponse(LLMError):
    """Провайдер дважды подряд вернул невалидный JSON."""


class _ProviderFailed(LLMError):
    """Внутренний сигнал: конкретный провайдер не смог ответить после
    своих ретраев. Наружу из complete_json не выходит — клиент ловит
    его сам и переходит к следующему провайдеру в цепочке."""


JSON_REPAIR_SUFFIX = (
    "\n\nПредыдущий ответ не распарсился как JSON. "
    "Верни строго JSON без пояснений и без markdown-обёртки."
)

# Дефолты на случай, если {ИМЯ}_BASE_URL не задан в .env. Уточняй по
# актуальной документации провайдера — это отправная точка, не гарантия.
DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "grok": "https://api.x.ai/v1",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com",
}

# Имя провайдера -> протокол запроса/ответа (см. _ADAPTERS).
PROVIDER_KIND = {
    "deepseek": "openai_compatible",
    "grok": "openai_compatible",
    "openai": "openai_compatible",
    "gemini": "gemini",
    "anthropic": "anthropic",
}

ANTHROPIC_API_VERSION = "2023-06-01"
# 4096 — безопасный минимум, который поддерживают все модели Claude 3 без
# доп. заголовков; поднимать выше нельзя вслепую, не зная конкретную модель
# в ANTHROPIC_MODEL (у части моделей семейства это потолок, у части — можно
# больше). ПРОВЕРИТЬ, когда Anthropic реально станет активным провайдером
# в цепочке (сейчас ANTHROPIC_API_KEY пуст, ветка не используется): после
# блока Р2 (PLAN_STAGE1_EXT.md) ответ модели стал заметно длиннее — полные
# реплики педагога и развёрнутые дескрипторы вместо однословных заглушек,
# у DeepSeek суммарно (промпт+ответ) уходило до ~5600 токенов на реальных
# замерах. Если Anthropic окажется единственным живым провайдером на
# длинном уроке, 4096 на сам ответ может не хватить — раньше это было
# маловероятно, теперь стоит присматривать.
ANTHROPIC_MAX_TOKENS = 4096


def _is_retryable_status(status_code: int) -> bool:
    """429 (лимит/квота) и весь диапазон 5xx — стоит повторить.
    Всё остальное (400, 401, ...) — повтор бессмысленен, падаем сразу."""
    return status_code == 429 or 500 <= status_code < 600


# --- адаптеры запроса/ответа по протоколу провайдера ---
# Каждый адаптер: build(base_url, api_key, model, system, user) -> (url, headers, json_body)
#                 parse(response_json) -> (текст_ответа, число_токенов_или_None)


def _build_openai_compatible(base_url: str, api_key: str, model: str, system: str, user: str):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    return url, headers, body


def _parse_openai_compatible(data: dict) -> tuple[str, int | None]:
    text = data["choices"][0]["message"]["content"]
    tokens = (data.get("usage") or {}).get("total_tokens")
    return text, tokens


def _build_gemini(base_url: str, api_key: str, model: str, system: str, user: str):
    # М0: ключ передаётся заголовком x-goog-api-key, а не query-параметром
    # ?key= — httpx на уровне INFO логирует полный URL запроса, и ключ в
    # query-строке утекал в logs/app.log при любом вызове без явной
    # глушилки логгера httpx (обнаружено вживую 26.08.2026).
    url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    return url, headers, body


def _parse_gemini(data: dict) -> tuple[str, int | None]:
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    tokens = (data.get("usageMetadata") or {}).get("totalTokenCount")
    return text, tokens


def _build_anthropic(base_url: str, api_key: str, model: str, system: str, user: str):
    url = f"{base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    return url, headers, body


def _parse_anthropic(data: dict) -> tuple[str, int | None]:
    text = data["content"][0]["text"]
    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    tokens = None if input_tokens is None and output_tokens is None else (input_tokens or 0) + (output_tokens or 0)
    return text, tokens


_ADAPTERS: dict[str, tuple[Callable, Callable]] = {
    "openai_compatible": (_build_openai_compatible, _parse_openai_compatible),
    "gemini": (_build_gemini, _parse_gemini),
    "anthropic": (_build_anthropic, _parse_anthropic),
}


# --- Р6.2: адаптеры запроса с изображением ---
#
# Только сборка запроса другая (текст промпта + картинка вместо только
# текста) — разбор ответа тот же самый _parse_openai_compatible/_parse_gemini
# (ответ приходит в том же поле, vision тут влияет только на то, что
# модель "видела" при генерации текста, не на форму самого ответа).
#
# Ловушка плана (Р6.2): никакого tesseract, никаких локальных ML-моделей —
# только уже подключённые провайдеры по HTTP через httpx, тем же клиентом,
# что и текстовые запросы.


def _build_openai_compatible_vision(
    base_url: str, api_key: str, model: str, system: str, user: str, image_base64: str, image_mime: str
):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_base64}"}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
    }
    return url, headers, body


def _build_gemini_vision(
    base_url: str, api_key: str, model: str, system: str, user: str, image_base64: str, image_mime: str
):
    # М0: ключ заголовком, не в URL — та же причина, что в _build_gemini.
    url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": user},
                    {"inline_data": {"mime_type": image_mime, "data": image_base64}},
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    return url, headers, body


# Ключ — provider.kind (тот же протокол, что и в _ADAPTERS), значение —
# только build-функция с изображением; parse переиспускается из _ADAPTERS.
VISION_ADAPTERS: dict[str, Callable] = {
    "openai_compatible": _build_openai_compatible_vision,
    "gemini": _build_gemini_vision,
}

# Из провайдеров нашей цепочки эти умеют vision (план Р6.2, дословно:
# "Gemini и OpenAI в нашей цепочке это умеют"). deepseek/grok/anthropic —
# не проверялись, не включены; провайдер без vision пропускается молча,
# тем же способом, что провайдер без API-ключа (см. complete_json_with_image).
VISION_CAPABLE_PROVIDERS = {"gemini", "openai"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str
    api_key: str
    model: str
    base_url: str


def _load_providers_from_settings() -> list[ProviderConfig]:
    """Строит цепочку провайдеров из core.config.settings, в порядке
    LLM_PROVIDERS. Провайдер без api_key пропускается молча — это
    штатная ситуация (например, у Gemini ещё не завели бесплатный ключ).
    """
    providers: list[ProviderConfig] = []
    for name in settings.llm_provider_order:
        raw = settings.llm_providers.get(name, {})
        api_key = raw.get("api_key")
        if not api_key:
            continue

        model = raw.get("model")
        if not model:
            raise LLMConfigError(
                f"для провайдера '{name}' задан API-ключ, но не задана модель "
                f"({name.upper()}_MODEL в .env)"
            )

        kind = PROVIDER_KIND.get(name)
        if kind is None:
            raise LLMConfigError(
                f"неизвестный провайдер '{name}' в LLM_PROVIDERS — для него нет "
                f"адаптера протокола в core/llm_client.py (PROVIDER_KIND)"
            )

        base_url = raw.get("base_url") or DEFAULT_BASE_URLS[name]
        providers.append(
            ProviderConfig(name=name, kind=kind, api_key=api_key, model=model, base_url=base_url)
        )
    return providers


def _strip_markdown_fence(text: str) -> str:
    """Убирает обёртку ```json ... ``` / ``` ... ```, если модель её добавила.

    Содержимое внутри не трогает: сломанный JSON (лишняя запятая,
    незакрытая кавычка и т.п.) здесь не чинится — за это отвечает
    единственный повторный запрос к модели (см. _call_with_json_guarantee),
    а не регулярки и не ручная правка текста."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _try_parse_json(text: str) -> dict | None:
    try:
        return json.loads(_strip_markdown_fence(text))
    except json.JSONDecodeError:
        return None


class LLMClient:
    """Клиент LLM с цепочкой провайдеров. Два публичных метода:
    complete_json (текст) и complete_json_with_image (Р6.2, с фото —
    только провайдеры из VISION_CAPABLE_PROVIDERS). Провайдеры и
    http-клиент можно передать явно (нужно для тестов — httpx мокается
    через transport=httpx.MockTransport)."""

    def __init__(
        self,
        providers: list[ProviderConfig] | None = None,
        http_client: httpx.AsyncClient | None = None,
        request_timeout: float = 60.0,
    ) -> None:
        self._providers = providers if providers is not None else _load_providers_from_settings()
        self._http_client = http_client or httpx.AsyncClient(timeout=request_timeout)
        self._owns_http_client = http_client is None

    async def aclose(self) -> None:
        """Закрывает http-клиент, если его создал сам LLMClient (а не
        если он был передан явно — тогда за его жизненный цикл отвечает
        вызывающий код, обычно тест)."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: dict,
        max_retries: int = 3,
    ) -> dict:
        """Возвращает распарсенный JSON-ответ модели, пробуя провайдеров
        по очереди. Схема встраивается в промпт текстом — это инструкция
        модели, не механизм валидации: соответствие бизнес-полям схемы
        проверяет вызывающий код."""
        if max_retries < 1:
            raise LLMConfigError(f"max_retries должен быть >= 1, получено {max_retries}")

        if not self._providers:
            raise LLMUnavailable(
                "не настроен ни один провайдер LLM — проверьте LLM_PROVIDERS "
                "и {ИМЯ}_API_KEY в .env"
            )

        prompt = self._with_schema(user, schema)
        last_error: Exception | None = None

        for provider in self._providers:
            try:
                return await self._call_with_json_guarantee(provider, system, prompt, max_retries)
            except _ProviderFailed as exc:
                logger.warning("провайдер %s отказал, перехожу к следующему: %s", provider.name, exc)
                last_error = exc
                continue

        raise LLMUnavailable(f"все провайдеры LLM отказали: {last_error}")

    async def complete_json_with_image(
        self,
        system: str,
        user: str,
        schema: dict,
        image_bytes: bytes,
        image_mime: str,
        max_retries: int = 3,
    ) -> dict:
        """Р6.2: то же, что complete_json, но запрос дополнен изображением.
        Пробует ТОЛЬКО провайдеров с поддержкой vision (VISION_CAPABLE_PROVIDERS),
        в том же относительном порядке, что и общая цепочка LLM_PROVIDERS —
        провайдер без vision пропускается молча, тем же способом, что и
        провайдер без API-ключа в complete_json."""
        if max_retries < 1:
            raise LLMConfigError(f"max_retries должен быть >= 1, получено {max_retries}")

        vision_providers = [p for p in self._providers if p.name in VISION_CAPABLE_PROVIDERS]
        if not vision_providers:
            raise LLMUnavailable(
                "ни один провайдер с поддержкой изображений не настроен — "
                "нужен действующий ключ GEMINI_API_KEY или OPENAI_API_KEY в .env"
            )

        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = self._with_schema(user, schema)
        last_error: Exception | None = None

        for provider in vision_providers:
            vision_build = VISION_ADAPTERS.get(provider.kind)
            if vision_build is None:
                # Защитная ветка: провайдер попал в VISION_CAPABLE_PROVIDERS,
                # но для его протокола нет vision-адаптера. Не должно
                # случаться при текущем составе списка (gemini, openai —
                # оба openai_compatible/gemini уже есть в VISION_ADAPTERS),
                # но не роняем всю цепочку из-за одной несостыковки конфигурации.
                logger.warning(
                    "провайдер %s заявлен как vision-способный, но адаптера для '%s' нет — пропускаю",
                    provider.name, provider.kind,
                )
                continue

            build_override = functools.partial(
                vision_build, image_base64=image_base64, image_mime=image_mime
            )
            try:
                return await self._call_with_json_guarantee(
                    provider, system, prompt, max_retries, build_override=build_override
                )
            except _ProviderFailed as exc:
                logger.warning(
                    "провайдер %s (vision) отказал, перехожу к следующему: %s", provider.name, exc
                )
                last_error = exc
                continue

        raise LLMUnavailable(f"все провайдеры с поддержкой изображений отказали: {last_error}")

    @staticmethod
    def _with_schema(user: str, schema: dict) -> str:
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        return (
            f"{user}\n\n"
            f"Ответ должен быть строго JSON, соответствующим следующей схеме:\n"
            f"{schema_text}"
        )

    async def _call_with_json_guarantee(
        self,
        provider: ProviderConfig,
        system: str,
        user: str,
        max_retries: int,
        build_override: Callable | None = None,
    ) -> dict:
        """Один вызов провайдера (с его собственными ретраями на 429/5xx),
        и при невалидном JSON — ровно один повторный запрос тому же
        провайдеру с явной просьбой вернуть строгий JSON (задача Б2.2).

        build_override — Р6.2: для запроса с изображением используется
        vision-сборщик тела запроса вместо текстового из _ADAPTERS; при
        повторном запросе (ниже) картинка тоже переотправляется — модели
        нужно снова её увидеть, чтобы разобрать текст на ней заново."""
        raw_text = await self._call_provider_with_retries(
            provider, system, user, max_retries, build_override=build_override
        )
        parsed = _try_parse_json(raw_text)
        if parsed is not None:
            return parsed

        logger.warning("провайдер %s вернул невалидный JSON, повторяю запрос один раз", provider.name)
        repaired_user = user + JSON_REPAIR_SUFFIX
        raw_text_retry = await self._call_provider_with_retries(
            provider, system, repaired_user, max_retries, build_override=build_override
        )
        parsed = _try_parse_json(raw_text_retry)
        if parsed is not None:
            return parsed

        raise LLMBadResponse(f"провайдер {provider.name} дважды подряд вернул невалидный JSON")

    async def _call_provider_with_retries(
        self,
        provider: ProviderConfig,
        system: str,
        user: str,
        max_retries: int,
        build_override: Callable | None = None,
    ) -> str:
        """HTTP-вызов одного провайдера с ретраями на 429/5xx (растущая
        пауза с джиттером) и немедленным отказом на всём остальном
        (400/401 и т.п. — повтор бессмысленен)."""
        _, parse = _ADAPTERS[provider.kind]
        build = build_override or _ADAPTERS[provider.kind][0]
        url, headers, body = build(provider.base_url, provider.api_key, provider.model, system, user)

        for attempt in range(1, max_retries + 1):
            started = time.monotonic()
            try:
                response = await self._http_client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                duration_ms = (time.monotonic() - started) * 1000
                is_last = attempt == max_retries
                logger.warning(
                    "провайдер=%s модель=%s попытка=%d/%d длительность=%.0fмс исход=%s: %s",
                    provider.name, provider.model, attempt, max_retries, duration_ms,
                    "отказ" if is_last else "ретрай_сеть", exc,
                )
                if is_last:
                    raise _ProviderFailed(
                        f"{provider.name}: сетевая ошибка после {attempt} попыток: {exc}"
                    ) from exc
                await self._sleep_backoff(attempt)
                continue

            duration_ms = (time.monotonic() - started) * 1000

            if response.status_code == 200:
                # 200 ещё не значит, что тело — то, чего мы ждём. Провайдер
                # может вернуть успех с телом другой формы: у Gemini это
                # штатный ответ с finishReason=SAFETY и без parts, у
                # OpenAI-совместимых — пустой choices, у любого — HTML
                # страницы прокси вместо JSON. Адаптеры разбирают тело
                # прямым индексированием, поэтому такой ответ давал
                # KeyError/IndexError, а он НЕ httpx.HTTPError: исключение
                # улетало мимо _ProviderFailed прямо наружу из
                # complete_json, и остальные провайдеры в цепочке даже не
                # пробовались — ровно тот отказ, ради защиты от которого
                # весь фоллбэк и написан. Теперь это обычный отказ
                # провайдера: переходим к следующему.
                try:
                    data = response.json()
                    text, tokens = parse(data)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    logger.warning(
                        "провайдер=%s модель=%s попытка=%d/%d длительность=%.0fмс "
                        "исход=неожиданный_формат_ответа: %s",
                        provider.name, provider.model, attempt, max_retries, duration_ms, exc,
                    )
                    raise _ProviderFailed(
                        f"{provider.name}: ответ 200, но тело не разобралось "
                        f"({type(exc).__name__}: {exc})"
                    ) from exc

                logger.info(
                    "провайдер=%s модель=%s попытка=%d/%d токены=%s длительность=%.0fмс исход=успех",
                    provider.name, provider.model, attempt, max_retries, tokens, duration_ms,
                )
                return text

            retryable = _is_retryable_status(response.status_code)
            is_last = (not retryable) or attempt == max_retries
            logger.warning(
                "провайдер=%s модель=%s попытка=%d/%d статус=%d длительность=%.0fмс исход=%s",
                provider.name, provider.model, attempt, max_retries, response.status_code, duration_ms,
                "отказ" if is_last else "ретрай",
            )
            if is_last:
                raise _ProviderFailed(
                    f"{provider.name}: HTTP {response.status_code} после {attempt} попыток: "
                    f"{response.text[:200]}"
                )
            await self._sleep_backoff(attempt)

        # Достижимо только при некорректном вызове (max_retries <= 0), что
        # complete_json уже отсекает выше, — оставлено как честная защита.
        raise LLMConfigError(f"{provider.name}: max_retries должен быть >= 1, получено {max_retries}")

    @staticmethod
    async def _sleep_backoff(attempt: int) -> None:
        """Растущая пауза перед следующей попыткой: ~1с перед 2-й попыткой,
        ~2с перед 3-й, ~4с перед 4-й и так далее, плюс небольшой джиттер
        (чтобы несколько параллельных задач не били по API одновременно)."""
        base_delay = 2 ** (attempt - 1)
        jitter = random.uniform(0, base_delay * 0.25)
        await asyncio.sleep(base_delay + jitter)
