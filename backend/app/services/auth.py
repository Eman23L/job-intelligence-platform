from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AuthSession, Invite, User

MIN_PASSWORD_LENGTH = 8
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AuthError(ValueError):
    """A login, setup or invite problem that is safe to show to the user."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "$".join(["scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P), _b64(salt), _b64(digest)])


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, _unb64(expected))


def setup_required(db: Session) -> bool:
    return db.scalar(select(func.count(User.id)).where(User.password_hash.is_not(None))) == 0


def complete_owner_setup(db: Session, *, setup_code: str, email: str, password: str, name: str | None) -> User:
    if not settings.owner_setup_code:
        raise AuthError("Owner setup is not enabled on this server.")
    if not hmac.compare_digest(setup_code.strip(), settings.owner_setup_code):
        raise AuthError("That setup code is not right.")
    if not setup_required(db):
        raise AuthError("Setup has already been completed. Sign in instead.")
    email = _clean_email(email)
    _check_password(password)
    # Claim the original single-user account so existing CV, scores and saved jobs carry over.
    user = db.scalar(select(User).order_by(User.id))
    if user is None:
        user = User(email=email)
        db.add(user)
    elif user.email != email and db.scalar(select(User).where(User.email == email)) is not None:
        raise AuthError("Another account already uses that email.")
    user.email = email
    user.display_name = _clean_name(name)
    user.password_hash = hash_password(password)
    user.is_admin = True
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, *, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Email or password is incorrect.")
    return user


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=_sha256(token),
            expires_at=_now() + timedelta(days=settings.session_days),
        )
    )
    db.commit()
    return token


def user_for_token(db: Session, token: str) -> User | None:
    row = db.execute(
        select(User, AuthSession).join(AuthSession, AuthSession.user_id == User.id).where(AuthSession.token_hash == _sha256(token))
    ).first()
    if row is None:
        return None
    user, session = row
    now = _now()
    if _aware(session.expires_at) <= now:
        return None
    # Avoid a write on every request; a coarse "last seen" is enough.
    if session.last_used_at is None or now - _aware(session.last_used_at) > timedelta(hours=1):
        session.last_used_at = now
        db.commit()
    return user


def revoke_session(db: Session, token: str) -> None:
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == _sha256(token)))
    if session is not None:
        db.delete(session)
        db.commit()


def create_invite(db: Session, created_by: User, note: str | None = None) -> tuple[str, Invite]:
    code = secrets.token_urlsafe(18)
    invite = Invite(
        code_hash=_sha256(code),
        note=(note or "").strip()[:255] or None,
        created_by_user_id=created_by.id,
        expires_at=_now() + timedelta(days=settings.invite_days),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return code, invite


def redeem_invite(db: Session, *, code: str, email: str, password: str, name: str | None) -> User:
    invite = db.scalar(select(Invite).where(Invite.code_hash == _sha256(code.strip())))
    if invite is None or invite.used_at is not None or _aware(invite.expires_at) <= _now():
        raise AuthError("This invite link is invalid, expired or already used. Ask for a new one.")
    email = _clean_email(email)
    _check_password(password)
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise AuthError("An account with that email already exists. Sign in instead.")
    user = User(email=email, display_name=_clean_name(name), password_hash=hash_password(password))
    db.add(user)
    db.flush()
    invite.used_at = _now()
    invite.used_by_user_id = user.id
    db.commit()
    db.refresh(user)
    return user


def _clean_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value or "." not in value.split("@")[-1] or len(value) > 255:
        raise AuthError("Enter a valid email address.")
    return value


def _clean_name(name: str | None) -> str | None:
    value = (name or "").strip()
    return value[:120] or None


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; treat them as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
