from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, User
from app.schemas.database import AssistApplyResult
from app.services.job_availability import check_job_availability
from app.services.profile import get_profile
from app.services.run_tracking import utcnow

logger = logging.getLogger(__name__)

ALLOWED_APPLICATION_STATUSES = {"ready_to_apply", "opened"}
LEGAL_FIELD_PATTERN = re.compile(r"\b(visa|sponsor|sponsorship|authorized|authorised|eligibility|criminal|disability|veteran)\b", re.I)
SUBMIT_PATTERN = re.compile(r"\b(submit|send application|apply now|final)\b", re.I)
_OPEN_REVIEW_BROWSERS: list[Any] = []


@dataclass(frozen=True)
class FieldCandidate:
    key: str
    value: str
    reason: str


def assist_apply_application(db: Session, job: Job, user: User, *, browser_runner=None) -> AssistApplyResult:
    _validate_application(job)
    availability = check_job_availability(db, job)
    if availability.availability_status != "active":
        raise ValueError(f"Application assistance blocked because job is {availability.availability_status}. {availability.availability_reason or ''}".strip())
    _validate_application(job)

    started_at = utcnow()
    job.assisted_started_at = started_at
    job.last_apply_attempt_at = started_at
    db.commit()

    profile = get_profile(db, user)
    candidates = profile_field_candidates(user, profile)
    warnings = _safety_warnings(job)
    try:
        result = browser_runner(job.canonical_url, candidates) if browser_runner else run_playwright_assist(job.canonical_url, candidates)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc

    result.warnings[:] = [*warnings, *result.warnings]
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    job.application_status = "opened"
    db.commit()
    return result


def profile_field_candidates(user: User, profile) -> dict[str, FieldCandidate]:
    preferences = profile.preferences if profile is not None and profile.preferences else {}
    raw_values = {
        "email": user.email,
        "name": preferences.get("name") or preferences.get("full_name"),
        "phone": preferences.get("phone") or preferences.get("phone_number"),
        "location": profile.location_preference if profile is not None else preferences.get("location"),
        "linkedin": preferences.get("linkedin") or preferences.get("linkedin_url"),
        "portfolio": preferences.get("portfolio") or preferences.get("portfolio_url") or preferences.get("website"),
        "salary": preferences.get("salary_expectation") or preferences.get("salary"),
        "work_authorization": preferences.get("work_authorization"),
    }
    return {
        key: FieldCandidate(key=key, value=str(value).strip(), reason="Saved profile value")
        for key, value in raw_values.items()
        if value is not None and str(value).strip()
    }


def classify_form_field(label_text: str, input_type: str = "", autocomplete: str = "", name: str = "", placeholder: str = "") -> str | None:
    text = " ".join([label_text, input_type, autocomplete, name, placeholder]).lower()
    if not text.strip():
        return None
    if "linkedin" in text:
        return "linkedin"
    if "portfolio" in text or "website" in text:
        return "portfolio"
    if "email" in text:
        return "email"
    if "phone" in text or "mobile" in text or "tel" in text:
        return "phone"
    if "salary" in text or "compensation" in text:
        return "salary"
    if "location" in text or "city" in text or "address" in text:
        return "location"
    if LEGAL_FIELD_PATTERN.search(text):
        return "work_authorization"
    if "full name" in text or autocomplete == "name":
        return "name"
    if "first name" in text or "last name" in text:
        return None
    return None


def run_playwright_assist(url: str, candidates: dict[str, FieldCandidate]) -> AssistApplyResult:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Browser automation worker not available in this environment.") from exc

    headless = settings.app_env.lower() in {"production", "prod", "render"}
    filled: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            keep_open_for_review = not headless
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                if _captcha_visible(page):
                    warnings.append("Captcha detected; manual review required.")
                fields = page.locator("input, textarea, select").all()
                for field in fields:
                    try:
                        tag = field.evaluate("element => element.tagName.toLowerCase()")
                        input_type = str(field.get_attribute("type") or "").lower()
                        if input_type in {"hidden", "password", "submit", "button", "checkbox", "radio"}:
                            continue
                        label_text = _field_label(field)
                        key = classify_form_field(
                            label_text=label_text,
                            input_type=input_type,
                            autocomplete=str(field.get_attribute("autocomplete") or ""),
                            name=str(field.get_attribute("name") or ""),
                            placeholder=str(field.get_attribute("placeholder") or ""),
                        )
                        if input_type == "file":
                            warnings.append("CV upload field detected; upload is disabled until CV file support is configured.")
                            continue
                        if not key:
                            unfilled.append(label_text or str(field.get_attribute("name") or "unknown field"))
                            continue
                        if key == "work_authorization" and key not in candidates:
                            unfilled.append(label_text or "work authorization")
                            continue
                        candidate = candidates.get(key)
                        if candidate is None:
                            unfilled.append(label_text or key)
                            continue
                        if tag == "select":
                            unfilled.append(label_text or key)
                            continue
                        field.fill(candidate.value)
                        filled.append(label_text or key)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"Could not inspect or fill a field: {exc}")
                if _submit_visible(page):
                    warnings.append("Submit control detected and intentionally not clicked.")
                if keep_open_for_review:
                    warnings.append("Browser left open for manual review; close it after reviewing the application.")
                return AssistApplyResult(status="review_required", filled_fields=_dedupe(filled), unfilled_fields=_dedupe(unfilled), warnings=_dedupe(warnings), screenshot_path=None)
            finally:
                if keep_open_for_review:
                    _OPEN_REVIEW_BROWSERS.append(browser)
                else:
                    browser.close()
    except PlaywrightError as exc:
        logger.exception("apply_agent_browser_failed url=%s error=%s", url, exc)
        raise RuntimeError("Browser automation worker not available in this environment.") from exc


def _validate_application(job: Job) -> None:
    if job.application_status not in ALLOWED_APPLICATION_STATUSES:
        raise ValueError("Application must be ready_to_apply or opened before assisted apply.")
    if job.apply_strategy == "blocked" or job.apply_difficulty == "blocked":
        raise ValueError("Blocked apply routes cannot be assisted.")
    if not job.canonical_url:
        raise ValueError("Missing apply URL.")


def _safety_warnings(job: Job) -> list[str]:
    warnings = [
        "Assisted apply will not submit the application.",
        "Legal or eligibility questions are only filled when exact saved profile data exists.",
    ]
    if job.apply_difficulty == "hard":
        warnings.append("Hard application flow detected; expect manual review.")
    return warnings


def _captcha_visible(page) -> bool:
    text = page.locator("body").inner_text(timeout=3000).lower()
    return "captcha" in text or "recaptcha" in text or "hcaptcha" in text


def _submit_visible(page) -> bool:
    controls = page.locator("button, input[type=submit]").all()
    for control in controls:
        try:
            text = str(control.inner_text(timeout=500) or "")
        except Exception:  # noqa: BLE001
            text = ""
        label = " ".join([text, str(control.get_attribute("value") or "")])
        if SUBMIT_PATTERN.search(label):
            return True
    return False


def _field_label(field) -> str:
    return str(
        field.evaluate(
            """element => {
                const id = element.getAttribute('id');
                if (id) {
                    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                    if (label) return label.innerText || '';
                }
                const parentLabel = element.closest('label');
                if (parentLabel) return parentLabel.innerText || '';
                const described = element.getAttribute('aria-label') || element.getAttribute('placeholder') || element.getAttribute('name') || '';
                return described;
            }"""
        )
        or ""
    ).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result
