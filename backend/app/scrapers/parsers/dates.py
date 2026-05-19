import re
from datetime import datetime, timedelta, timezone


def parse_posted_date(value: str, now: datetime | None = None) -> datetime | None:
    text = value.strip().lower()
    reference = now or datetime.now(tz=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    if text in {"today", "posted today"}:
        return reference
    if text in {"yesterday", "posted yesterday"}:
        return reference - timedelta(days=1)

    match = re.search(r"(\d+)\s+day[s]?\s+ago", text)
    if match:
        return reference - timedelta(days=int(match.group(1)))

    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None
