"""Работа с api.exchangerate.host: endpoint /convert и access_key.

Используется @current_api.py для получения ключа и проверки доступа к API
(``get_current_rate``). Собственные запросы идут строго на
http://api.exchangerate.host/convert с параметром access_key.

Дополнительно:
    - кэш курса по паре валют (TTL) — снижает число запросов;
    - обработка HTTP 429 (rate-limit) с повтором по Retry-After;
    - минимальный интервал между запросами.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import requests
from dotenv import load_dotenv

# Импорт из @current_api.py — там хранится логика доступа к API
load_dotenv()

from current_api import get_current_rate  # noqa: E402

log = logging.getLogger("exchange")

# Конфигурация: access_key для API и токен бота (вторая переменная).
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

CONVERT_ENDPOINT = "http://api.exchangerate.host/convert"

# Настройки защиты от rate-limit (429).
MIN_REQUEST_INTERVAL = float(os.getenv("EXCHANGE_MIN_INTERVAL", "1.1"))  # сек
RATE_TTL = float(os.getenv("EXCHANGE_RATE_TTL", "300"))  # кэш курса, сек
MAX_RETRIES_429 = int(os.getenv("EXCHANGE_MAX_RETRIES_429", "3"))  # повторов
DEFAULT_RETRY_AFTER = float(os.getenv("EXCHANGE_RETRY_AFTER", "2"))  # сек, если заголовка нет


class ExchangeAPIError(Exception):
    """Понятная ошибка для пользователя при проблемах с API."""


class RateLimitError(ExchangeAPIError):
    """API вернул HTTP 429 (исчерпан лимит запросов)."""


# ---------------------------------------------------------------------------
# Состояние: троттлинг и кэш курса
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_last_request_ts = 0.0
_rate_cache: dict[tuple[str, str], tuple[float, float]] = {}  # (pair) -> (rate, ts)


def _throttle() -> None:
    """Выдержать минимальный интервал между запросами к API."""
    global _last_request_ts
    with _lock:
        now = time.monotonic()
        wait = MIN_REQUEST_INTERVAL - (now - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def _require_key() -> str:
    """Проверить наличие ключа БЕЗ сетевого запроса.

    Раньше здесь вызывался get_current_rate(), что удваивало число
    запросов и провоцировало 429. Теперь проверяем только переменную
    окружения; сам ключ используется в convert().
    """
    if not EXCHANGERATE_API_KEY:
        raise ExchangeAPIError(
            "Не задан EXCHANGERATE_API_KEY. "
            "Откройте .env и укажите ваш ключ api.exchangerate.host."
        )
    return EXCHANGERATE_API_KEY


def _parse_retry_after(response: requests.Response) -> float:
    """Прочитать заголовок Retry-After (секунды). Дефолт — DEFAULT_RETRY_AFTER."""
    value = response.headers.get("Retry-After")
    if not value:
        return DEFAULT_RETRY_AFTER
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


def _do_convert_request(
    key: str, amount: float, from_code: str, to_code: str
) -> float:
    """Выполнить запрос /convert с троттлингом и повторами на 429.

    Возвращает сконвертированную сумму (поле result).
    """
    params = {
        "access_key": key,
        "amount": amount,
        "from": from_code.upper(),
        "to": to_code.upper(),
    }

    attempt = 0
    while True:
        _throttle()
        try:
            response = requests.get(CONVERT_ENDPOINT, params=params, timeout=15)
        except requests.exceptions.ConnectionError as e:
            raise ExchangeAPIError(
                "Нет связи с api.exchangerate.host. "
                "Проверьте интернет или попробуйте позже."
            ) from e
        except requests.exceptions.Timeout as e:
            raise ExchangeAPIError(
                "api.exchangerate.host не ответил вовремя. Попробуйте позже."
            ) from e

        # Rate-limit: повторяем по Retry-After.
        if response.status_code == 429:
            attempt += 1
            if attempt > MAX_RETRIES_429:
                raise RateLimitError(
                    "Превышен лимит запросов к api.exchangerate.host (HTTP 429). "
                    "Подождите немного и попробуйте снова."
                )
            delay = _parse_retry_after(response)
            log.warning(
                "exchange: HTTP 429 (попытка %s/%s). Пауза %.1f c.",
                attempt,
                MAX_RETRIES_429,
                delay,
            )
            time.sleep(delay)
            continue

        # Остальные HTTP-ошибки.
        if response.status_code >= 400:
            code = response.status_code
            if code in (401, 403):
                raise ExchangeAPIError(
                    f"API отклонил ключ (HTTP {code}). "
                    "Проверьте EXCHANGERATE_API_KEY в .env."
                )
            raise ExchangeAPIError(f"Сервер вернул HTTP {code}.")

        try:
            data = response.json()
        except ValueError as e:  # JSONDecodeError
            raise ExchangeAPIError(
                "Сервер вернул некорректный ответ. Попробуйте позже."
            ) from e

        if not isinstance(data, dict):
            raise ExchangeAPIError("Некорректный формат ответа API.")

        if not data.get("success", False):
            err = data.get("error", {}) or {}
            info = err.get("info") or err.get("type") or "неизвестная ошибка API"
            ecode = err.get("code")
            raise ExchangeAPIError(
                f"API вернул ошибку: {info}" + (f" (код {ecode})" if ecode else "")
            )

        try:
            return float(data["result"])
        except (KeyError, TypeError, ValueError) as e:
            raise ExchangeAPIError("В ответе API нет поля result.") from e


def convert(
    amount: float,
    from_code: str,
    to_code: str,
) -> tuple[float, float]:
    """Конвертировать сумму между двумя валютами.

    Args:
        amount: сколько единиц из ``from_code`` конвертировать.
        from_code: код валюты-источника (например, "RUB").
        to_code: код валюты-назначения (например, "CNY").

    Returns:
        (converted_amount, rate)
        где ``rate`` — сколько ``from_code`` стоит 1 ``to_code``.

    Raises:
        ExchangeAPIError: при любой ошибке API/сети.
        RateLimitError: при исчерпанном лимите запросов (HTTP 429).
    """
    key = _require_key()
    converted = _do_convert_request(key, amount, from_code, to_code)

    # rate: сколько 1 единицы валюты-назначения стоит в валюте-источнике
    rate = (float(amount) / converted) if converted else 0.0
    return converted, rate


def get_rate(from_code: str, to_code: str) -> float:
    """Вернуть текущий курс: сколько ``from_code`` стоит 1 ``to_code``.

    Используется кэш (TTL = RATE_TTL), чтобы не превышать лимит запросов.
    """
    pair = (from_code.upper(), to_code.upper())
    now = time.monotonic()

    with _lock:
        cached = _rate_cache.get(pair)
        if cached and (now - cached[1]) < RATE_TTL:
            return cached[0]

    # amount=1 => rate == result
    converted, _ = convert(1.0, from_code, to_code)

    with _lock:
        _rate_cache[pair] = (converted, time.monotonic())
    return converted


def is_valid_pair(from_code: str, to_code: str) -> bool:
    """Проверить, доступна ли пара валют (через пробный convert)."""
    try:
        convert(1.0, from_code, to_code)
        return True
    except ExchangeAPIError:
        return False
