import re
from typing import Any

from app.db.models import UserProfile


def build_profile_context(profile: UserProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "candidate": {"name": "", "location": "", "work_authorization": "", "clearance": []},
            "target_preferences": {"remote": "", "salary": "", "preferred_roles": []},
            "core_skills": [],
            "secondary_skills": [],
            "experience_summary": "",
            "strong_projects": [],
            "strengths": [],
            "constraints": [],
            "data_gaps": ["No saved CV/profile is available."],
        }

    preferences = profile.preferences or {}
    work_authorization = preferences.get("work_authorization", "")
    skills = _dedupe(profile.skills or [])
    projects = _strong_projects(profile.projects or [], skills)
    data_gaps = []
    if not skills:
        data_gaps.append("No skills extracted.")
    if not profile.preferred_roles:
        data_gaps.append("No preferred roles extracted.")
    if not (profile.location_preference or preferences.get("location")):
        data_gaps.append("No location preference extracted.")

    return {
        "candidate": {
            "name": _candidate_name(profile),
            "location": profile.location_preference or preferences.get("location", ""),
            "work_authorization": work_authorization,
            "clearance": _clearance(work_authorization),
        },
        "target_preferences": {
            "remote": profile.remote_preference or preferences.get("remote", ""),
            "salary": preferences.get("salary", "") or _salary_preference(profile),
            "preferred_roles": _dedupe(profile.preferred_roles or [])[:8],
        },
        "core_skills": skills[:12],
        "secondary_skills": skills[12:32],
        "experience_summary": _experience_summary(profile),
        "strong_projects": projects[:8],
        "strengths": _strengths(profile, skills),
        "constraints": _constraints(profile, preferences),
        "data_gaps": data_gaps,
    }


def _candidate_name(profile: UserProfile) -> str:
    for line in (profile.cv_text or "").splitlines()[:8]:
        value = line.strip()
        if not value or "@" in value or "linkedin" in value.lower() or any(char.isdigit() for char in value):
            continue
        words = value.split()
        if 1 < len(words) <= 4 and all(word[:1].isupper() for word in words):
            return value
    return ""


def _clearance(work_authorization: str) -> list[str]:
    text = work_authorization.lower()
    clearance = []
    if "sc cleared" in text or re.search(r"\bsc\s+clear", text):
        clearance.append("SC Cleared")
    if "bpss" in text:
        clearance.append("BPSS Cleared")
    return clearance


def _salary_preference(profile: UserProfile) -> str:
    if profile.salary_min_preference is None and profile.salary_max_preference is None:
        return ""
    if profile.salary_min_preference is not None and profile.salary_max_preference is not None:
        return f"{int(profile.salary_min_preference)}-{int(profile.salary_max_preference)}"
    return str(int(profile.salary_min_preference or profile.salary_max_preference or 0))


def _experience_summary(profile: UserProfile) -> str:
    if profile.summary:
        return profile.summary[:700]
    return " ".join((profile.experience or [])[:3])[:700]


def _strong_projects(projects: list[str], skills: list[str]) -> list[dict[str, Any]]:
    output = []
    for project in projects:
        name, evidence = _project_name_and_evidence(project)
        project_skills = [skill for skill in skills if skill.lower() in project.lower()]
        output.append({"name": name, "skills": project_skills[:8], "evidence": evidence[:280]})
    return output


def _project_name_and_evidence(project: str) -> tuple[str, str]:
    clean = " ".join(project.split())
    if " - " in clean:
        name, evidence = clean.split(" - ", 1)
    elif ":" in clean:
        name, evidence = clean.split(":", 1)
    else:
        words = clean.split()
        name, evidence = " ".join(words[:8]), clean
    return name.strip(), evidence.strip()


def _strengths(profile: UserProfile, skills: list[str]) -> list[str]:
    strengths = []
    if skills:
        strengths.append(f"Technical skill base includes {', '.join(skills[:8])}.")
    if profile.projects:
        strengths.append(f"Project evidence available across {min(len(profile.projects), 8)} projects.")
    if profile.experience:
        strengths.append("Experience entries are available for role relevance matching.")
    return strengths


def _constraints(profile: UserProfile, preferences: dict[str, str]) -> list[str]:
    constraints = []
    remote = profile.remote_preference or preferences.get("remote")
    location = profile.location_preference or preferences.get("location")
    salary = preferences.get("salary") or _salary_preference(profile)
    if remote:
        constraints.append(f"Remote preference: {remote}.")
    if location:
        constraints.append(f"Location preference: {location}.")
    if salary:
        constraints.append(f"Salary preference: {salary}.")
    if preferences.get("work_authorization"):
        constraints.append(f"Work authorization: {preferences['work_authorization']}.")
    return constraints


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        clean = value.strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output
