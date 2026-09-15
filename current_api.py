import os

import requests
from dotenv import load_dotenv

load_dotenv()


def get_current_rate(
    default: str = "USD",
    currencies: list[str] | None = None,
):
    """Вернуть актуальные курсы валют относительно `default`.

    Args:
        default: базовая валюта (по умолчанию "USD").
        currencies: список валют для сравнения
            (по умолчанию ["EUR", "GBP", "JPY"]).

    Raises:
        RuntimeError: если переменная окружения
            EXCHANGERATE_API_KEY не задана.
    """
    # Изменяемый список нельзя задавать значением по умолчанию напрямую,
    # поэтому подставляем его здесь.
    if currencies is None:
        currencies = ["EUR", "GBP", "JPY"]

    # Ключ берём из .env (файл не попадает в git, см. .gitignore).
    access_key = os.getenv("EXCHANGERATE_API_KEY")
    if not access_key:
        raise RuntimeError(
            "Не задан EXCHANGERATE_API_KEY. "
            "Создайте файл .env рядом с current_api.py и укажите ключ."
        )

    url = "https://api.exchangerate.host/live"
    params = {
        "access_key": access_key,
        "source": default,
        "currencies": ",".join(currencies),
        # ",".join(currencies) объединяет список в строку через запятую
    }

    response = requests.get(url, params=params)
    response.raise_for_status()  # выбросить ошибку при HTTP-коде 4xx/5xx
    return response.json()


if __name__ == "__main__":
    print(get_current_rate())