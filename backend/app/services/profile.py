import re
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, UserProfile


KNOWN_SKILLS = (
    "Python",
    "JavaScript",
    "TypeScript",
    "React",
    "Next.js",
    "Tailwind CSS",
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
    "Power Apps",
    "Power Automate",
    "Power Query",
    "Excel",
    "SharePoint",
    "BeautifulSoup",
    "Requests",
    "JSON",
    "Cloudflare",
    "Supabase",
    "Linux",
    "WSL",
    "Bash",
    "YAML",
    "Tableau",
    "Machine Learning",
    "LLM",
    "RAG",
    "Project Management",
    "Stakeholder Management",
    "Service Design",
    "Business Analysis",
    "Agile",
    "Scrum",
    "Data Pipelines",
    "Web Scraping",
    "Workflow Automation",
    "Dashboard Reporting",
    "Systems Integration",
    "Role-Based Access Control",
    "Data Modelling",
    "Automation",
    "AI",
)

MAJOR_SECTION_HEADINGS = {
    "CAREER SUMMARY": "summary",
    "EDUCATION": "education",
    "PROFESSIONAL EXPERIENCE": "experience",
    "WORK EXPERIENCE": "experience",
    "PROJECTS": "projects",
    "PROJECT": "projects",
    "SKILLS": "skills",
    "EXPERIENCE": "experience",
    "CERTIFICATIONS": "certifications",
    "SECURITY CLEARANCE": "security_clearance",
    "EMPLOYMENT HISTORY": "experience",
    "MANAGEMENT EXPERIENCE": "experience",
    "DESIGN EXPERIENCE": "experience",
    "PREFERRED ROLES": "preferred_roles",
    "TARGET ROLES": "preferred_roles",
    "ROLES OF INTEREST": "preferred_roles",
    "DESIRED ROLES": "preferred_roles",
    "REFERENCES": "ignored",
}

PREFERENCE_HEADINGS = {
    "PREFERENCES",
    "JOB PREFERENCES",
    "ROLE PREFERENCES",
}

ROLE_KEYWORDS = (
    "Data Engineer",
    "Analytics Engineer",
    "Software Engineer",
    "Full Stack Developer",
    "Backend Developer",
    "Frontend Developer",
    "Machine Learning Engineer",
    "AI Engineer",
    "Automation Engineer",
    "Project Manager",
    "Programme Manager",
    "Product Manager",
    "Business Analyst",
    "Service Designer",
    "Delivery Manager",
    "UX Designer",
    "Power Platform Developer",
    "Reporting Analyst",
    "Data Analyst",
)

REPEATED_LABELS = (
    "Name",
    "Email",
    "Phone",
    "Mobile",
    "LinkedIn",
    "Portfolio",
    "Address",
)

KNOWN_LOCATIONS = (
    "Milton Keynes",
    "Broughton",
    "United Kingdom",
    "London",
    "Manchester",
    "Birmingham",
    "Bristol",
    "Leeds",
    "Remote UK",
    "UK",
)

KNOWN_PROJECT_NAMES = (
    "GetFlow - Contributions Management Platform",
    "UK Homelessness Support Data Pipeline",
    "Self-Hosted Remote Development Environment",
    "Opportunity DecisionAI",
    "Power Platform & Reporting Solutions",
    "Power BI Timesheet Dashboard",
)


def get_profile(db: Session, user: User) -> UserProfile | None:
    return db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))


def upsert_cv_profile(db: Session, user: User, cv_text: str) -> UserProfile:
    extracted = extract_profile_fields(cv_text)
    salary_min, salary_max = _salary_preference_bounds(extracted["preferences"]["salary"])
    storage_fields = {
        **extracted,
        "location_preference": extracted["preferences"]["location"] or None,
        "remote_preference": extracted["preferences"]["remote"] or None,
        "salary_min_preference": salary_min,
        "salary_max_preference": salary_max,
    }
    profile = get_profile(db, user)
    if profile is None:
        profile = UserProfile(user_id=user.id, cv_text=cv_text, **storage_fields)
        db.add(profile)
    else:
        profile.cv_text = cv_text
        for field, value in storage_fields.items():
            setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


def extract_profile_fields(cv_text: str) -> dict[str, Any]:
    cleaned_text = clean_cv_text(cv_text)
    sections = split_cv_sections(cleaned_text)
    summary = _paragraph(sections.get("summary", []))
    preferences = _preferences(sections)
    projects = _project_items(sections)

    return {
        "summary": summary,
        "skills": _skills(cleaned_text, sections.get("skills", [])),
        "experience": _experience_items(sections, projects),
        "projects": projects,
        "education": _section_items(sections.get("education", [])),
        "preferred_roles": _preferred_roles(sections, projects),
        "preferences": preferences,
    }


def clean_cv_text(cv_text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in cv_text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        if _is_noise_line(line, allow_location=True):
            continue
        line = _remove_repeated_label(line)
        if not line or _is_empty_bullet(line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def split_cv_sections(cv_text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "preamble"
    sections.setdefault(current, [])

    for line in cv_text.splitlines():
        heading = _section_key(line)
        if heading is not None:
            current = heading
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {key: lines for key, lines in sections.items() if lines}


def _section_key(line: str) -> str | None:
    normalized = re.sub(r"[^A-Za-z ]+", "", line).upper().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in MAJOR_SECTION_HEADINGS:
        return MAJOR_SECTION_HEADINGS[normalized]
    if normalized in PREFERENCE_HEADINGS:
        return "preferences"
    return None


def _experience_items(sections: dict[str, list[str]], projects: list[str]) -> list[str]:
    items = _section_items(sections.get("experience", []))
    return _dedupe(items + projects)


def _project_items(sections: dict[str, list[str]]) -> list[str]:
    candidates: list[str] = []
    for section_name in ("projects", "experience"):
        for line in sections.get(section_name, []):
            candidates.extend(_projects_from_line(line))
    for line in sections.get("projects", []):
        cleaned = _clean_item(line)
        if cleaned and not any(_same_project(cleaned, item) for item in candidates):
            candidates.append(cleaned)
    return _dedupe(candidates)


def _projects_from_line(line: str) -> list[str]:
    cleaned = _clean_item(line)
    if not cleaned or _is_noise_line(cleaned):
        return []

    match = re.search(r"\bProject\s*[:\-]\s*(.+)", cleaned, flags=re.IGNORECASE)
    if match:
        return [match.group(1).strip(" .")]

    matches = []
    for project_name in KNOWN_PROJECT_NAMES:
        pattern = _project_name_pattern(project_name)
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            if re.search(rf"{pattern}\s*[-:\u2013\u2014]\s*(.+)", cleaned, flags=re.IGNORECASE):
                matches.append(cleaned)
            else:
                matches.append(project_name)
    return matches


def _section_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    buffer: list[str] = []
    for line in lines:
        text = _clean_item(line)
        if not text or _is_noise_line(text):
            continue
        if _starts_new_item(line):
            if buffer:
                items.append(" ".join(buffer))
            buffer = [text]
        elif buffer and _looks_continuation(text):
            buffer.append(text)
        else:
            if buffer:
                items.append(" ".join(buffer))
            buffer = [text]
    if buffer:
        items.append(" ".join(buffer))
    return _dedupe([item for item in items if len(item) > 1])


def _skills(full_text: str, skill_lines: list[str]) -> list[str]:
    found = []
    haystack = full_text.lower()
    for skill in KNOWN_SKILLS:
        if re.search(rf"(?<![A-Za-z0-9+#.]){re.escape(skill.lower())}(?![A-Za-z0-9+#.])", haystack):
            found.append(skill)
    for item in _split_listish("\n".join(skill_lines)):
        if 1 <= len(item) <= 40 and item.lower() not in {skill.lower() for skill in found}:
            found.append(item)
    return _dedupe(found)


def _preferred_roles(sections: dict[str, list[str]], projects: list[str]) -> list[str]:
    explicit_roles = _split_listish("\n".join(sections.get("preferred_roles", [])))
    if explicit_roles:
        return _dedupe(explicit_roles)

    source_text = "\n".join(
        sections.get("summary", [])
        + sections.get("experience", [])
        + sections.get("projects", [])
        + sections.get("skills", [])
    )
    roles = []
    for role in ROLE_KEYWORDS:
        if re.search(rf"\b{re.escape(role)}\b", source_text, flags=re.IGNORECASE):
            roles.append(role)
    for project in projects:
        project_name = re.split(r"\s+[-:]\s+", project, maxsplit=1)[0]
        for role in _roles_from_phrase(project_name):
            roles.append(role)
    for line in sections.get("skills", []):
        for role in _roles_from_phrase(line):
            roles.append(role)
    return _dedupe(roles)


def _roles_from_phrase(value: str) -> list[str]:
    lowered = value.lower()
    roles = []
    if "management" in lowered:
        roles.append("Project Manager")
    if "design" in lowered:
        roles.append("Service Designer")
    if "analysis" in lowered or "analyst" in lowered:
        roles.append("Business Analyst")
    if "automation" in lowered:
        roles.append("Automation Engineer")
    if "data" in lowered:
        roles.append("Data Engineer")
    return roles


def _preferences(sections: dict[str, list[str]]) -> dict[str, str]:
    preference_text = "\n".join(sections.get("preferences", []))
    searchable_text = preference_text or "\n".join(
        sections.get("preamble", []) + sections.get("summary", []) + sections.get("security_clearance", [])
    )
    salary_min, salary_max = _salary_range(preference_text)
    salary = ""
    if salary_min and salary_max:
        salary = f"{int(salary_min)}-{int(salary_max)}"
    elif salary_min:
        salary = str(int(salary_min))
    return {
        "remote": _remote_preference(searchable_text) or "",
        "location": _location_preference(sections.get("preamble", [])) or "",
        "salary": salary,
        "work_authorization": _work_authorization(searchable_text) or "",
        "target_seniority": _target_seniority_preference(searchable_text),
    }


def _location_preference(header_lines: list[str]) -> str | None:
    text = "\n".join(header_lines[:10])
    found = []
    for place in KNOWN_LOCATIONS:
        if re.search(rf"\b{re.escape(place)}\b", text, flags=re.IGNORECASE):
            found.append(place)
    if "United Kingdom" in found and "UK" in found:
        found.remove("UK")
    if found:
        return " / ".join(_dedupe(found))
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


def _work_authorization(text: str) -> str | None:
    values = []
    if re.search(r"does\s+not\s+require\s+sponsorship|no\s+sponsorship", text, flags=re.IGNORECASE):
        values.append("does not require sponsorship")
    if re.search(r"\bSC\s+Cleared\b|\bSC\s+Clearance\b", text, flags=re.IGNORECASE):
        values.append("SC Cleared")
    if re.search(r"\bBPSS\s+Cleared\b|\bBPSS\b", text, flags=re.IGNORECASE):
        values.append("BPSS Cleared")
    return "; ".join(_dedupe(values)) if values else None


def _target_seniority_preference(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b(any seniority|open to all levels)\b", lowered):
        return "any"
    if re.search(r"\b(senior leadership|leadership roles|head of|director|principal|senior roles?)\b", lowered):
        return "senior"
    if re.search(r"\b(mid[-\s]?senior|experienced mid)\b", lowered):
        return "mid_senior"
    if re.search(r"\b(junior|entry[-\s]?level|graduate)\b", lowered):
        return "junior"
    if re.search(r"\b(mid[-\s]?level|intermediate)\b", lowered):
        return "mid"
    return "mid_senior"


def _salary_range(text: str) -> tuple[Decimal | None, Decimal | None]:
    amounts = []
    for match in re.finditer(r"(?:\u00a3|gbp\s*)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", text, flags=re.IGNORECASE):
        raw_value = Decimal(match.group(1).replace(",", ""))
        value = raw_value * 1000 if match.group(2) or raw_value < 1000 else raw_value
        amounts.append(value)
    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def _salary_preference_bounds(value: str) -> tuple[Decimal | None, Decimal | None]:
    if not value:
        return None, None
    amounts = [Decimal(match) for match in re.findall(r"\d+", value)]
    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def _paragraph(lines: list[str]) -> str:
    return " ".join(_clean_item(line) for line in lines if _clean_item(line)).strip()


def _split_listish(value: str) -> list[str]:
    cleaned = value.replace("\u2022", "\n").replace(";", "\n")
    parts = re.split(r"[\n,]+", cleaned)
    return [_clean_item(part) for part in parts if _clean_item(part)]


def _starts_new_item(line: str) -> bool:
    stripped = line.strip()
    return bool(
        re.match(r"^[-*\u2022]\s+", stripped)
        or re.match(r"^\d+[.)]\s+", stripped)
        or re.search(r"\b(?:Ltd|Limited|Council|NHS|University|Bank|Group|Agency)\b", stripped)
        or re.search(r"\b(?:Project|Programme|Manager|Engineer|Analyst|Designer|Consultant)\b", stripped)
    )


def _looks_continuation(text: str) -> bool:
    return bool(text and text[0].islower())


def _clean_item(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^[-*\u2022]\s*", "", value)
    value = re.sub(r"^\d+[.)]\s*", "", value)
    return value.strip(" \t.")


def _is_empty_bullet(line: str) -> bool:
    return bool(re.fullmatch(r"[-*\u2022\s]+", line))


def _is_noise_line(line: str, *, allow_location: bool = False) -> bool:
    normalized = line.strip()
    if not normalized:
        return True
    if re.fullmatch(r"Page\s+\d+\s+of\s+\d+", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"(linkedin\.com|linkedin\s*:)", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"AtkinsR.alis\s*-\s*Baseline|AtkinsR.alis\s*-\s*R.f.rence", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"\b[\w.+-]+@[\w.-]+\.\w+\b", normalized):
        return True
    if re.search(r"\b(?:phone|mobile|tel|portfolio)\s*:", normalized, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"(?:\+?\d[\d\s().-]{7,})", normalized):
        return True
    if re.fullmatch(r"Emmanuel\s+Bamgbala", normalized, flags=re.IGNORECASE):
        return True
    if not allow_location and any(re.fullmatch(re.escape(place), normalized, flags=re.IGNORECASE) for place in KNOWN_LOCATIONS):
        return True
    return False


def _same_project(left: str, right: str) -> bool:
    return left.casefold() == right.casefold() or left.casefold().startswith(right.casefold())


def _project_name_pattern(project_name: str) -> str:
    return re.escape(project_name).replace(r"\-", r"[-\u2013\u2014]")


def _remove_repeated_label(line: str) -> str:
    for label in REPEATED_LABELS:
        line = re.sub(rf"^(?:{label}\s*[:\-]\s*){{2,}}", f"{label}: ", line, flags=re.IGNORECASE)
    return line.strip()


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output
