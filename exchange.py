"""Работа с api.exchangerate.host: endpoint /convert и access_key.

Используется @current_api.py для получения ключа и проверки доступа к API
(``get_current_rate``). Собственные запросы идут строго на
http://api.exchangerate.host/convert с параметром access_key.
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

# Импорт из @current_api.py — там хранится логика доступа к API
load_dotenv()

from current_api import get_current_rate  # noqa: E402

# Конфигурация: access_key для API и токен бота (вторая переменная).
EXCHANGERATE_API_KEY = os.getenv("EXCHANGERATE_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

CONVERT_ENDPOINT = "http://api.exchangerate.host/convert"


class ExchangeAPIError(Exception):
    """Понятная ошибка для пользователя при проблемах с API."""


def _require_key() -> str:
    """Проверить наличие ключа через @current_api.py.

    Если ключ не задан, get_current_rate() бросит RuntimeError —
    переводим его в понятную ExchangeAPIError.
    """
    try:
        get_current_rate(default="USD", currencies=["RUB"])
    except RuntimeError as e:
        raise ExchangeAPIError(
            "Не задан EXCHANGERATE_API_KEY. "
            "Откройте .env и укажите ваш ключ api.exchangerate.host."
        ) from e
    if not EXCHANGERATE_API_KEY:
        raise ExchangeAPIError(
            "Не задан EXCHANGERATE_API_KEY. "
            "Откройте .env и укажите ваш ключ api.exchangerate.host."
        )
    return EXCHANGERATE_API_KEY


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
    """
    key = _require_key()
    params = {
        "access_key": key,
        "amount": amount,
        "from": from_code.upper(),
        "to": to_code.upper(),
    }
    try:
        response = requests.get(CONVERT_ENDPOINT, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.ConnectionError as e:
        raise ExchangeAPIError(
            "Нет связи с api.exchangerate.host. "
            "Проверьте интернет или попробуйте позже."
        ) from e
    except requests.exceptions.Timeout as e:
        raise ExchangeAPIError(
            "api.exchangerate.host не ответил вовремя. Попробуйте позже."
        ) from e
    except requests.exceptions.HTTPError as e:
        raise ExchangeAPIError(
            f"Сервер вернул HTTP {response.status_code}. "
            "Возможно, ключ недействителен или исчерпан лимит."
        ) from e
    except ValueError as e:  # JSONDecodeError
        raise ExchangeAPIError(
            "Сервер вернул некорректный ответ. Попробуйте позже."
        ) from e

    if not isinstance(data, dict):
        raise ExchangeAPIError("Некорректный формат ответа API.")

    if not data.get("success", False):
        err = data.get("error", {}) or {}
        info = err.get("info") or err.get("type") or "неизвестная ошибка API"
        code = err.get("code")
        raise ExchangeAPIError(
            f"API вернул ошибку: {info}" + (f" (код {code})" if code else "")
        )

    try:
        converted = float(data["result"])
    except (KeyError, TypeError, ValueError) as e:
        raise ExchangeAPIError("В ответе API нет поля result.") from e

    # rate: сколько 1 единицы валюты-назначения стоит в валюте-источнике
    if converted:
        rate = float(amount) / converted
    else:
        rate = 0.0

    return converted, rate


def get_rate(from_code: str, to_code: str) -> float:
    """Вернуть текущий курс: сколько ``from_code`` стоит 1 ``to_code``.

    Выполняется запрос с amount=1, поэтому rate == result.
    """
    converted, _ = convert(1.0, from_code, to_code)
    return converted


def is_valid_pair(from_code: str, to_code: str) -> bool:
    """Проверить, доступна ли пара валют (через пробный convert)."""
    try:
        convert(1.0, from_code, to_code)
        return True
    except ExchangeAPIError:
        return False
