import re
from decimal import Decimal


SALARY_RE = re.compile(
    r"(?P<currency>\u00a3|\$|\u20ac|GBP|USD|EUR)?\s*(?P<first>\d+k|\d{4,6}|\d{2,3}(?:,\d{3})?)"
    r"(?:\s*(?:-|to|\u2013)\s*(?P<second>\d+k|\d{4,6}|\d{2,3}(?:,\d{3})?))?",
    re.IGNORECASE,
)

CURRENCY_MAP = {"\u00a3": "GBP", "GBP": "GBP", "$": "USD", "USD": "USD", "\u20ac": "EUR", "EUR": "EUR"}
WORKING_DAYS_PER_YEAR = Decimal("230")
HOURS_PER_DAY = Decimal("8")


def parse_salary(text: str) -> dict[str, Decimal | str | None]:
    match = SALARY_RE.search(text)
    if not match:
        return _empty_salary()

    first = _parse_amount(match.group("first"))
    second = _parse_amount(match.group("second")) if match.group("second") else first
    currency = CURRENCY_MAP.get((match.group("currency") or "GBP").upper(), "GBP")
    period = _detect_period(text, first, second)
    annual_min, annual_max = normalise_annual_salary(first, second, period)
    return {
        "salary_min": first,
        "salary_max": second,
        "salary_currency": currency,
        "salary_min_raw": first,
        "salary_max_raw": second,
        "salary_period": period,
        "normalized_annual_min": annual_min,
        "normalized_annual_max": annual_max,
    }


def normalise_annual_salary(
    salary_min: Decimal | None,
    salary_max: Decimal | None,
    salary_period: str | None,
) -> tuple[Decimal | None, Decimal | None]:
    if salary_min is None and salary_max is None:
        return None, None
    period = salary_period or "year"
    multiplier = Decimal("1")
    if period == "day":
        multiplier = WORKING_DAYS_PER_YEAR
    elif period == "hour":
        multiplier = HOURS_PER_DAY * WORKING_DAYS_PER_YEAR
    return (
        salary_min * multiplier if salary_min is not None else None,
        salary_max * multiplier if salary_max is not None else None,
    )


def _parse_amount(value: str) -> Decimal:
    cleaned = value.lower().replace(",", "").strip()
    if cleaned.endswith("k"):
        return Decimal(cleaned[:-1]) * Decimal("1000")
    return Decimal(cleaned)


def _detect_period(text: str, salary_min: Decimal, salary_max: Decimal) -> str:
    lowered = text.lower()
    if re.search(r"\b(hour|hourly|hr|p/h|per hour|/h|/hr)\b", lowered):
        return "hour"
    if re.search(r"\b(day|daily|day rate|per day|p/d|/day|pd)\b", lowered):
        return "day"
    if re.search(r"\b(year|annual|annum|yearly|per year|p/a|pa|/year|salary)\b", lowered):
        return "year"
    highest = max(salary_min, salary_max)
    if highest <= Decimal("150"):
        return "hour"
    if highest <= Decimal("2000"):
        return "day"
    return "year"


def _empty_salary() -> dict[str, Decimal | str | None]:
    return {
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_min_raw": None,
        "salary_max_raw": None,
        "salary_period": None,
        "normalized_annual_min": None,
        "normalized_annual_max": None,
    }
