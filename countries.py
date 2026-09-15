"""Справочник стран и валют.

Используем pycountry (установлен в requirements.txt) для поиска
стран по названию. Русские названия сначала транслитерируются,
затем ищутся через pycountry; fallback-словарь даёт валюту.
"""
from __future__ import annotations

from typing import Final

try:
    import pycountry  # type: ignore

    _HAS_PYCOUNTRY = True
except ImportError:
    pycountry = None  # type: ignore
    _HAS_PYCOUNTRY = False


# ---------------------------------------------------------------------------
# Транслитерация кириллицы в латиницу (для поиска по pycountry)
# ---------------------------------------------------------------------------
_TRANSLIT: Final[dict[str, str]] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _transliterate(raw: str) -> str:
    out = []
    for ch in raw.lower():
        out.append(_TRANSLIT.get(ch, ch))
    return "".join(out)


# ---------------------------------------------------------------------------
# Минимальный набор для офлайн-режима (если pycountry не установлен).
# ---------------------------------------------------------------------------
_FALLBACK: dict[str, tuple[str, str]] = {
    # "ключ": (iso2, currency)
    "россия": ("RU", "RUB"),
    "russia": ("RU", "RUB"),
    "russian federation": ("RU", "RUB"),
    "китай": ("CN", "CNY"),
    "china": ("CN", "CNY"),
    "турция": ("TR", "TRY"),
    "turkey": ("TR", "TRY"),
    "türkiye": ("TR", "TRY"),
    "таиланд": ("TH", "THB"),
    "thailand": ("TH", "THB"),
    "оаэ": ("AE", "AED"),
    "uae": ("AE", "AED"),
    "united arab emirates": ("AE", "AED"),
    "germany": ("DE", "EUR"),
    "германия": ("DE", "EUR"),
    "france": ("FR", "EUR"),
    "франция": ("FR", "EUR"),
    "italy": ("IT", "EUR"),
    "италия": ("IT", "EUR"),
    "spain": ("ES", "EUR"),
    "испания": ("ES", "EUR"),
    "cyprus": ("CY", "EUR"),
    "кипр": ("CY", "EUR"),
    "georgia": ("GE", "GEL"),
    "грузия": ("GE", "GEL"),
    "armenia": ("AM", "AMD"),
    "армения": ("AM", "AMD"),
    "azerbaijan": ("AZ", "AZN"),
    "азербайджан": ("AZ", "AZN"),
    "kazakhstan": ("KZ", "KZT"),
    "казахстан": ("KZ", "KZT"),
    "uzbekistan": ("UZ", "UZS"),
    "узбекистан": ("UZ", "UZS"),
    "united states": ("US", "USD"),
    "сша": ("US", "USD"),
    "usa": ("US", "USD"),
    "united kingdom": ("GB", "GBP"),
    "great britain": ("GB", "GBP"),
    "великобритания": ("GB", "GBP"),
    "uk": ("GB", "GBP"),
    "japan": ("JP", "JPY"),
    "япония": ("JP", "JPY"),
    "korea, republic of": ("KR", "KRW"),
    "korea": ("KR", "KRW"),
    "южная корея": ("KR", "KRW"),
    "viet nam": ("VN", "VND"),
    "vietnam": ("VN", "VND"),
    "вьетнам": ("VN", "VND"),
    "india": ("IN", "INR"),
    "индия": ("IN", "INR"),
    "indonesia": ("ID", "IDR"),
    "индонезия": ("ID", "IDR"),
    "бали": ("ID", "IDR"),
    "belarus": ("BY", "BYN"),
    "беларусь": ("BY", "BYN"),
    "maldives": ("MV", "MVR"),
    "мальдивы": ("MV", "MVR"),
    "seychelles": ("SC", "SCR"),
    "сейшелы": ("SC", "SCR"),
    "egypt": ("EG", "EGP"),
    "египет": ("EG", "EGP"),
    "united arab": ("AE", "AED"),
}


def _normalize(raw: str) -> str:
    return (raw or "").strip().lower().replace("  ", " ")


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------
def country_to_currency(raw: str) -> tuple[str, str, str] | None:
    """Вернуть (display_name, iso2, currency) для названия страны.

    Если страна не найдена — None.
    """
    if not (raw or "").strip():
        return None

    # 1) прямой fallback по исходному тексту (рус/англ)
    fb = _FALLBACK.get(_normalize(raw))
    if fb:
        iso2, cur = fb
        return _pretty(raw.strip()), iso2, cur

    # 2) транслитерация и поиск
    translit = _transliterate(raw.strip())
    fb = _FALLBACK.get(translit)
    if fb:
        iso2, cur = fb
        return _pretty(raw.strip()), iso2, cur

    # 3) pycountry по транслиту
    if _HAS_PYCOUNTRY:
        c = _find_country(translit) or _find_country(raw.strip())
        if c is not None:
            cur = _currency_for_country(c)
            if cur:
                return c.name, c.alpha_2, cur

    return None


def _find_country(query: str):
    """Найти страну в pycountry (по имени, common_name, fuzzy)."""
    if not _HAS_PYCOUNTRY or not query:
        return None
    q = query.strip()
    title = q.title()
    try:
        c = pycountry.countries.get(name=title)
        if c is not None:
            return c
    except LookupError:
        pass
    try:
        c = pycountry.countries.get(common_name=title)
        if c is not None:
            return c
    except LookupError:
        pass
    try:
        c = pycountry.countries.get(official_name=title)
        if c is not None:
            return c
    except LookupError:
        pass
    try:
        matches = pycountry.countries.search_fuzzy(q)
    except LookupError:
        return None
    return matches[0] if matches else None


def _currency_for_country(country) -> str | None:
    """Найти валюту, привязанную к стране через pycountry.currencies."""
    if _HAS_PYCOUNTRY:
        # pycountry: валюта хранится под alpha_3 = alpha_3 страны + "1"
        # (например, "RUS1" -> RUB).
        try:
            cur = pycountry.currencies.get(alpha_3=f"{country.alpha_3}1")
            if cur is not None:
                return cur.alpha_3
        except LookupError:
            pass

        # Второй путь — по fallback-словарю
        fb = _FALLBACK.get(_normalize(country.name))
        if fb:
            return fb[1]

        # Третий путь — прямой поиск по alpha_3 страны (на случай,
        # если в базе валюта названа так же, как страна)
        try:
            cur = pycountry.currencies.get(alpha_3=country.alpha_3)
            if cur is not None:
                return cur.alpha_3
        except LookupError:
            pass

    return None


def _pretty(name: str) -> str:
    """Сделать 'россия' -> 'Россия'."""
    return name.strip().capitalize() if name else name
