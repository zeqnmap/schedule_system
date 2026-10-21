"""Authentication and account management endpoints."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .. import database, models, schemas
from ..security import (
    SESSION_TTL,
    hash_password,
    is_owner,
    make_session,
    require_owner,
    require_user,
    user_role,
    verify_password,
    clear_login_failures,
    login_allowed,
    register_login_failure,
)

router = APIRouter()


@router.post("/auth/login")
def login(data: schemas.LoginRequest, response: Response, db: Session = Depends(database.get_db)):
    login_name = data.login.strip()
    if not login_allowed(login_name):
        raise HTTPException(status_code=429, detail="Слишком много попыток входа. Повторите позже.")
    user = db.query(models.User).filter_by(login=login_name, is_active=True).first()
    if not user or not verify_password(data.password, user.password_hash):
        register_login_failure(login_name)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    clear_login_failures(login_name)
    secure = __import__("os").getenv("SESSION_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
    response.set_cookie("schedule_session", make_session(user.id), httponly=True, secure=secure, samesite="lax", max_age=SESSION_TTL)
    return {"login": user.login, "role": user_role(user), "is_admin": is_owner(user)}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("schedule_session")
    return {"ok": True}


@router.get("/auth/me")
def me(user: models.User = Depends(require_user)):
    return {"id": user.id, "login": user.login, "role": user_role(user), "is_admin": is_owner(user), "is_active": user.is_active}


@router.get("/users/", response_model=List[schemas.UserOut])
def read_users(_: models.User = Depends(require_owner), db: Session = Depends(database.get_db)):
    return db.query(models.User).order_by(models.User.login).all()


@router.post("/users/", response_model=schemas.UserOut)
def create_user(data: schemas.UserCreate, _: models.User = Depends(require_owner), db: Session = Depends(database.get_db)):
    login_name = data.login.strip()
    if not login_name or not data.password:
        raise HTTPException(status_code=400, detail="Логин и пароль обязательны")
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 8 символов")
    if db.query(models.User).filter_by(login=login_name).first():
        raise HTTPException(status_code=409, detail="Такой логин уже существует")
    user = models.User(login=login_name, password_hash=hash_password(data.password), role=data.role, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current: models.User = Depends(require_owner), db: Session = Depends(database.get_db)):
    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить текущего владельца")
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    if is_owner(user):
        raise HTTPException(status_code=400, detail="Владельца нельзя удалить")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.put("/users/{user_id}", response_model=schemas.UserOut)
def update_user(user_id: int, data: schemas.UserUpdate, current: models.User = Depends(require_owner), db: Session = Depends(database.get_db)):
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.id == current.id and data.is_active is False:
        raise HTTPException(status_code=400, detail="Нельзя отключить собственный аккаунт")
    if is_owner(user) and user.id != current.id:
        raise HTTPException(status_code=400, detail="Нельзя изменять другого владельца")
    if data.password:
        if len(data.password) < 8:
            raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 8 символов")
        user.password_hash = hash_password(data.password)
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.role is not None:
        user.role = data.role
        user.is_admin = False
    db.commit()
    db.refresh(user)
    return user
