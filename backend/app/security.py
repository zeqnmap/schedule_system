"""Session authentication and application role rules."""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import database, models

SESSION_TTL = 60 * 60 * 12
SESSION_SECRET = "change-this-session-secret"
OWNER_ROLE = "owner"
ADMIN_ROLE = "admin"
USER_ROLE = "user"
_LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)
LOGIN_WINDOW = 15 * 60
LOGIN_LIMIT = 8


def login_allowed(login: str) -> bool:
    now = time.time()
    failures = _LOGIN_FAILURES[login.casefold()]
    while failures and now - failures[0] > LOGIN_WINDOW:
        failures.popleft()
    return len(failures) < LOGIN_LIMIT


def register_login_failure(login: str) -> None:
    _LOGIN_FAILURES[login.casefold()].append(time.time())


def clear_login_failures(login: str) -> None:
    _LOGIN_FAILURES.pop(login.casefold(), None)


def configure(secret: str) -> None:
    global SESSION_SECRET
    SESSION_SECRET = secret


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected = stored_hash.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
        return hmac.compare_digest(actual, expected)
    except ValueError:
        return False


def make_session(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def current_user(request: Request, db: Session) -> models.User:
    token = request.cookies.get("schedule_session", "")
    try:
        user_id, issued_at, signature = token.split(":", 2)
        payload = f"{user_id}:{issued_at}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or time.time() - int(issued_at) > SESSION_TTL:
            raise ValueError
        user = db.query(models.User).filter_by(id=int(user_id), is_active=True).first()
        if not user:
            raise ValueError
        return user
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Требуется авторизация")


def require_user(request: Request, db: Session = Depends(database.get_db)):
    return current_user(request, db)


def user_role(user: models.User) -> str:
    role = (user.role or "").strip().lower()
    return role if role in {OWNER_ROLE, ADMIN_ROLE, USER_ROLE} else (OWNER_ROLE if user.is_admin else USER_ROLE)


def can_manage_schedule(user: models.User) -> bool:
    return user_role(user) in {OWNER_ROLE, ADMIN_ROLE}


def is_owner(user: models.User) -> bool:
    return user_role(user) == OWNER_ROLE


def require_admin(request: Request, db: Session = Depends(database.get_db)):
    user = current_user(request, db)
    if not can_manage_schedule(user):
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return user


def require_owner(request: Request, db: Session = Depends(database.get_db)):
    user = current_user(request, db)
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="Управление аккаунтами доступно только владельцу")
    return user
