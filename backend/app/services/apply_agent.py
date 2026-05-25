from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, JobScore, User
from app.schemas.database import AssistApplyResult
from app.services.job_availability import check_job_availability
from app.services.profile import get_profile
from app.services.run_tracking import utcnow

logger = logging.getLogger(__name__)

ALLOWED_APPLICATION_STATUSES = {"ready_to_apply", "opened"}
ASSIST_MODES = {"review_only", "submit_with_confirmation"}
LEGAL_FIELD_PATTERN = re.compile(r"\b(visa|sponsor|sponsorship|authorized|authorised|eligibility|criminal|disability|veteran)\b", re.I)
SUBMIT_PATTERN = re.compile(r"\b(submit|send application|apply now|final)\b", re.I)
_OPEN_REVIEW_BROWSERS: list[Any] = []


@dataclass(frozen=True)
class FieldCandidate:
    key: str
    value: str
    reason: str


def assist_apply_application(db: Session, job: Job, user: User, *, mode: str = "review_only", browser_runner=None) -> AssistApplyResult:
    if mode not in ASSIST_MODES:
        raise ValueError("Invalid assisted apply mode.")
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
    if mode == "submit_with_confirmation":
        _validate_jobserve_submit(db, job, user, profile)
    candidates = profile_field_candidates(user, profile)
    warnings = _safety_warnings(job)
    try:
        result = (
            browser_runner(job.canonical_url, candidates, profile, mode, job.apply_strategy)
            if browser_runner
            else run_playwright_assist(job.canonical_url, candidates, profile=profile, mode=mode, apply_strategy=job.apply_strategy)
        )
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc

    result.warnings[:] = [*warnings, *result.warnings]
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    if result.submitted:
        job.application_status = "applied"
        job.applied_at = utcnow()
    else:
        job.application_status = "opened"
    db.commit()
    return result


def profile_field_candidates(user: User, profile) -> dict[str, FieldCandidate]:
    preferences = profile.preferences if profile is not None and profile.preferences else {}
    raw_values = {
        "email": getattr(profile, "email", None) or user.email,
        "first_name": getattr(profile, "first_name", None),
        "last_name": getattr(profile, "last_name", None),
        "name": preferences.get("name") or preferences.get("full_name"),
        "phone": getattr(profile, "phone", None) or preferences.get("phone") or preferences.get("phone_number"),
        "address": getattr(profile, "address", None),
        "country": getattr(profile, "country", None),
        "location": profile.location_preference if profile is not None else preferences.get("location"),
        "linkedin": preferences.get("linkedin") or preferences.get("linkedin_url"),
        "portfolio": preferences.get("portfolio") or preferences.get("portfolio_url") or preferences.get("website"),
        "salary": getattr(profile, "salary_expectation", None) or preferences.get("salary_expectation") or preferences.get("salary"),
        "travel_distance": getattr(profile, "travel_distance", None),
        "availability_notice": getattr(profile, "availability_notice", None),
        "salary_expectation_gbp": getattr(profile, "salary_expectation_gbp", None),
        "travel_distance_miles": getattr(profile, "travel_distance_miles", None),
        "work_authorization": getattr(profile, "work_status_uk", None) or preferences.get("work_authorization"),
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
    if "first name" in text or autocomplete == "given-name":
        return "first_name"
    if "last name" in text or "surname" in text or autocomplete == "family-name":
        return "last_name"
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
    return None


def run_playwright_assist(url: str, candidates: dict[str, FieldCandidate], *, profile=None, mode: str = "review_only", apply_strategy: str = "unknown") -> AssistApplyResult:
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
                if apply_strategy == "jobserve_apply_easy":
                    return _run_jobserve_modal(page, browser, candidates, profile, mode=mode, keep_open_for_review=keep_open_for_review)
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
                return AssistApplyResult(status="review_required", filled_fields=_dedupe(filled), unfilled_fields=_dedupe(unfilled), unfilled_required_fields=[], uploaded_cv=False, submitted=False, warnings=_dedupe(warnings), screenshot_path=None)
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


def _validate_jobserve_submit(db: Session, job: Job, user: User, profile) -> None:
    if job.apply_strategy != "jobserve_apply_easy":
        raise ValueError("Submit with confirmation is only available for JobServe easy apply.")
    if not profile or not profile.cv_file_path:
        raise ValueError("Saved CV file is required before submitting a JobServe application.")
    if not (getattr(profile, "email", None) or ""):
        raise ValueError("Email is required before submitting a JobServe application.")
    if not getattr(profile, "availability_notice", None):
        raise ValueError("Availability notice missing")
    if getattr(profile, "salary_expectation_gbp", None) is None:
        raise ValueError("Salary expectation missing")
    if getattr(profile, "travel_distance_miles", None) is None:
        raise ValueError("Travel distance missing")
    score = db.scalar(select(JobScore).where(JobScore.job_id == job.id, JobScore.user_id == user.id).order_by(JobScore.scored_at.desc()))
    threshold = int(getattr(profile, "minimum_apply_score", None) or 80)
    total = float(score.total_score) if score is not None and score.total_score is not None else 0.0
    if total < threshold:
        raise ValueError(f"Job score {round(total):g} is below your apply threshold of {threshold}.")


def _safety_warnings(job: Job) -> list[str]:
    warnings = [
        "Assisted apply will not submit the application.",
        "Legal or eligibility questions are only filled when exact saved profile data exists.",
    ]
    if job.apply_difficulty == "hard":
        warnings.append("Hard application flow detected; expect manual review.")
    return warnings


def _run_jobserve_modal(page, browser, candidates: dict[str, FieldCandidate], profile, *, mode: str, keep_open_for_review: bool) -> AssistApplyResult:
    filled: list[str] = []
    unfilled_required: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    uploaded_cv = False
    submitted = False
    status = "review_required"
    page.get_by_text(re.compile(r"^apply\b", re.I)).first.click(timeout=8000)
    modal = page.locator("[role=dialog], .modal, #ApplyModal, text=Job Application").first
    modal.wait_for(timeout=10000)
    if _captcha_visible(page):
        warnings.append("Captcha detected; manual review required.")

    _disable_jobserve_account_options(page, warnings)
    required = ["email"]
    for key, patterns in {
        "email": [r"email address", r"email"],
        "first_name": [r"first name"],
        "last_name": [r"last name", r"surname"],
        "phone": [r"phone", r"mobile"],
    }.items():
        label = _fill_by_label_patterns(page, patterns, candidates.get(key))
        if label:
            filled.append(label)
        elif key in required:
            unfilled_required.append(key.replace("_", " "))

    if candidates.get("work_authorization"):
        if _select_work_status(page, candidates["work_authorization"].value):
            filled.append("Working status in UK")
        else:
            unfilled.append("Working status in UK")
    else:
        unfilled_required.append("working status in UK")

    _handle_required_dropdown(
        page,
        candidates,
        "availability_notice",
        [r"availability", r"notice"],
        lambda value: value,
        "Availability notice",
        "Availability notice missing",
        filled,
        unfilled_required,
        warnings,
    )
    _handle_required_dropdown(
        page,
        candidates,
        "salary_expectation_gbp",
        [r"salary expectation", r"salary"],
        salary_range_label,
        "Salary expectation",
        "Salary expectation missing",
        filled,
        unfilled_required,
        warnings,
        no_match_warning="Could not match salary range",
    )
    _handle_required_dropdown(
        page,
        candidates,
        "travel_distance_miles",
        [r"travel distance", r"travel"],
        travel_distance_label,
        "Travel distance",
        "Travel distance missing",
        filled,
        unfilled_required,
        warnings,
    )

    if profile and profile.cv_file_path:
        try:
            file_input = page.locator("input[type=file]").first
            file_input.set_input_files(profile.cv_file_path, timeout=5000)
            uploaded_cv = True
            filled.append("CV upload")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not upload CV: {exc}")
            unfilled_required.append("CV upload")
    else:
        unfilled_required.append("CV upload")

    _disable_jobserve_account_options(page, warnings)
    if mode == "submit_with_confirmation":
        if unfilled_required:
            raise RuntimeError(f"Required fields missing: {', '.join(_dedupe(unfilled_required))}")
        apply_button = _jobserve_apply_button(page)
        apply_button.click(timeout=8000)
        success = page.get_by_text("Your application has been submitted.").first
        success.wait_for(timeout=12000)
        submitted = True
        status = "submitted"
        _close_modal(page)
    else:
        warnings.append("Review-only mode: JobServe Apply button was intentionally not clicked.")
        if keep_open_for_review:
            warnings.append("Browser left open for manual review; close it after reviewing the application.")

    return AssistApplyResult(
        status=status,
        filled_fields=_dedupe(filled),
        unfilled_fields=_dedupe(unfilled),
        unfilled_required_fields=_dedupe(unfilled_required),
        uploaded_cv=uploaded_cv,
        submitted=submitted,
        warnings=_dedupe(warnings),
        screenshot_path=None,
    )


def _fill_by_label_patterns(page, patterns: list[str], candidate: FieldCandidate | None) -> str | None:
    if candidate is None:
        return None
    for pattern in patterns:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        try:
            locator.fill(candidate.value, timeout=1500)
            return pattern
        except Exception:  # noqa: BLE001
            continue
    return None


def _select_work_status(page, value: str) -> bool:
    for pattern in [r"working status", r"work status", r"status in uk", r"eligible.*uk"]:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        try:
            locator.select_option(label=re.compile("Citizen|Permanent|Eligible|No sponsorship|UK", re.I), timeout=1500)
            return True
        except Exception:  # noqa: BLE001
            try:
                locator.fill(value, timeout=1500)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _handle_required_dropdown(
    page,
    candidates: dict[str, FieldCandidate],
    key: str,
    label_patterns: list[str],
    matcher,
    field_name: str,
    missing_warning: str,
    filled: list[str],
    unfilled_required: list[str],
    warnings: list[str],
    *,
    no_match_warning: str | None = None,
) -> None:
    candidate = candidates.get(key)
    if candidate is None:
        warnings.append(missing_warning)
        unfilled_required.append(field_name)
        return
    option = matcher(candidate.value)
    if option is None:
        warnings.append(no_match_warning or f"Could not match {field_name.lower()}")
        unfilled_required.append(field_name)
        return
    if _select_dropdown_by_label_patterns(page, label_patterns, option):
        filled.append(field_name)
        return
    warnings.append(f"Could not fill {field_name.lower()}")
    unfilled_required.append(field_name)


def _select_dropdown_by_label_patterns(page, label_patterns: list[str], visible_text: str) -> bool:
    for pattern in label_patterns:
        locator = page.get_by_label(re.compile(pattern, re.I)).first
        try:
            locator.select_option(label=visible_text, timeout=1500)
            return True
        except Exception:  # noqa: BLE001
            try:
                locator.select_option(label=re.compile(re.escape(visible_text), re.I), timeout=1500)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def salary_range_label(value: str) -> str | None:
    amount = _int_value(value)
    if amount is None:
        return None
    ranges = [
        (0, 15000, "0 - £15,000"),
        (15000, 20000, "£15,000 - £20,000"),
        (20000, 25000, "£20,000 - £25,000"),
        (25000, 30000, "£25,000 - £30,000"),
        (30000, 40000, "£30,000 - £40,000"),
        (40000, 50000, "£40,000 - £50,000"),
        (50000, 75000, "£50,000 - £75,000"),
        (75000, 100000, "£75,000 - £100,000"),
    ]
    for lower, upper, label in ranges:
        if lower <= amount <= upper:
            return label
    if amount > 100000:
        return "Above £100,000"
    return None


def travel_distance_label(value: str) -> str | None:
    miles = _int_value(value)
    if miles is None:
        return None
    if miles <= 5:
        return "0 to 5"
    if miles <= 15:
        return "6 to 15"
    if miles <= 30:
        return "16 to 30"
    if miles <= 50:
        return "31 to 50"
    return "50+"


def _int_value(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", str(value))
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def _disable_jobserve_account_options(page, warnings: list[str]) -> None:
    for text in ["register a Job Seeker account", "make my CV searchable", "job alerts", "create an account"]:
        controls = page.get_by_label(re.compile(text, re.I)).all()
        for control in controls:
            try:
                if control.is_checked():
                    control.uncheck()
                    warnings.append(f"Disabled option: {text}.")
            except Exception:  # noqa: BLE001
                continue


def _jobserve_apply_button(page):
    buttons = page.get_by_role("button", name=re.compile(r"^apply$", re.I)).all()
    if buttons:
        return buttons[-1]
    return page.locator("input[type=submit], button[type=submit]").last


def _close_modal(page) -> None:
    for locator in [page.get_by_role("button", name=re.compile(r"close", re.I)).first, page.locator(".modal button.close").first]:
        try:
            locator.click(timeout=1000)
            return
        except Exception:  # noqa: BLE001
            continue


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
