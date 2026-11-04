"""Keep each person's CV current.

Every two days, as part of the daily Claude run, Claude checks for anything new to add: the notes the person
left on My CV, their GitHub, and what it knows of their recent work. It saves the updated CV here. The app keeps
the CV it replaced, rebuilds the PDF that gets attached to applications, and lets the person undo any change.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import CvVersion, User, UserProfile
from app.services.cv_pdf import render_cv_pdf
from app.services.profile import apply_cv_text, get_profile
from app.services.run_tracking import utcnow

REFRESH_EVERY = timedelta(days=2)
# A run that starts a little earlier in the day than last time still counts as due.
DUE_SLACK = timedelta(hours=6)
KEEP_VERSIONS = 30
# An update adds to the CV. One that cuts more than this much is refused, so a bad run can't gut it.
MIN_KEPT_SHARE = 0.7
MAX_CHANGE_LENGTH = 300


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def refresh_status(db: Session, user: User) -> dict[str, Any]:
    profile = get_profile(db, user)
    checked = _aware(profile.cv_checked_at) if profile else None
    has_cv = bool(profile and profile.cv_text.strip())
    due = has_cv and (checked is None or utcnow() - checked >= REFRESH_EVERY - DUE_SLACK)
    versions = db.scalars(select(CvVersion).where(CvVersion.user_id == user.id).order_by(CvVersion.id.desc()).limit(10)).all()
    return {
        "refresh_every_days": REFRESH_EVERY.days,
        "due": due,
        "checked_at": checked,
        "next_check_at": checked + REFRESH_EVERY if checked else None,
        "notes": (profile.cv_notes or "") if profile else "",
        "versions": versions,
    }


def save_notes(db: Session, user: User, notes: str) -> UserProfile:
    profile = _profile_with_cv(db, user)
    profile.cv_notes = notes.strip() or None
    db.commit()
    db.refresh(profile)
    return profile


def save_cv_text(db: Session, user: User, cv_text: str) -> UserProfile:
    """A CV edited by hand. The previous text is kept so it can be brought back."""
    profile = get_profile(db, user)
    if profile is None:
        profile = UserProfile(user_id=user.id, cv_text=cv_text)
        db.add(profile)
    elif profile.cv_text.strip() and not _same_text(profile.cv_text, cv_text):
        _keep_version(db, profile, changed_by="you", note="You edited your CV.")
    apply_cv_text(profile, cv_text)
    db.commit()
    _prune(db, user.id)
    db.refresh(profile)
    return profile


def refresh_cv(db: Session, user: User, cv_text: str | None, changes: list[str]) -> tuple[UserProfile, bool]:
    """Claude's CV refresh. Returns the profile and whether the CV changed.

    With no new text (or the same text), it only records that Claude checked.
    """
    profile = _profile_with_cv(db, user)
    now = utcnow()
    new_text = (cv_text or "").strip()
    if not new_text or _same_text(new_text, profile.cv_text):
        profile.cv_checked_at = now
        db.commit()
        db.refresh(profile)
        return profile, False

    changes = [" ".join(change.split())[:MAX_CHANGE_LENGTH] for change in changes if change and change.strip()]
    if not changes:
        raise ValueError("List what changed, one line per change.")
    _check_safe(profile.cv_text, new_text)
    pdf = render_cv_pdf(new_text)

    _keep_version(db, profile, changed_by="claude", note="\n".join(changes))
    apply_cv_text(profile, new_text)
    _store_pdf(profile, pdf, now)
    profile.cv_checked_at = now
    profile.cv_notes = None  # Claude has used them.
    db.commit()
    _prune(db, user.id)
    db.refresh(profile)
    return profile, True


def restore_version(db: Session, user: User, version_id: int) -> UserProfile:
    version = db.scalar(select(CvVersion).where(CvVersion.id == version_id, CvVersion.user_id == user.id))
    if version is None:
        raise LookupError("That version of your CV wasn't found.")
    profile = _profile_with_cv(db, user)
    _keep_version(db, profile, changed_by="undo", note=f"Went back to your CV from {version.created_at:%d %b %Y, %H:%M}.")
    apply_cv_text(profile, version.cv_text)
    if version.cv_file_bytes:
        profile.cv_file_name = version.cv_file_name
        profile.cv_file_bytes = version.cv_file_bytes
        profile.cv_file_mime_type = version.cv_file_mime_type
        profile.cv_file_size = len(version.cv_file_bytes)
        profile.cv_uploaded_at = utcnow()
    db.commit()
    _prune(db, user.id)
    db.refresh(profile)
    return profile


def rebuild_pdf(db: Session, user: User) -> UserProfile:
    """Make the attached CV file a PDF of the CV text."""
    profile = _profile_with_cv(db, user)
    pdf = render_cv_pdf(profile.cv_text)
    _keep_version(db, profile, changed_by="you", note="You made a new PDF from your CV text.")
    _store_pdf(profile, pdf, utcnow())
    db.commit()
    _prune(db, user.id)
    db.refresh(profile)
    return profile


def _profile_with_cv(db: Session, user: User) -> UserProfile:
    profile = get_profile(db, user)
    if profile is None or not profile.cv_text.strip():
        raise ValueError("Add your CV first.")
    return profile


def _keep_version(db: Session, profile: UserProfile, *, changed_by: str, note: str) -> None:
    db.add(
        CvVersion(
            user_id=profile.user_id,
            cv_text=profile.cv_text,
            cv_file_name=profile.cv_file_name,
            cv_file_bytes=profile.cv_file_bytes,
            cv_file_mime_type=profile.cv_file_mime_type,
            changed_by=changed_by,
            change_note=note[:2000],
        )
    )


def _prune(db: Session, user_id: int) -> None:
    keep = select(CvVersion.id).where(CvVersion.user_id == user_id).order_by(CvVersion.id.desc()).limit(KEEP_VERSIONS)
    db.execute(delete(CvVersion).where(CvVersion.user_id == user_id, CvVersion.id.not_in(keep)))
    db.commit()


def _store_pdf(profile: UserProfile, pdf: bytes, now: datetime) -> None:
    name = profile.cv_file_name if (profile.cv_file_name or "").lower().endswith(".pdf") else _pdf_name(profile.cv_text)
    profile.cv_file_name = name
    profile.cv_file_bytes = pdf
    profile.cv_file_mime_type = "application/pdf"
    profile.cv_file_size = len(pdf)
    profile.cv_file_path = None
    profile.cv_uploaded_at = now


def _pdf_name(cv_text: str) -> str:
    name = _first_line(cv_text)
    return f"{re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_') or 'My'}_CV.pdf"


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.split()) == " ".join(right.split())


def _check_safe(old: str, new: str) -> None:
    if _first_line(old).casefold() != _first_line(new).casefold():
        raise ValueError(f'The updated CV must still start with your name ("{_first_line(old)}").')
    if len(new) < MIN_KEPT_SHARE * len(old.strip()):
        raise ValueError("The updated CV is much shorter than the current one. An update should add to your CV, not cut it down.")
