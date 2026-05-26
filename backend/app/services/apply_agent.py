from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Job, JobScore, User
from app.db.session import SessionLocal
from app.schemas.database import AssistApplyResult
from app.services.browser_automation import chromium_diagnostics, chromium_executable_path, validate_browser_automation_availability
from app.services.job_availability import check_job_availability
from app.services.profile import get_profile
from app.services.run_tracking import utcnow

logger = logging.getLogger(__name__)

ALLOWED_APPLICATION_STATUSES = {"ready_to_apply", "opened"}
ASSIST_MODES = {"review_only", "submit_with_confirmation"}
LEGAL_FIELD_PATTERN = re.compile(r"\b(visa|sponsor|sponsorship|authorized|authorised|eligibility|criminal|disability|veteran)\b", re.I)
SUBMIT_PATTERN = re.compile(r"\b(submit|send application|apply now|final)\b", re.I)
_OPEN_REVIEW_BROWSERS: list[Any] = []
DEBUG_ARTIFACT_DIR = Path("backend/runtime/apply_debug")
JOBSERVE_REQUIRED_DROPDOWN_PATTERNS = {
    "availability_notice": [r"availability", r"notice"],
    "salary_expectation_gbp": [r"salary expectation", r"salary"],
    "travel_distance_miles": [r"travel distance", r"travel"],
    "work_authorization": [r"working status", r"work status", r"status in uk", r"eligible.*uk"],
}


class BrowserAutomationError(RuntimeError):
    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


@dataclass(frozen=True)
class FieldCandidate:
    key: str
    value: str
    reason: str


def assist_apply_application(db: Session, job: Job, user: User, *, mode: str = "review_only", debug_mode: bool = False, browser_runner=None) -> AssistApplyResult:
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
        if browser_runner:
            result = browser_runner(job.canonical_url, candidates, profile, mode, job.apply_strategy)
        elif debug_mode:
            result = run_playwright_assist(job.canonical_url, candidates, profile=profile, mode=mode, apply_strategy=job.apply_strategy, debug_mode=True)
        else:
            result = run_playwright_assist(job.canonical_url, candidates, profile=profile, mode=mode, apply_strategy=job.apply_strategy)
    except BrowserAutomationError:
        raise
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc

    result.warnings[:] = [*warnings, *result.warnings]
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    logger.info(
        "assist_apply_result_persisted application_id=%s mode=%s debug_mode=%s status=%s screenshots=%s html_snapshots=%s debug_steps=%s final_url=%s final_error=%s",
        job.id,
        mode,
        debug_mode,
        result.status,
        len(result.screenshot_paths),
        len(result.html_snapshot_paths),
        len(result.debug_steps),
        result.final_url,
        result.final_error,
    )
    if result.submitted:
        job.application_status = "applied"
        job.applied_at = utcnow()
    else:
        job.application_status = "opened"
    db.commit()
    return result


def run_assist_apply_background(application_id: int, user_id: int, mode: str = "review_only", debug_mode: bool = False) -> None:
    logger.info(
        "assist_apply_worker_start service_type=%s application_id=%s user_id=%s mode=%s debug_mode=%s",
        settings.service_type,
        application_id,
        user_id,
        mode,
        debug_mode,
    )
    with SessionLocal() as db:
        job = db.get(Job, application_id)
        user = db.get(User, user_id)
        if job is None or user is None:
            logger.error("assist_apply_worker_missing_record service_type=%s application_id=%s user_id=%s", settings.service_type, application_id, user_id)
            return
        try:
            assist_apply_application(db, job, user, mode=mode, debug_mode=debug_mode)
        except BrowserAutomationError as exc:
            _store_assist_failure(db, job, exc.message, error=exc.error)
            logger.exception(
                "assist_apply_worker_browser_error service_type=%s application_id=%s error_code=%s error=%s",
                settings.service_type,
                application_id,
                exc.error,
                exc.message,
            )
        except Exception as exc:  # noqa: BLE001
            _store_assist_failure(db, job, str(exc))
            logger.exception("assist_apply_worker_failed service_type=%s application_id=%s error=%s", settings.service_type, application_id, exc)
        else:
            logger.info("assist_apply_worker_completed service_type=%s application_id=%s", settings.service_type, application_id)


def queued_assist_apply_result() -> AssistApplyResult:
    return AssistApplyResult(
        status="queued",
        filled_fields=[],
        unfilled_fields=[],
        unfilled_required_fields=[],
        uploaded_cv=False,
        submitted=False,
        warnings=["Assisted apply queued on the browser automation worker."],
        screenshot_path=None,
    )


def _store_assist_failure(db: Session, job: Job, message: str, *, error: str | None = None) -> None:
    result = AssistApplyResult(
        status="failed",
        filled_fields=[],
        unfilled_fields=[],
        unfilled_required_fields=[],
        uploaded_cv=False,
        submitted=False,
        warnings=[f"{error}: {message}" if error else message],
        screenshot_path=None,
    )
    job.assisted_result = result.model_dump()
    job.assisted_warnings = result.warnings
    job.last_apply_attempt_at = utcnow()
    db.commit()


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


def run_playwright_assist(
    url: str,
    candidates: dict[str, FieldCandidate],
    *,
    profile=None,
    mode: str = "review_only",
    apply_strategy: str = "unknown",
    debug_mode: bool = False,
) -> AssistApplyResult:
    diagnostics = chromium_diagnostics()
    logger.info(
        "assist_apply_browser_preflight service_type=%s playwright_browsers_path=%s chromium_executable_path=%s chromium_file_exists=%s chromium_file_executable=%s",
        settings.service_type,
        diagnostics["playwright_browsers_path"],
        diagnostics["chromium_executable_path"],
        diagnostics["chromium_file_exists"],
        diagnostics["chromium_file_executable"],
    )
    availability = validate_browser_automation_availability(require_worker=settings.queue_enabled)
    if not availability.available:
        raise BrowserAutomationError(availability.error or "worker_unavailable", availability.message or "Browser automation worker is offline.")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise BrowserAutomationError("playwright_not_installed", "Playwright is not installed in this environment.") from exc

    headless = settings.app_env.lower() in {"production", "prod", "render"}
    filled: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    try:
        with sync_playwright() as playwright:
            executable_path = chromium_executable_path()
            launch_options = {"headless": headless}
            if executable_path:
                launch_options["executable_path"] = executable_path
            browser = playwright.chromium.launch(**launch_options)
            keep_open_for_review = not headless
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                if apply_strategy == "jobserve_apply_easy":
                    return _run_jobserve_modal(page, browser, candidates, profile, mode=mode, keep_open_for_review=keep_open_for_review, debug_mode=debug_mode)
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
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise BrowserAutomationError("chromium_not_installed", "Playwright Chromium is not installed in this environment.") from exc
        raise BrowserAutomationError("worker_unavailable", "Browser automation worker is offline.") from exc


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


class _ApplyDebugRecorder:
    def __init__(self, page, browser, *, enabled: bool, prefix: str = "jobserve") -> None:
        self.page = page
        self.browser = browser
        self.enabled = enabled
        self.started_ms = int(time.time() * 1000)
        self.dir = DEBUG_ARTIFACT_DIR / str(self.started_ms)
        self.prefix = prefix
        self.screenshot_paths: list[str] = []
        self.html_snapshot_paths: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self.final_error: str | None = None
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def step(self, name: str, **extra: Any) -> None:
        state: dict[str, Any] = {"step": name, **_page_state(self.page, self.browser), **extra}
        self.steps.append(state)
        logger.info("jobserve_apply_step %s", state)

    def screenshot(self, name: str) -> None:
        if not self.enabled:
            return
        path = self.dir / f"{len(self.screenshot_paths) + 1:02d}_{_slug(name)}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True, timeout=8000)
            self.screenshot_paths.append(str(path))
            logger.info("jobserve_apply_screenshot_saved path=%s", path)
        except Exception as exc:  # noqa: BLE001
            self.step(f"screenshot_failed_{name}", error=str(exc))

    def html(self, name: str, target=None) -> str | None:
        if not self.enabled:
            return None
        path = self.dir / f"{len(self.html_snapshot_paths) + 1:02d}_{_slug(name)}.html"
        try:
            html = (target or self.page).content()
            path.write_text(html, encoding="utf-8")
            self.html_snapshot_paths.append(str(path))
            logger.info("jobserve_apply_html_snapshot_saved path=%s", path)
            return str(path)
        except Exception as exc:  # noqa: BLE001
            self.step(f"html_snapshot_failed_{name}", error=str(exc))
            return None

    def result_kwargs(self, target=None) -> dict[str, Any]:
        target_page = target or self.page
        inventory = _inventory_browser(target_page, self.browser)
        return {
            "screenshot_paths": self.screenshot_paths,
            "screenshot_urls": [_artifact_url(path) for path in self.screenshot_paths],
            "html_snapshot_paths": self.html_snapshot_paths,
            "html_snapshot_urls": [_artifact_url(path) for path in self.html_snapshot_paths],
            "detected_buttons": inventory["buttons"],
            "detected_fields": inventory["fields"],
            "detected_selects": inventory["selects"],
            "detected_iframes": inventory["iframes"],
            "debug_steps": self.steps,
            "final_url": _safe_url(target_page),
            "final_error": self.final_error,
            "debug_mode": self.enabled,
        }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:80] or "artifact"


def _artifact_url(path: str) -> str:
    try:
        relative = Path(path).resolve().relative_to(DEBUG_ARTIFACT_DIR.resolve())
    except Exception:  # noqa: BLE001
        return ""
    return f"/applications/debug-artifacts/{relative.as_posix()}"


def _safe_url(page) -> str | None:
    try:
        return page.url
    except Exception:  # noqa: BLE001
        return None


def _safe_title(page) -> str | None:
    try:
        return page.title()
    except Exception:  # noqa: BLE001
        return None


def _page_state(page, browser) -> dict[str, Any]:
    return {
        "current_url": _safe_url(page),
        "page_title": _safe_title(page),
        "iframe_count": len(getattr(page, "frames", []) or []),
        "popup_window_count": len(getattr(page.context, "pages", []) or []) if getattr(page, "context", None) else None,
    }


def _latest_page(browser):
    try:
        pages = browser.contexts[0].pages
        return pages[-1] if pages else None
    except Exception:  # noqa: BLE001
        return None


def _context_name(context) -> str:
    try:
        return f"frame:{context.url}"
    except Exception:  # noqa: BLE001
        return "page"


def _all_contexts(page, browser) -> list[Any]:
    contexts: list[Any] = []
    try:
        for candidate_page in page.context.pages:
            contexts.append(candidate_page)
            contexts.extend(frame for frame in candidate_page.frames if frame is not candidate_page.main_frame)
    except Exception:  # noqa: BLE001
        contexts.append(page)
    return contexts


def _find_apply_target(page, browser):
    for context in _all_contexts(page, browser):
        locators = [
            context.get_by_role("button", name=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.get_by_role("link", name=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.locator("button, input[type=button], input[type=submit], a").filter(has_text=re.compile(r"^apply(\s+now)?$", re.I)).first,
            context.get_by_text(re.compile(r"^apply\b", re.I)).first,
        ]
        for locator in locators:
            try:
                if locator.count() and locator.is_visible(timeout=1000):
                    return locator
            except Exception:  # noqa: BLE001
                continue
    return None


def _find_jobserve_form_context(page, browser):
    deadline = time.time() + 12
    while time.time() < deadline:
        for context in _all_contexts(page, browser):
            try:
                has_modal = context.locator("[role=dialog], .modal, #ApplyModal").count() > 0
                has_job_application_text = context.get_by_text(re.compile(r"job application", re.I)).count() > 0
                has_fields = context.locator("input:not([type=hidden]), select, textarea").count() > 0
                if (has_modal or has_job_application_text) and has_fields:
                    return context
                if has_fields and context.locator("input[type=file], select").count() > 0:
                    return context
            except Exception:  # noqa: BLE001
                continue
        page.wait_for_timeout(500)
    return None


def _inventory_browser(page, browser) -> dict[str, list[dict[str, Any]]]:
    buttons: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    selects: list[dict[str, Any]] = []
    iframes: list[dict[str, Any]] = []
    for context in _all_contexts(page, browser):
        inventory = _inventory_context(context)
        buttons.extend(inventory["buttons"])
        fields.extend(inventory["fields"])
        selects.extend(inventory["selects"])
        iframes.extend(inventory["iframes"])
    return {"buttons": buttons[:20], "fields": fields, "selects": selects, "iframes": iframes}


def _inventory_context(context) -> dict[str, list[dict[str, Any]]]:
    try:
        return context.evaluate(
            """() => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const labelFor = (el) => {
                    const id = el.getAttribute('id');
                    if (id) {
                        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (label) return label.innerText.trim();
                    }
                    const parent = el.closest('label');
                    if (parent) return parent.innerText.trim();
                    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
                };
                const selectorish = (el) => {
                    const bits = [el.tagName.toLowerCase()];
                    if (el.id) bits.push(`#${el.id}`);
                    if (el.getAttribute('name')) bits.push(`[name="${el.getAttribute('name')}"]`);
                    if (el.className && typeof el.className === 'string') bits.push('.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.'));
                    return bits.join('');
                };
                const buttons = [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
                    .filter(visible)
                    .slice(0, 20)
                    .map((el) => ({
                        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
                        tag: el.tagName.toLowerCase(),
                        selector: selectorish(el),
                        href: el.href || null,
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        type: el.getAttribute('type') || null
                    }));
                const fields = [...document.querySelectorAll('input, select, textarea')]
                    .filter(visible)
                    .map((el) => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type') || null,
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        placeholder: el.getAttribute('placeholder') || null,
                        label: labelFor(el),
                        value: el.value || '',
                        selector: selectorish(el),
                        required: Boolean(el.required),
                        options: el.tagName.toLowerCase() === 'select' ? [...el.options].map((option) => option.text.trim()).filter(Boolean).slice(0, 50) : []
                    }));
                const selects = fields.filter((field) => field.tag === 'select');
                const file_inputs = fields.filter((field) => field.type === 'file');
                const iframes = [...document.querySelectorAll('iframe')]
                    .map((el) => ({ id: el.id || null, name: el.name || null, src: el.src || null, title: el.title || null }));
                return { buttons, fields, selects, file_inputs, iframes };
            }"""
        )
    except Exception as exc:  # noqa: BLE001
        return {"buttons": [], "fields": [], "selects": [], "file_inputs": [], "iframes": [{"error": str(exc), "context": _context_name(context)}]}


def _detect_jobserve_dropdowns(selects: list[dict[str, Any]]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for key, patterns in JOBSERVE_REQUIRED_DROPDOWN_PATTERNS.items():
        result[key] = any(
            any(re.search(pattern, " ".join(str(select.get(part) or "") for part in ["label", "name", "id", "placeholder"]), re.I) for pattern in patterns)
            for select in selects
        )
    return result


def _run_jobserve_modal(page, browser, candidates: dict[str, FieldCandidate], profile, *, mode: str, keep_open_for_review: bool, debug_mode: bool = False) -> AssistApplyResult:
    filled: list[str] = []
    unfilled_required: list[str] = []
    unfilled: list[str] = []
    warnings: list[str] = []
    uploaded_cv = False
    submitted = False
    status = "review_required"
    debug = _ApplyDebugRecorder(page, browser, enabled=debug_mode)
    debug.step("initial_page_loaded")
    debug.screenshot("initial_page_loaded")

    apply_target = _find_apply_target(page, browser)
    debug.step("apply_button_lookup", apply_button_found=apply_target is not None, detected_buttons=_inventory_browser(page, browser)["buttons"][:20])
    if apply_target is None:
        debug.final_error = "No visible JobServe Apply button/link found."
        debug.html("no_apply_button_found")
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe([*warnings, debug.final_error]),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            **debug.result_kwargs(),
        )

    pages_before = len(page.context.pages)
    apply_target.click(timeout=8000)
    page.wait_for_timeout(1000)
    target_page = _latest_page(browser) or page
    if len(page.context.pages) <= pages_before:
        target_page = page
    else:
        target_page.wait_for_load_state("domcontentloaded", timeout=15000)
    debug.page = target_page
    debug.step("apply_button_clicked", apply_button_clicked=True, popup_opened=target_page is not page)

    target_page.wait_for_timeout(3500 if debug_mode else 1200)
    debug.screenshot("after_clicking_apply")
    context = _find_jobserve_form_context(target_page, browser)
    debug.step(
        "modal_wait_complete",
        modal_found=context is not None,
        job_application_modal_found=context is not None,
        target_context=_context_name(context) if context else None,
    )
    debug.screenshot("after_modal_wait")
    if context is None:
        debug.final_error = "Job Application modal/form not found after clicking Apply."
        debug.html("modal_not_found", target_page)
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe([*warnings, debug.final_error]),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            **debug.result_kwargs(target_page),
        )

    form_inventory = _inventory_context(context)
    debug.step(
        "before_filling",
        form_fields_detected=len(form_inventory["fields"]),
        cv_upload_input_detected=bool(form_inventory["file_inputs"]),
        required_dropdowns_detected=_detect_jobserve_dropdowns(form_inventory["selects"]),
        detected_fields=form_inventory["fields"],
        detected_selects=form_inventory["selects"],
    )
    debug.screenshot("before_filling")
    if not form_inventory["fields"]:
        debug.final_error = "No visible application fields detected in JobServe apply form."
        debug.html("no_fields_detected", context)
        return AssistApplyResult(
            status="review_required",
            filled_fields=[],
            unfilled_fields=[],
            unfilled_required_fields=[],
            uploaded_cv=False,
            submitted=False,
            warnings=_dedupe([*warnings, debug.final_error]),
            screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
            **debug.result_kwargs(target_page),
        )

    if _captcha_visible(target_page):
        warnings.append("Captcha detected; manual review required.")

    _disable_jobserve_account_options(context, warnings)
    required = ["email"]
    for key, patterns in {
        "email": [r"email address", r"email"],
        "first_name": [r"first name"],
        "last_name": [r"last name", r"surname"],
        "phone": [r"phone", r"mobile"],
    }.items():
        label = _fill_by_label_patterns(context, patterns, candidates.get(key))
        if label:
            filled.append(label)
        elif key in required:
            unfilled_required.append(key.replace("_", " "))

    if candidates.get("work_authorization"):
        if _select_work_status(context, candidates["work_authorization"].value):
            filled.append("Working status in UK")
        else:
            unfilled.append("Working status in UK")
    else:
        unfilled_required.append("working status in UK")

    _handle_required_dropdown(
        context,
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
        context,
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
        context,
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

    if not form_inventory["file_inputs"]:
        debug.final_error = debug.final_error or "CV upload input not found in JobServe apply form."
        debug.html("cv_upload_input_not_found", context)
    if profile and profile.cv_file_path:
        try:
            file_input = context.locator("input[type=file]").first
            file_input.set_input_files(profile.cv_file_path, timeout=5000)
            uploaded_cv = True
            filled.append("CV upload")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not upload CV: {exc}")
            unfilled_required.append("CV upload")
            debug.final_error = debug.final_error or "CV upload input not found or could not be populated."
            debug.html("cv_upload_input_not_found", context)
    else:
        unfilled_required.append("CV upload")

    debug.step(
        "after_filling",
        filled_fields=_dedupe(filled),
        unfilled_fields=_dedupe(unfilled),
        unfilled_required_fields=_dedupe(unfilled_required),
        uploaded_cv=uploaded_cv,
    )
    debug.screenshot("after_filling")
    _disable_jobserve_account_options(context, warnings)
    if mode == "submit_with_confirmation":
        debug.screenshot("before_submit")
        if unfilled_required:
            raise RuntimeError(f"Required fields missing: {', '.join(_dedupe(unfilled_required))}")
        apply_button = _jobserve_apply_button(context)
        if apply_button.count() == 0:
            debug.final_error = "Submit button not found in JobServe apply form."
            debug.html("submit_button_not_found", context)
            raise RuntimeError(debug.final_error)
        apply_button.click(timeout=8000)
        success = target_page.get_by_text("Your application has been submitted.").first
        success.wait_for(timeout=12000)
        submitted = True
        status = "submitted"
        _close_modal(target_page)
    else:
        warnings.append("Review-only mode: JobServe Apply button was intentionally not clicked.")
        if debug_mode:
            warnings.append("Debug mode: submit is disabled and browser state was inventoried.")
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
        screenshot_path=debug.screenshot_paths[-1] if debug.screenshot_paths else None,
        **debug.result_kwargs(target_page),
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
