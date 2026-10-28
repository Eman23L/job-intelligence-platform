from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import bearer_token, get_current_user, require_admin
from app.db.models import User
from app.db.session import get_db
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class AccountRead(BaseModel):
    id: int
    email: str
    display_name: str | None
    is_admin: bool


class AuthStatus(BaseModel):
    setup_required: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class SetupRequest(BaseModel):
    setup_code: str
    email: str
    password: str
    name: str | None = None


class RegisterRequest(BaseModel):
    invite_code: str
    email: str
    password: str
    name: str | None = None


class SessionResponse(BaseModel):
    token: str
    user: AccountRead


class InviteCreate(BaseModel):
    note: str | None = None


class InviteCreated(BaseModel):
    code: str
    expires_at: datetime


def _account(user: User) -> AccountRead:
    return AccountRead(id=user.id, email=user.email, display_name=user.display_name, is_admin=user.is_admin)


def _session(db: Session, user: User) -> SessionResponse:
    return SessionResponse(token=auth_service.create_session(db, user), user=_account(user))


def _bad_request(exc: auth_service.AuthError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/status", response_model=AuthStatus)
def auth_status(db: Session = Depends(get_db)):
    return AuthStatus(setup_required=auth_service.setup_required(db))


@router.post("/setup", response_model=SessionResponse)
def owner_setup(payload: SetupRequest, db: Session = Depends(get_db)):
    try:
        user = auth_service.complete_owner_setup(
            db, setup_code=payload.setup_code, email=payload.email, password=payload.password, name=payload.name
        )
    except auth_service.AuthError as exc:
        raise _bad_request(exc) from exc
    return _session(db, user)


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = auth_service.authenticate(db, email=payload.email, password=payload.password)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return _session(db, user)


@router.post("/register", response_model=SessionResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user = auth_service.redeem_invite(
            db, code=payload.invite_code, email=payload.email, password=payload.password, name=payload.name
        )
    except auth_service.AuthError as exc:
        raise _bad_request(exc) from exc
    return _session(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    token = bearer_token(authorization)
    if token:
        auth_service.revoke_session(db, token)


@router.get("/me", response_model=AccountRead)
def me(user: User = Depends(get_current_user)):
    return _account(user)


@router.post("/invites", response_model=InviteCreated)
def create_invite(payload: InviteCreate | None = None, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    code, invite = auth_service.create_invite(db, user, payload.note if payload else None)
    return InviteCreated(code=code, expires_at=invite.expires_at)
