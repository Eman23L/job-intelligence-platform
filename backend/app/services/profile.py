import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, UserProfile


KNOWN_SKILLS = (
    "Python",
    "JavaScript",
    "TypeScript",
    "React",
    "Next.js",
    "FastAPI",
    "Django",
    "Flask",
    "SQL",
    "PostgreSQL",
    "MySQL",
    "SQLite",
    "dbt",
    "Airflow",
    "Docker",
    "Kubernetes",
    "AWS",
    "Azure",
    "GCP",
    "Terraform",
    "Git",
    "CI/CD",
    "Pandas",
    "NumPy",
    "Power BI",
    "Tableau",
    "Machine Learning",
    "LLM",
    "RAG",
)

SECTION_ALIASES = {
    "skills": ("skills", "technical skills", "technologies"),
    "experience": ("experience", "work experience", "employment", "professional experience"),
    "projects": ("projects", "project experience"),
    "education": ("education", "qualifications"),
    "preferred_roles": ("preferred roles", "target roles", "roles of interest", "desired roles"),
    "preferences": ("preferences", "job preferences", "role preferences"),
}


def get_profile(db: Session, user: User) -> UserProfile | None:
    return db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))


def upsert_cv_profile(db: Session, user: User, cv_text: str) -> UserProfile:
    extracted = extract_profile_fields(cv_text)
    profile = get_profile(db, user)
    if profile is None:
        profile = UserProfile(user_id=user.id, cv_text=cv_text, **extracted)
        db.add(profile)
    else:
        profile.cv_text = cv_text
        for field, value in extracted.items():
            setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


def extract_profile_fields(cv_text: str) -> dict:
    text = cv_text.strip()
    sections = _sections(text)
    preference_text = "\n".join(
        value for value in [sections.get("preferences", ""), sections.get("preferred_roles", ""), text] if value
    )
    salary_min, salary_max = _salary_range(preference_text)
    return {
        "skills": _skills(text, sections.get("skills", "")),
        "experience": _section_items(sections.get("experience", "")),
        "projects": _section_items(sections.get("projects", "")),
        "education": _section_items(sections.get("education", "")),
        "preferred_roles": _preferred_roles(sections.get("preferred_roles", ""), text),
        "location_preference": _location_preference(preference_text),
        "remote_preference": _remote_preference(preference_text),
        "salary_min_preference": salary_min,
        "salary_max_preference": salary_max,
    }


def _sections(text: str) -> dict[str, str]:
    section_lookup = {alias: key for key, aliases in SECTION_ALIASES.items() for alias in aliases}
    current: str | None = None
    output: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        normalized = line.lower().rstrip(":")
        if normalized in section_lookup:
            current = section_lookup[normalized]
            output.setdefault(current, [])
            continue
        if current and line:
            output[current].append(line)
    return {key: "\n".join(lines) for key, lines in output.items()}


def _skills(text: str, skill_section: str) -> list[str]:
    found = []
    haystack = text.lower()
    for skill in KNOWN_SKILLS:
        if re.search(rf"(?<![A-Za-z0-9+#.]){re.escape(skill.lower())}(?![A-Za-z0-9+#.])", haystack):
            found.append(skill)
    for item in _split_listish(skill_section):
        if 1 <= len(item) <= 40 and item.lower() not in {skill.lower() for skill in found}:
            found.append(item)
    return _dedupe(found)


def _section_items(section_text: str) -> list[str]:
    if not section_text.strip():
        return []
    items = _split_listish(section_text)
    return items or [line.strip() for line in section_text.splitlines() if line.strip()]


def _preferred_roles(section_text: str, full_text: str) -> list[str]:
    roles = _split_listish(section_text)
    if roles:
        return _dedupe(roles)
    matches = re.findall(
        r"\b(?:data engineer|analytics engineer|software engineer|full stack developer|backend developer|"
        r"frontend developer|machine learning engineer|ai engineer|automation engineer)\b",
        full_text,
        flags=re.IGNORECASE,
    )
    return _dedupe([match.title() for match in matches])


def _location_preference(text: str) -> str | None:
    match = re.search(r"\b(?:location|based in|preferred location)\s*[:\-]?\s*([A-Za-z ,]+)", text, flags=re.IGNORECASE)
    if match:
        value = match.group(1).strip(" ,.")
        return value[:255] or None
    for place in ("London", "Manchester", "Birmingham", "Bristol", "Leeds", "Remote UK", "UK"):
        if re.search(rf"\b{re.escape(place)}\b", text, flags=re.IGNORECASE):
            return place
    return None


def _remote_preference(text: str) -> str | None:
    lowered = text.lower()
    if "fully remote" in lowered or "remote only" in lowered:
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if "onsite" in lowered or "on-site" in lowered:
        return "onsite"
    if "remote" in lowered:
        return "remote"
    return None


def _salary_range(text: str) -> tuple[Decimal | None, Decimal | None]:
    amounts = []
    for match in re.finditer(r"(?:\u00a3|gbp\s*)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", text, flags=re.IGNORECASE):
        raw_value = Decimal(match.group(1).replace(",", ""))
        value = raw_value * 1000 if match.group(2) or raw_value < 1000 else raw_value
        amounts.append(value)
    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def _split_listish(value: str) -> list[str]:
    cleaned = value.replace("\u2022", "\n").replace(";", "\n")
    parts = re.split(r"[\n,]+", cleaned)
    return [part.strip(" -*\t.") for part in parts if part.strip(" -*\t.")]


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output
