"""Кошелёк путешественника — Telegram-бот.

Связывает модули проекта воедино:
    - countries.py  -> определение валюты по названию страны
    - exchange.py   -> актуальные курсы и конвертация (api.exchangerate.host)
    - storage.py    -> SQLite-хранилище (пользователи, путешествия, расходы)

Запуск:
    python wallet.py

Требуется TELEGRAM_BOT_TOKEN (и EXCHANGERATE_API_KEY) в .env.
"""
from __future__ import annotations

import logging
import os
import time

import requests
import telebot
from dotenv import load_dotenv

import countries
import exchange
import storage

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("wallet")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "Не задан TELEGRAM_BOT_TOKEN. Создайте файл .env рядом с wallet.py "
        "и укажите токен бота."
    )

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None)

# Необязательный прокси для доступа к api.telegram.org.
# Примеры значений TELEGRAM_PROXY в .env:
#   socks5h://127.0.0.1:1080
#   http://127.0.0.1:8888
# Требует: pip install requests[socks]
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()
if TELEGRAM_PROXY:
    telebot.apihelper.proxy = {"https": TELEGRAM_PROXY}
    log.info("wallet: использую прокси для Telegram: %s", TELEGRAM_PROXY)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def _fmt(value: float, ccy: str) -> str:
    """Отформатировать сумму: 1234.5 -> '1 234.50 RUB'."""
    return f"{value:,.2f} {ccy}".replace(",", " ")


def _resolve_country(raw: str) -> tuple[str, str, str] | None:
    """Вернуть (display_name, iso2, currency) или None."""
    return countries.country_to_currency(raw)


def _active_trip_or_reply(user_id: int, chat_id: int):
    """Вернуть активное путешествие; если его нет — отправить подсказку."""
    trip_id = storage.get_active_trip_id(user_id)
    if trip_id is None:
        bot.send_message(
            chat_id,
            "Нет активного путешествия. Создайте: /new <дом> <назначение>",
        )
        return None
    trip = storage.get_trip(trip_id, user_id)
    if trip is None:
        bot.send_message(
            chat_id,
            "Активное путешествие не найдено. Создайте новое: /new",
        )
        return None
    return trip


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------
@bot.message_handler(commands=["start"])
def cmd_start(message: telebot.types.Message) -> None:
    user_id = message.from_user.id
    storage.ensure_user(user_id)
    bot.send_message(
        message.chat.id,
        "Привет! Я кошелёк путешественника.\n\n"
        "Команды:\n"
        "/new <страна дома> <страна назначения> — создать путешествие\n"
        "/balance — балансы активного путешествия\n"
        "/topup <сумма> — пополнить баланс (домашняя валюта)\n"
        "/spend <сумма> — потратить (валюта назначения)\n"
        "/expenses — последние расходы\n"
        "/trips — список путешествий\n"
        "/use <id> — сделать путешествие активным\n"
        "/rate — обновить и показать текущий курс",
    )


@bot.message_handler(commands=["new"])
def cmd_new(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    parts = (message.text or "").split(maxsplit=2)
    # Ожидаем: /new <дом> <назначение> (по одному слову на страну)
    if len(parts) < 3:
        bot.send_message(
            chat_id,
            "Формат: /new <страна дома> <страна назначения>\n"
            "Например: /new Россия Турция",
        )
        return

    home_raw, dest_raw = parts[1], parts[2]
    home = _resolve_country(home_raw)
    dest = _resolve_country(dest_raw)

    if home is None:
        bot.send_message(chat_id, f"Не распознал страну дома: «{home_raw}».")
        return
    if dest is None:
        bot.send_message(
            chat_id, f"Не распознал страну назначения: «{dest_raw}»."
        )
        return

    home_name, _, home_ccy = home
    dest_name, _, dest_ccy = dest

    if home_ccy == dest_ccy:
        rate = 1.0
    else:
        try:
            rate = exchange.get_rate(home_ccy, dest_ccy)
        except exchange.ExchangeAPIError as e:
            bot.send_message(chat_id, f"Не удалось получить курс: {e}")
            return

    trip_id = storage.create_trip(
        user_id=user_id,
        home_country=home_name,
        dest_country=dest_name,
        home_ccy=home_ccy,
        dest_ccy=dest_ccy,
        rate=rate,
        balance_home=0.0,
        balance_dest=0.0,
    )

    bot.send_message(
        chat_id,
        f"Создано путешествие №{trip_id}: {home_name} ({home_ccy}) → "
        f"{dest_name} ({dest_ccy}).\n"
        f"Курс: 1 {dest_ccy} = {rate:,.4f} {home_ccy}\n"
        "Пополните баланс: /topup <сумма>".replace(",", " "),
    )


@bot.message_handler(commands=["balance"])
def cmd_balance(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    trip = _active_trip_or_reply(user_id, chat_id)
    if trip is None:
        return

    bot.send_message(
        chat_id,
        f"Путешествие №{trip['id']}: {trip['home_country']} → "
        f"{trip['dest_country']}\n"
        f"Домашний баланс: {_fmt(trip['balance_home'], trip['home_ccy'])}\n"
        f"Баланс назначения: {_fmt(trip['balance_dest'], trip['dest_ccy'])}\n"
        f"Курс: 1 {trip['dest_ccy']} = {trip['rate']:,.4f} {trip['home_ccy']}".replace(
            ",", " "
        ),
    )


@bot.message_handler(commands=["topup"])
def cmd_topup(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    trip = _active_trip_or_reply(user_id, chat_id)
    if trip is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(chat_id, "Формат: /topup <сумма>")
        return
    try:
        amount = float(parts[1].replace(",", "."))
    except ValueError:
        bot.send_message(chat_id, "Сумма должна быть числом.")
        return
    if amount <= 0:
        bot.send_message(chat_id, "Сумма должна быть больше нуля.")
        return

    new_home = float(trip["balance_home"]) + amount
    storage.update_trip_balances(
        trip["id"], user_id, new_home, float(trip["balance_dest"])
    )
    bot.send_message(
        chat_id,
        f"Пополнил на {_fmt(amount, trip['home_ccy'])}.\n"
        f"Домашний баланс: {_fmt(new_home, trip['home_ccy'])}",
    )


@bot.message_handler(commands=["spend"])
def cmd_spend(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    trip = _active_trip_or_reply(user_id, chat_id)
    if trip is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(chat_id, "Формат: /spend <сумма>")
        return
    try:
        amount_dest = float(parts[1].replace(",", "."))
    except ValueError:
        bot.send_message(chat_id, "Сумма должна быть числом.")
        return
    if amount_dest <= 0:
        bot.send_message(chat_id, "Сумма должна быть больше нуля.")
        return

    home_ccy = trip["home_ccy"]
    dest_ccy = trip["dest_ccy"]

    # Пересчитываем по ТЕКУЩЕМУ курсу, сколько домашней валюты
    # стоит потраченная сумма в валюте назначения.
    if home_ccy == dest_ccy:
        rate_used = 1.0
        amount_home = amount_dest
    else:
        try:
            rate_used = exchange.get_rate(home_ccy, dest_ccy)
        except exchange.ExchangeAPIError as e:
            bot.send_message(chat_id, f"Не удалось получить курс: {e}")
            return
        amount_home = amount_dest * rate_used

    balance_home = float(trip["balance_home"])
    balance_dest = float(trip["balance_dest"])

    if amount_home > balance_home:
        bot.send_message(
            chat_id,
            f"Недостаточно средств. Нужно {_fmt(amount_home, home_ccy)}, "
            f"на балансе {_fmt(balance_home, home_ccy)}",
        )
        return

    new_home = balance_home - amount_home
    new_dest = balance_dest + amount_dest
    storage.update_trip_balances(trip["id"], user_id, new_home, new_dest)
    storage.add_expense(
        trip_id=trip["id"],
        user_id=user_id,
        amount_dest=amount_dest,
        amount_home=amount_home,
        rate_used=rate_used,
    )
    bot.send_message(
        chat_id,
        f"Потрачено {_fmt(amount_dest, dest_ccy)} "
        f"(списано {_fmt(amount_home, home_ccy)} по курсу {rate_used:,.4f}).\n"
        f"Домашний баланс: {_fmt(new_home, home_ccy)}\n"
        f"Баланс назначения: {_fmt(new_dest, dest_ccy)}".replace(",", " "),
    )


@bot.message_handler(commands=["expenses"])
def cmd_expenses(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    trip = _active_trip_or_reply(user_id, chat_id)
    if trip is None:
        return

    rows = storage.list_expenses(trip["id"], user_id, limit=15)
    if not rows:
        bot.send_message(chat_id, "Расходов пока нет.")
        return

    lines = [f"Расходы путешествия №{trip['id']}:"]
    for r in rows:
        lines.append(
            f"{r['created_at']}  "
            f"{_fmt(r['amount_dest'], trip['dest_ccy'])} "
            f"({r['amount_home']:,.2f} {trip['home_ccy']})".replace(",", " ")
        )
    bot.send_message(chat_id, "\n".join(lines))


@bot.message_handler(commands=["trips"])
def cmd_trips(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    rows = storage.list_trips(user_id)
    if not rows:
        bot.send_message(chat_id, "Путешествий пока нет. /new")
        return

    active_id = storage.get_active_trip_id(user_id)
    lines = ["Ваши путешествия:"]
    for t in rows:
        mark = " (активное)" if t["id"] == active_id else ""
        lines.append(
            f"№{t['id']}{mark}: {t['home_country']} ({t['home_ccy']}) → "
            f"{t['dest_country']} ({t['dest_ccy']})"
        )
    lines.append("Сделать активным: /use <id>")
    bot.send_message(chat_id, "\n".join(lines))


@bot.message_handler(commands=["use"])
def cmd_use(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(chat_id, "Формат: /use <id>")
        return
    try:
        trip_id = int(parts[1])
    except ValueError:
        bot.send_message(chat_id, "id должен быть числом.")
        return

    trip = storage.get_trip(trip_id, user_id)
    if trip is None:
        bot.send_message(chat_id, f"Путешествие №{trip_id} не найдено.")
        return

    storage.set_active_trip(user_id, trip_id)
    bot.send_message(chat_id, f"Активное путешествие: №{trip_id}.")


@bot.message_handler(commands=["rate"])
def cmd_rate(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    trip = _active_trip_or_reply(user_id, chat_id)
    if trip is None:
        return

    home_ccy = trip["home_ccy"]
    dest_ccy = trip["dest_ccy"]
    if home_ccy == dest_ccy:
        bot.send_message(chat_id, f"Валюты совпадают ({home_ccy}), курс 1.0.")
        return

    try:
        new_rate = exchange.get_rate(home_ccy, dest_ccy)
    except exchange.ExchangeAPIError as e:
        bot.send_message(chat_id, f"Не удалось получить курс: {e}")
        return

    storage.update_trip_rate(trip["id"], user_id, new_rate)
    bot.send_message(
        chat_id,
        f"Текущий курс: 1 {dest_ccy} = {new_rate:,.4f} {home_ccy}".replace(
            ",", " "
        ),
    )


@bot.message_handler(commands=["help", "помощь"])
def cmd_help(message: telebot.types.Message) -> None:
    bot.send_message(
        message.chat.id,
        "Я кошелёк путешественника. См. /start для списка команд.",
    )


@bot.message_handler(func=lambda m: bool((m.text or "").startswith("/")))
def cmd_unknown(message: telebot.types.Message) -> None:
    bot.send_message(message.chat.id, "Неизвестная команда. /start")


# Параметры повторов при сетевых сбоях (ConnectTimeout и аналоги).
POLL_CONNECT_TIMEOUT = int(os.getenv("POLL_CONNECT_TIMEOUT", "15"))
POLL_READ_TIMEOUT = int(os.getenv("POLL_READ_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("POLL_MAX_RETRIES", "0"))  # 0 = бесконечно
RETRY_BASE_DELAY = float(os.getenv("POLL_RETRY_BASE_DELAY", "3"))  # сек
RETRY_MAX_DELAY = float(os.getenv("POLL_RETRY_MAX_DELAY", "60"))  # сек

# Сетевые исключения, которые считаем «временным сбоем связи».
NETWORK_ERRORS = (
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    telebot.apihelper.ApiTelegramException,
)


def _is_network_error(exc: BaseException) -> bool:
    """Определить, связан ли сбой с сетью (в т.ч. вложенные причины)."""
    if isinstance(exc, NETWORK_ERRORS):
        return True
    # telebot иногда оборачивает requests-ошибку в свою.
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return _is_network_error(cause)
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "timed out",
            "connection",
            "max retries exceeded",
            "timeout",
        )
    )


def _run_polling_with_retries() -> None:
    """Запустить polling; при сетевых сбоях — повтор с экспоненциальной задержкой."""
    attempt = 0
    while True:
        try:
            log.info(
                "wallet: подключаюсь к Telegram (connect=%ss, read=%ss)…",
                POLL_CONNECT_TIMEOUT,
                POLL_READ_TIMEOUT,
            )
            # none_stop=False, чтобы исключения всплывали в наш обработчик
            # и мы сами управляли паузами и повторами.
            bot.polling(
                none_stop=False,
                interval=1,
                timeout=POLL_READ_TIMEOUT,
            )
            # Если polling вернулся без исключения — штатно выходим.
            log.info("wallet: polling завершён без ошибки.")
            return
        except KeyboardInterrupt:
            log.info("wallet: остановлено пользователем (Ctrl+C).")
            return
        except Exception as exc:  # noqa: BLE001
            if not _is_network_error(exc):
                # Не сеть — чиним код, а не ждём.
                log.exception(
                    "wallet: polling упал с НЕ-сетевой ошибкой. "
                    "Это не проблема связи — проверьте код/данные. "
                    "Останавливаюсь."
                )
                raise

            attempt += 1
            if MAX_RETRIES and attempt > MAX_RETRIES:
                log.error(
                    "wallet: не удалось связаться с Telegram после %s "
                    "повторов. Останавливаюсь. Тип: %s: %s",
                    MAX_RETRIES,
                    type(exc).__name__,
                    exc,
                )
                raise

            # Экспоненциальная задержка с ограничением сверху.
            delay = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            log.warning(
                "wallet: нет связи с Telegram (попытка %s%s). "
                "Тип: %s: %s. Повтор через %.0f c. "
                "Если блокировка сети — настройте прокси "
                "(см. TELEGRAM_PROXY в .env) или смените сеть.",
                attempt,
                f"/{MAX_RETRIES}" if MAX_RETRIES else "",
                type(exc).__name__,
                exc,
                delay,
            )
            time.sleep(delay)


def main() -> None:
    storage.init_db()
    log.info("wallet: БД инициализирована, запускаю бота…")
    _run_polling_with_retries()


if __name__ == "__main__":
    main()
