import hashlib
import hmac
import os
import secrets
import time
import io
from datetime import date
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, RedirectResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from sqlalchemy.orm import Session
from sqlalchemy import or_, text, inspect

from . import models, schemas, database, solver


def load_env_file():
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

# Создание таблиц (миграции здесь лучше убрать, так как ты удалишь БД)
models.Base.metadata.create_all(bind=database.engine)
teacher_columns = {column["name"] for column in inspect(database.engine).get_columns("teachers")}
if "working_days" not in teacher_columns:
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE teachers ADD COLUMN working_days VARCHAR DEFAULT '1,2,3,4,5' NOT NULL"))
if "vacation_weeks" not in teacher_columns:
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE teachers ADD COLUMN vacation_weeks VARCHAR DEFAULT '' NOT NULL"))
curator_columns = {column["name"] for column in inspect(database.engine).get_columns("curator_hours")}
with database.engine.begin() as connection:
    if "room_name" not in curator_columns:
        connection.execute(text("ALTER TABLE curator_hours ADD COLUMN room_name VARCHAR"))
    if "teacher_id" not in curator_columns:
        connection.execute(text("ALTER TABLE curator_hours ADD COLUMN teacher_id INTEGER"))

app = FastAPI(title="Schedule System API")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

SESSION_TTL = 60 * 60 * 12
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-this-session-secret")


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


def require_admin(request: Request, db: Session = Depends(database.get_db)):
    user = current_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return user


@app.middleware("http")
async def protect_site(request: Request, call_next):
    public_paths = {"/login.html", "/html/login.html", "/auth/login", "/docs", "/openapi.json", "/redoc"}
    public_asset = request.url.path.endswith((".js", ".css"))
    if request.url.path not in public_paths and not public_asset and not request.url.path.startswith(("/docs/", "/redoc/")):
        db = database.SessionLocal()
        try:
            user = current_user(request, db)
            admin_pages = {
                "/teachers.html", "/groups_subjects.html", "/admin.html", "/progress.html", "/users.html", "/algorithm_settings.html",
                "/html/teachers.html", "/html/groups_subjects.html", "/html/admin.html", "/html/progress.html", "/html/users.html", "/html/algorithm_settings.html",
            }
            if request.url.path in admin_pages and not user.is_admin:
                return Response(status_code=307, headers={"Location": "/"})
            admin_only = request.url.path.startswith("/users/") or (
                request.method != "GET" and request.url.path.startswith(("/rooms/", "/teachers/", "/groups/", "/subjects/", "/course_plans/"))
            )
            if admin_only and not user.is_admin:
                return Response(content='{"detail":"Нужны права администратора"}', status_code=403, media_type="application/json")
        except HTTPException:
            if request.url.path.endswith(".html") or request.url.path == "/":
                return Response(status_code=307, headers={"Location": "/html/login.html"})
            return Response(content='{"detail":"Требуется авторизация"}', status_code=401, media_type="application/json")
        finally:
            db.close()
    return await call_next(request)


def bootstrap_admin():
    db = database.SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            db.add(models.User(
                login=os.getenv("ADMIN_LOGIN", "admin").strip(),
                password_hash=hash_password(os.getenv("ADMIN_PASSWORD", "change-me-now")),
                is_admin=True,
            ))
            db.commit()
    finally:
        db.close()


bootstrap_admin()


@app.get("/", include_in_schema=False)
def home_page():
    return RedirectResponse("/html/index.html")


@app.get("/{page_name}.html", include_in_schema=False)
def legacy_page(page_name: str):
    allowed_pages = {"index", "login", "groups_subjects", "teachers", "admin", "progress", "users", "algorithm_settings"}
    if page_name not in allowed_pages:
        raise HTTPException(status_code=404, detail="Страница не найдена")
    return RedirectResponse(f"/html/{page_name}.html")


@app.post("/auth/login")
def login(data: schemas.LoginRequest, response: Response, db: Session = Depends(database.get_db)):
    user = db.query(models.User).filter_by(login=data.login.strip(), is_active=True).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    response.set_cookie("schedule_session", make_session(user.id), httponly=True, samesite="lax", max_age=SESSION_TTL)
    return {"login": user.login, "is_admin": user.is_admin}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("schedule_session")
    return {"ok": True}


@app.get("/auth/me")
def me(user: models.User = Depends(require_user)):
    return {"id": user.id, "login": user.login, "is_admin": user.is_admin, "is_active": user.is_active}


@app.get("/users/", response_model=List[schemas.UserOut])
def read_users(_: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    return db.query(models.User).order_by(models.User.login).all()


@app.post("/users/", response_model=schemas.UserOut)
def create_user(data: schemas.UserCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    login_name = data.login.strip()
    if not login_name or not data.password:
        raise HTTPException(status_code=400, detail="Логин и пароль обязательны")
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 8 символов")
    if db.query(models.User).filter_by(login=login_name).first():
        raise HTTPException(status_code=409, detail="Такой логин уже существует")
    user = models.User(login=login_name, password_hash=hash_password(data.password), is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/users/{user_id}")
def delete_user(user_id: int, current: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить текущего администратора")
    db.query(models.User).filter_by(id=user_id).delete()
    db.commit()
    return {"ok": True}


@app.put("/users/{user_id}", response_model=schemas.UserOut)
def update_user(user_id: int, data: schemas.UserUpdate, current: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.id == current.id and data.is_active is False:
        raise HTTPException(status_code=400, detail="Нельзя отключить собственный аккаунт")
    if data.password:
        if len(data.password) < 8:
            raise HTTPException(status_code=400, detail="Пароль должен содержать минимум 8 символов")
        user.password_hash = hash_password(data.password)
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.is_admin is True and user.id != current.id:
        raise HTTPException(status_code=400, detail="Полный доступ владельца нельзя передать другому пользователю")
    db.commit()
    db.refresh(user)
    return user


# ----------------- КАБИНЕТЫ (НОВОЕ) -----------------
@app.post("/rooms/", response_model=schemas.RoomOut)
def create_room(room: schemas.RoomCreate, db: Session = Depends(database.get_db)):
    db_room = models.Room(**room.dict())
    db.add(db_room)
    db.commit()
    db.refresh(db_room)
    return db_room


@app.get("/rooms/", response_model=List[schemas.RoomOut])
def read_rooms(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return db.query(models.Room).offset(skip).limit(limit).all()


@app.delete("/rooms/{room_id}")
def delete_room(room_id: int, db: Session = Depends(database.get_db)):
    db.query(models.Room).filter(models.Room.id == room_id).delete()
    db.commit()
    return {"ok": True}


# ----------------- ПРЕПОДАВАТЕЛИ -----------------
@app.post("/teachers/", response_model=schemas.TeacherOut)
def create_teacher(teacher: schemas.TeacherCreate, db: Session = Depends(database.get_db)):
    db_teacher = models.Teacher(**teacher.dict())
    db.add(db_teacher)
    db.commit()
    db.refresh(db_teacher)
    # Ручное добавление room_name для ответа
    room = db.query(models.Room).filter(models.Room.id == db_teacher.room_id).first()
    db_teacher.room_name = room.name if room else None
    return db_teacher


@app.get("/teachers/", response_model=List[schemas.TeacherOut])
def read_teachers(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    teachers = db.query(models.Teacher).offset(skip).limit(limit).all()
    rooms_dict = {r.id: r.name for r in db.query(models.Room).all()}
    for t in teachers:
        t.room_name = rooms_dict.get(t.room_id)
    return teachers


@app.put("/teachers/{teacher_id}", response_model=schemas.TeacherOut)
def update_teacher(teacher_id: int, teacher_data: schemas.TeacherCreate, db: Session = Depends(database.get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    for key, value in teacher_data.dict().items(): setattr(teacher, key, value)
    db.commit()
    db.refresh(teacher)
    room = db.query(models.Room).filter(models.Room.id == teacher.room_id).first()
    teacher.room_name = room.name if room else None
    return teacher


@app.put("/teachers/{teacher_id}/vacation-weeks", response_model=schemas.TeacherOut)
def update_teacher_vacation_weeks(teacher_id: int, data: schemas.TeacherVacationWeeks, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")
    weeks = sorted({int(week) for week in data.weeks if 1 <= int(week) <= 52})
    teacher.vacation_weeks = ",".join(map(str, weeks))
    db.commit()
    db.refresh(teacher)
    room = db.query(models.Room).filter(models.Room.id == teacher.room_id).first()
    teacher.room_name = room.name if room else None
    return teacher


@app.delete("/teachers/{teacher_id}")
def delete_teacher(teacher_id: int, db: Session = Depends(database.get_db)):
    db.query(models.Teacher).filter(models.Teacher.id == teacher_id).delete()
    db.commit()
    return {"ok": True}


@app.get("/teachers-workload/")
def get_teachers_workload(db: Session = Depends(database.get_db)):
    teachers = db.query(models.Teacher).all()
    rooms_dict = {r.id: r.name for r in db.query(models.Room).all()}
    result = []
    for t in teachers:
        plans = db.query(models.CoursePlan).filter(
            or_(models.CoursePlan.teacher_id == t.id, models.CoursePlan.teacher2_id == t.id)).all()

        total_semester_hours = 0
        total_weekly_hours_estimated = 0
        subject_loads_by_name = {}

        for p in plans:
            total_semester_hours += p.total_hours
            g = db.query(models.Group).filter(models.Group.id == p.group_id).first()
            weeks = getattr(g, 'semester_weeks', 20) or 20
            weekly_hours = round(p.total_hours / weeks, 1)
            total_weekly_hours_estimated += weekly_hours
            key = (p.subject_name or "").strip()
            item = subject_loads_by_name.setdefault(key, {
                "subject_name": key,
                "weekly_hours": 0,
                "plan_limit": 0,
            })
            item["weekly_hours"] += weekly_hours
            item["plan_limit"] += p.max_weekly_hours or 0

        subject_loads = [
            {**item, "weekly_hours": round(item["weekly_hours"], 1)}
            for item in sorted(subject_loads_by_name.values(), key=lambda item: item["subject_name"].casefold())
        ]

        result.append({
            "id": t.id,
            "name": t.name,
            "room_name": rooms_dict.get(t.room_id, "Без кабинета"),
            "max_hours_per_week": t.max_hours_per_week,
            "assigned_weekly_hours": round(total_weekly_hours_estimated, 1),
            "total_semester_hours": total_semester_hours,
            "subject_loads": subject_loads,
        })
    return result


# ----------------- ГРУППЫ -----------------
@app.post("/groups/", response_model=schemas.GroupOut)
def create_group(group: schemas.GroupCreate, db: Session = Depends(database.get_db)):
    db_group = models.Group(**group.dict())
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    return db_group


@app.get("/groups/", response_model=List[schemas.GroupOut])
def read_groups(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return db.query(models.Group).offset(skip).limit(limit).all()


@app.put("/groups/{group_id}", response_model=schemas.GroupOut)
def update_group(group_id: int, group_data: schemas.GroupCreate, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    group.number = group_data.number
    group.course = group_data.course
    group.has_saturday = group_data.has_saturday
    group.semester_weeks = group_data.semester_weeks
    db.commit()
    db.refresh(group)
    return group


@app.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(database.get_db)):
    db.query(models.Group).filter(models.Group.id == group_id).delete()
    db.commit()
    return {"ok": True}


@app.post("/groups/{group_id}/toggle-saturday")
def toggle_group_saturday(group_id: int, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    group.has_saturday = not bool(group.has_saturday)
    db.commit()
    db.refresh(group)
    return group


@app.post("/groups/{group_id}/set-weekly-hours")
def set_group_weekly_hours(group_id: int, payload: dict, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    group.weekly_hours = max(2, int(payload.get("weekly_hours", 30)))
    db.commit()
    db.refresh(group)
    return group


# ----------------- ПРЕДМЕТЫ -----------------
@app.get("/subjects/", response_model=List[schemas.SubjectOut])
def get_subjects(db: Session = Depends(database.get_db)):
    return db.query(models.Subject).all()


@app.post("/subjects/", response_model=schemas.SubjectOut)
def create_subject(subj: schemas.SubjectCreate, db: Session = Depends(database.get_db)):
    db_subj = models.Subject(name=subj.name)
    db.add(db_subj)
    db.commit()
    db.refresh(db_subj)
    return db_subj


@app.delete("/subjects/{subject_id}")
def delete_subject(subject_id: int, db: Session = Depends(database.get_db)):
    db.query(models.Subject).filter(models.Subject.id == subject_id).delete()
    db.commit()
    return {"ok": True}


# ----------------- УЧЕБНЫЕ ПЛАНЫ -----------------
@app.post("/course_plans/", response_model=schemas.CoursePlanOut)
def create_course_plan(plan: schemas.CoursePlanCreate, db: Session = Depends(database.get_db)):
    db_plan = models.CoursePlan(**plan.dict())
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


@app.get("/course_plans/", response_model=List[schemas.CoursePlanOut])
def read_course_plans(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return db.query(models.CoursePlan).offset(skip).limit(limit).all()


@app.put("/course_plans/{plan_id}", response_model=schemas.CoursePlanOut)
def update_course_plan(plan_id: int, plan_data: schemas.CoursePlanCreate, db: Session = Depends(database.get_db)):
    plan = db.query(models.CoursePlan).filter(models.CoursePlan.id == plan_id).first()
    plan.subject_name = plan_data.subject_name
    plan.total_hours = plan_data.total_hours
    plan.max_weekly_hours = plan_data.max_weekly_hours
    plan.group_id = plan_data.group_id
    plan.teacher_id = plan_data.teacher_id
    plan.teacher2_id = plan_data.teacher2_id
    db.commit()
    db.refresh(plan)
    return plan


@app.delete("/course_plans/{plan_id}")
def delete_course_plan(plan_id: int, db: Session = Depends(database.get_db)):
    db.query(models.CoursePlan).filter(models.CoursePlan.id == plan_id).delete()
    db.commit()
    return {"ok": True}


@app.get("/plans-progress/")
def get_plans_progress(group_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.CoursePlan)
    if group_id: query = query.filter(models.CoursePlan.group_id == group_id)
    plans = query.all()
    groups_dict = {g.id: g.number for g in db.query(models.Group).all()}
    teachers_dict = {t.id: t.name for t in db.query(models.Teacher).all()}

    result = []
    for p in plans:
        # ВАЖНО: Считаем только живые часы. Болезни (canceled) не идут в зачет!
        actual_count = db.query(models.ScheduleEntry).filter(
            models.ScheduleEntry.group_id == p.group_id,
            models.ScheduleEntry.subject_name == p.subject_name,
            models.ScheduleEntry.status != 'canceled'
        ).count()

        percentage = round((actual_count / p.total_hours * 100), 1) if p.total_hours > 0 else 0
        result.append({
            "plan_id": p.id, "group_id": p.group_id, "group_number": groups_dict.get(p.group_id, str(p.group_id)),
            "subject_name": p.subject_name, "teacher_name": teachers_dict.get(p.teacher_id, "Не назначен"),
            "total_hours": p.total_hours, "scheduled_hours": actual_count, "diff": actual_count - p.total_hours,
            "percentage": min(percentage, 100.0), "raw_percentage": percentage
        })
    return result


# ----------------- НАСТРОЙКИ АЛГОРИТМА -----------------
@app.get("/algorithm-rules/", response_model=List[schemas.AlgorithmRuleOut])
def read_algorithm_rules(_: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    return db.query(models.AlgorithmRule).order_by(models.AlgorithmRule.subject_name, models.AlgorithmRule.course).all()


@app.post("/algorithm-rules/", response_model=schemas.AlgorithmRuleOut)
def create_algorithm_rule(data: schemas.AlgorithmRuleCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    if not data.subject_name.strip() or data.weekly_hours < 1:
        raise HTTPException(status_code=400, detail="Укажите предмет и минимум 1 час в неделю")
    if data.group_id is None and data.course is None:
        raise HTTPException(status_code=400, detail="Выберите курс или конкретную группу")
    if data.lesson_mode not in {"auto", "lessons", "pairs", "pair_and_lesson"}:
        raise HTTPException(status_code=400, detail="Неизвестный режим занятий")
    rule = models.AlgorithmRule(**data.dict(exclude={"subject_name"}), subject_name=data.subject_name.strip())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@app.delete("/algorithm-rules/{rule_id}")
def delete_algorithm_rule(rule_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    rule = db.query(models.AlgorithmRule).filter_by(id=rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    db.delete(rule)
    db.commit()
    return {"ok": True}


@app.get("/curator-hours/", response_model=List[schemas.CuratorHourOut])
def read_curator_hours(_: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    return db.query(models.CuratorHour).filter_by(is_active=True).order_by(models.CuratorHour.group_id, models.CuratorHour.day_of_week, models.CuratorHour.time_slot).all()


@app.post("/curator-hours/", response_model=schemas.CuratorHourOut)
def create_curator_hour(data: schemas.CuratorHourCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    if not 1 <= data.day_of_week <= 6 or not 1 <= data.time_slot <= 12 or data.duration not in (1, 2):
        raise HTTPException(status_code=400, detail="Выберите корректный день, урок и длительность 1 или 2 урока")
    if data.duration == 2 and data.time_slot == 12:
        raise HTTPException(status_code=400, detail="Для двух уроков нужен не последний слот")
    group_id = data.group_id if data.group_id is not None else 0
    if group_id != 0 and not db.query(models.Group).filter_by(id=group_id).first():
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if group_id != 0 and data.teacher_id is None:
        raise HTTPException(status_code=400, detail="Для группового кураторского часа выберите куратора")
    if group_id != 0 and not (data.room_name or "").strip():
        raise HTTPException(status_code=400, detail="Для группового кураторского часа выберите кабинет")
    for slot in range(data.time_slot, data.time_slot + data.duration):
        conflict_groups = [0, group_id] if group_id == 0 else [group_id]
        exists = db.query(models.CuratorHour).filter(
            models.CuratorHour.group_id.in_(conflict_groups),
            models.CuratorHour.day_of_week == data.day_of_week,
            models.CuratorHour.is_active.is_(True),
            models.CuratorHour.time_slot <= slot,
            models.CuratorHour.time_slot + models.CuratorHour.duration > slot,
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="Этот слот уже занят кураторским часом")
        if group_id != 0:
            same_time = db.query(models.CuratorHour).filter(
                models.CuratorHour.group_id != group_id,
                models.CuratorHour.group_id != 0,
                models.CuratorHour.day_of_week == data.day_of_week,
                models.CuratorHour.time_slot <= slot,
                models.CuratorHour.time_slot + models.CuratorHour.duration > slot,
                models.CuratorHour.is_active.is_(True),
            ).all()
            if any(item.teacher_id == data.teacher_id for item in same_time):
                raise HTTPException(status_code=409, detail="Этот куратор уже занят в это время")
            if any(item.room_name and data.room_name and item.room_name.strip() == data.room_name.strip() for item in same_time):
                raise HTTPException(status_code=409, detail="Этот кабинет уже занят кураторским часом")
    payload = data.dict(exclude={"group_id"})
    if payload.get("room_name"):
        payload["room_name"] = payload["room_name"].strip()
    item = models.CuratorHour(**payload, group_id=group_id)
    db.add(item); db.commit(); db.refresh(item)
    return item


@app.put("/curator-hours/{item_id}", response_model=schemas.CuratorHourOut)
def update_curator_hour(item_id: int, data: schemas.CuratorHourCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    item = db.query(models.CuratorHour).filter_by(id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Кураторский час не найден")
    group_id = data.group_id if data.group_id is not None else item.group_id or 0
    if group_id != 0 and (data.teacher_id is None or not (data.room_name or "").strip()):
        raise HTTPException(status_code=400, detail="Для группового кураторского часа выберите куратора и кабинет")
    for slot in range(data.time_slot, data.time_slot + data.duration):
        exists = db.query(models.CuratorHour).filter(
            models.CuratorHour.id != item_id,
            models.CuratorHour.group_id.in_([0] if group_id == 0 else [group_id]),
            models.CuratorHour.day_of_week == data.day_of_week,
            models.CuratorHour.is_active.is_(True),
            models.CuratorHour.time_slot <= slot,
            models.CuratorHour.time_slot + models.CuratorHour.duration > slot,
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="Этот слот уже занят кураторским часом")
        if group_id != 0:
            same_time = db.query(models.CuratorHour).filter(
                models.CuratorHour.id != item_id,
                models.CuratorHour.group_id != 0,
                models.CuratorHour.day_of_week == data.day_of_week,
                models.CuratorHour.time_slot <= slot,
                models.CuratorHour.time_slot + models.CuratorHour.duration > slot,
                models.CuratorHour.is_active.is_(True),
            ).all()
            if any(other.teacher_id == data.teacher_id for other in same_time):
                raise HTTPException(status_code=409, detail="Этот куратор уже занят в это время")
            if any(other.room_name and data.room_name and other.room_name.strip() == data.room_name.strip() for other in same_time):
                raise HTTPException(status_code=409, detail="Этот кабинет уже занят кураторским часом")
    for key, value in data.dict(exclude={"group_id"}).items():
        setattr(item, key, value.strip() if key == "room_name" and value else value)
    item.group_id = group_id
    db.commit(); db.refresh(item)
    return item


@app.delete("/curator-hours/{item_id}")
def delete_curator_hour(item_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    item = db.query(models.CuratorHour).filter_by(id=item_id).first()
    if not item: raise HTTPException(status_code=404, detail="Кураторский час не найден")
    db.delete(item); db.commit(); return {"ok": True}


# ----------------- АРХИВЫ И ГЕНЕРАЦИЯ -----------------
@app.get("/archived-weeks/status")
def get_archive_status(group_id: int, week_number: int, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter_by(group_id=group_id, week_number=week_number).first()
    return {"is_archived": record.is_archived if record else False}

@app.post("/archived-weeks/toggle")
def toggle_archive(data: schemas.ArchivedWeekToggle, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter_by(group_id=data.group_id, week_number=data.week_number).first()
    if record: record.is_archived = not record.is_archived
    else: db.add(models.ArchivedWeek(group_id=data.group_id, week_number=data.week_number, is_archived=True))
    db.commit()
    return {"is_archived": record.is_archived if record else True}

@app.post("/generate_schedule/", response_model=schemas.GenerateResponse)
def trigger_generation(approve_adjustments: bool = False, db: Session = Depends(database.get_db)):
    # Генерируем ВЕСЬ семестр сразу
    success, msg = solver.trigger_global_generation(db, approve_adjustments=approve_adjustments)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


# ----------------- РАСПИСАНИЕ И СТАТУСЫ -----------------
@app.get("/schedule/", response_model=List[schemas.ScheduleEntryOut])
def get_schedule(group_id: Optional[int] = None, week_number: int = 1, db: Session = Depends(database.get_db)):
    query = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.week_number == week_number)
    if group_id: query = query.filter(models.ScheduleEntry.group_id == group_id)
    return query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.time_slot).all()


def build_schedule_pdf(db: Session, week_number: int, day: Optional[int] = None, schedule_date: Optional[date] = None):
    font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"] if Path(path).exists()), None)
    if not font_path:
        raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
    pdfmetrics.registerFont(TTFont("ScheduleFont", font_path))
    bold_font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"] if Path(path).exists()), font_path)
    pdfmetrics.registerFont(TTFont("ScheduleFont-Bold", bold_font_path))
    group_numbers = {group.id: group.number for group in db.query(models.Group).all()}
    teachers = {teacher.id: teacher.name for teacher in db.query(models.Teacher).all()}
    curator_hours = db.query(models.CuratorHour).filter_by(is_active=True).all()
    query = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == week_number,
        models.ScheduleEntry.status != "canceled",
    )
    if day is not None:
        query = query.filter(models.ScheduleEntry.day_of_week == day)
    entries = query.order_by(
        models.ScheduleEntry.day_of_week,
        models.ScheduleEntry.group_id,
        models.ScheduleEntry.time_slot,
    ).all()
    available_days = {entry.day_of_week for entry in entries}
    available_days.update(item.day_of_week for item in curator_hours)
    days = [day] if day else sorted(available_days) or list(range(1, 6))
    day_names = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА"]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=9 * mm,
        bottomMargin=9 * mm,
    )
    title = ParagraphStyle("PdfTitle", fontName="ScheduleFont", fontSize=16, leading=19, alignment=1, textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    subtitle = ParagraphStyle("PdfSubtitle", fontName="ScheduleFont", fontSize=11, leading=14, alignment=1, textColor=colors.HexColor("#475569"), spaceAfter=2)
    story = []

    for index, selected_day in enumerate(days):
        date_line = f"ДАТА: {schedule_date.strftime('%d.%m.%Y')}" if schedule_date else ""
        story.extend([
            Paragraph("РАСПИСАНИЕ ЗАНЯТИЙ", title),
            Paragraph(date_line, subtitle) if date_line else Spacer(1, 1 * mm),
            Paragraph(f"{day_names[selected_day - 1]}  •  НЕДЕЛЯ {week_number}", subtitle),
            Spacer(1, 3 * mm),
        ])

        day_entries = [entry for entry in entries if entry.day_of_week == selected_day]
        day_curators = [item for item in curator_hours if item.day_of_week == selected_day]
        specific_slots = {
            (item.group_id, item.time_slot + offset)
            for item in day_curators
            if item.group_id not in (0, None)
            for offset in range(item.duration)
        }
        rows_by_group = {}

        for entry in day_entries:
            teacher_name = teachers.get(entry.teacher_id, "Не назначен")
            if entry.teacher2_id:
                teacher_name += f" / {teachers.get(entry.teacher2_id, '')}"
            rows_by_group.setdefault(entry.group_id, []).append((
                entry.time_slot,
                0,
                [str(group_numbers.get(entry.group_id, entry.group_id)), str(entry.time_slot), entry.subject_name or "-", entry.room_name or "-", teacher_name],
                False,
            ))

        for item in day_curators:
            target_groups = [item.group_id] if item.group_id not in (0, None) else [group_id for group_id in group_numbers if (group_id, item.time_slot) not in specific_slots]
            for group_id in target_groups:
                slot_label = str(item.time_slot) if item.duration == 1 else f"{item.time_slot}-{item.time_slot + item.duration - 1}"
                rows_by_group.setdefault(group_id, []).append((
                    item.time_slot,
                    1,
                    [str(group_numbers.get(group_id, group_id)), slot_label, "Кураторский час", item.room_name or "-", teachers.get(item.teacher_id, "Куратор не указан")],
                    True,
                ))

        rows = [["№ группы", "№ урока", "Предмет", "Кабинет", "Преподаватель"]]
        group_spans = []
        curator_rows = []
        ordered_groups = sorted(rows_by_group, key=lambda group_id: (int(group_numbers.get(group_id, group_id)), int(group_id)))
        for group_index, group_id in enumerate(ordered_groups):
            group_rows = sorted(rows_by_group[group_id], key=lambda item: (item[0], item[1]))
            group_start = len(rows)
            for row_index, (slot, kind, row, is_curator) in enumerate(group_rows):
                # Одна объединённая ячейка на весь блок группы вместо повторения номера.
                row[0] = str(group_numbers.get(group_id, group_id)) if row_index == 0 else ""
                rows.append(row)
                if is_curator:
                    curator_rows.append(len(rows) - 1)
            group_spans.append((group_start, len(rows) - 1))

        subject_width = max([stringWidth(str(entry.subject_name or "Предмет"), "ScheduleFont", 8) + 10 * mm for entry in day_entries] or [45 * mm])
        subject_width = min(max(subject_width, 45 * mm), 125 * mm)
        table = Table(rows, colWidths=[23 * mm, 20 * mm, subject_width, 27 * mm, 52 * mm], repeatRows=1, hAlign="CENTER")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "ScheduleFont"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for group_index, (start_row, end_row) in enumerate(group_spans):
            style.append(("SPAN", (0, start_row), (0, end_row)))
            style.append(("FONTNAME", (0, start_row), (0, start_row), "ScheduleFont-Bold"))
            style.append(("FONTSIZE", (0, start_row), (0, start_row), 10))
            style.append(("BACKGROUND", (0, start_row), (0, end_row), colors.HexColor("#e8f3f1") if group_index % 2 == 0 else colors.HexColor("#eef2f7")))
            style.append(("TEXTCOLOR", (0, start_row), (0, start_row), colors.HexColor("#0f766e")))
        for row in curator_rows:
            # Не перекрываем цельный фон объединённой ячейки группы.
            style.append(("BACKGROUND", (1, row), (-1, row), colors.HexColor("#fff7ed")))
            style.append(("TEXTCOLOR", (2, row), (2, row), colors.HexColor("#b45309")))
        table.setStyle(TableStyle(style))
        story.append(table)
        if index < len(days) - 1:
            story.append(PageBreak())

    document.build(story)
    buffer.seek(0)
    return buffer


def build_teachers_schedule_pdf(db: Session, week_number: int, day: Optional[int] = None, schedule_date: Optional[date] = None):
    font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"] if Path(path).exists()), None)
    if not font_path:
        raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
    pdfmetrics.registerFont(TTFont("ScheduleFont", font_path))
    bold_font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"] if Path(path).exists()), font_path)
    pdfmetrics.registerFont(TTFont("ScheduleFont-Bold", bold_font_path))
    groups = {group.id: group.number for group in db.query(models.Group).all()}
    teachers = {teacher.id: teacher.name for teacher in db.query(models.Teacher).all()}
    curator_hours = db.query(models.CuratorHour).filter_by(is_active=True).all()
    query = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == week_number,
        models.ScheduleEntry.status != "canceled",
    )
    if day is not None:
        query = query.filter(models.ScheduleEntry.day_of_week == day)
    entries = query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.time_slot).all()
    available_days = {entry.day_of_week for entry in entries}
    available_days.update(item.day_of_week for item in curator_hours)
    days = [day] if day else sorted(available_days) or list(range(1, 6))
    day_names = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА"]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=8 * mm, rightMargin=8 * mm, topMargin=9 * mm, bottomMargin=9 * mm)
    title = ParagraphStyle("TeachersPdfTitle", fontName="ScheduleFont", fontSize=16, leading=19, alignment=1, textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    subtitle = ParagraphStyle("TeachersPdfSubtitle", fontName="ScheduleFont", fontSize=11, leading=14, alignment=1, textColor=colors.HexColor("#475569"), spaceAfter=2)
    story = []

    for index, selected_day in enumerate(days):
        date_line = f"ДАТА: {schedule_date.strftime('%d.%m.%Y')}" if schedule_date else ""
        story.extend([
            Paragraph("РАСПИСАНИЕ ПРЕПОДАВАТЕЛЕЙ", title),
            Paragraph(date_line, subtitle) if date_line else Spacer(1, 1 * mm),
            Paragraph(f"{day_names[selected_day - 1]}  •  НЕДЕЛЯ {week_number}", subtitle),
            Spacer(1, 3 * mm),
        ])
        rows_by_teacher = {}
        for entry in (entry for entry in entries if entry.day_of_week == selected_day):
            for teacher_id in {teacher_id for teacher_id in (entry.teacher_id, entry.teacher2_id) if teacher_id}:
                rows_by_teacher.setdefault(teacher_id, []).append((
                    entry.time_slot, 0,
                    [teachers.get(teacher_id, f"Преподаватель {teacher_id}"), str(entry.time_slot), f"Группа {groups.get(entry.group_id, entry.group_id)}", entry.subject_name or "-", entry.room_name or "-"],
                    False,
                ))
        for item in (item for item in curator_hours if item.day_of_week == selected_day and item.teacher_id):
            target_groups = [item.group_id] if item.group_id not in (0, None) else list(groups)
            group_label = ", ".join(f"гр. {groups.get(group_id, group_id)}" for group_id in target_groups)
            slot_label = str(item.time_slot) if item.duration == 1 else f"{item.time_slot}-{item.time_slot + item.duration - 1}"
            rows_by_teacher.setdefault(item.teacher_id, []).append((
                item.time_slot, 1,
                [teachers.get(item.teacher_id, f"Преподаватель {item.teacher_id}"), slot_label, group_label, "Кураторский час", item.room_name or "-"],
                True,
            ))

        rows = [["Преподаватель", "№ урока", "Группа", "Предмет", "Кабинет"]]
        teacher_spans, curator_rows = [], []
        ordered_teachers = sorted(rows_by_teacher, key=lambda teacher_id: teachers.get(teacher_id, "").casefold())
        for teacher_index, teacher_id in enumerate(ordered_teachers):
            teacher_rows = sorted(rows_by_teacher[teacher_id], key=lambda item: (item[0], item[1]))
            start_row = len(rows)
            for row_index, (_, _, row, is_curator) in enumerate(teacher_rows):
                row[0] = teachers.get(teacher_id, f"Преподаватель {teacher_id}") if row_index == 0 else ""
                rows.append(row)
                if is_curator:
                    curator_rows.append(len(rows) - 1)
            teacher_spans.append((start_row, len(rows) - 1))

        table = Table(rows, colWidths=[58 * mm, 20 * mm, 35 * mm, 100 * mm, 35 * mm], repeatRows=1, hAlign="CENTER")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "ScheduleFont"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for teacher_index, (start_row, end_row) in enumerate(teacher_spans):
            style.extend([
                ("SPAN", (0, start_row), (0, end_row)),
                ("FONTNAME", (0, start_row), (0, start_row), "ScheduleFont-Bold"),
                ("FONTSIZE", (0, start_row), (0, start_row), 9),
                ("BACKGROUND", (0, start_row), (0, end_row), colors.HexColor("#e8f3f1") if teacher_index % 2 == 0 else colors.HexColor("#eef2f7")),
                ("TEXTCOLOR", (0, start_row), (0, start_row), colors.HexColor("#0f766e")),
            ])
        for row in curator_rows:
            style.extend([("BACKGROUND", (1, row), (-1, row), colors.HexColor("#fff7ed")), ("TEXTCOLOR", (3, row), (3, row), colors.HexColor("#b45309"))])
        table.setStyle(TableStyle(style))
        story.append(table)
        if index < len(days) - 1:
            story.append(PageBreak())
    document.build(story)
    buffer.seek(0)
    return buffer


@app.get("/export/schedule.pdf")
def export_schedule_pdf(week_number: int = 1, day: Optional[int] = None, schedule_date: Optional[date] = None, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    date_suffix = f"_{schedule_date.isoformat()}" if schedule_date else ""
    filename = f"schedule_week_{week_number}{date_suffix}" + (f"_day_{day}" if day else "") + ".pdf"
    return StreamingResponse(build_schedule_pdf(db, week_number, day, schedule_date), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/export/teachers.pdf")
def export_teachers_schedule_pdf(week_number: int = 1, day: Optional[int] = None, schedule_date: Optional[date] = None, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    date_suffix = f"_{schedule_date.isoformat()}" if schedule_date else ""
    filename = f"teachers_schedule_week_{week_number}{date_suffix}" + (f"_day_{day}" if day else "") + ".pdf"
    return StreamingResponse(build_teachers_schedule_pdf(db, week_number, day, schedule_date), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def is_physical_education(subject_name: str) -> bool:
    name = (subject_name or "").casefold()
    return "физ" in name or "спорт" in name or "здоров" in name


def teacher_works_on_day(teacher, day_of_week: int) -> bool:
    try:
        working_days = {int(value.strip()) for value in (teacher.working_days or "1,2,3,4,5").split(",")}
    except (AttributeError, TypeError, ValueError):
        working_days = {1, 2, 3, 4, 5}
    return day_of_week in working_days and teacher.is_active and not teacher.on_vacation and not teacher.is_sick


def teacher_is_on_vacation(teacher, week_number: int) -> bool:
    try:
        vacation_weeks = {int(value.strip()) for value in (teacher.vacation_weeks or "").split(",") if value.strip()}
    except (AttributeError, TypeError, ValueError):
        vacation_weeks = set()
    return week_number in vacation_weeks


def validate_schedule_conflicts(data: schemas.ScheduleEntryBase, db: Session, exclude_entry_id: Optional[int] = None):
    teacher_ids = {teacher_id for teacher_id in (data.teacher_id, data.teacher2_id) if teacher_id}
    selected_teachers = db.query(models.Teacher).filter(models.Teacher.id.in_(teacher_ids)).all()
    if len(selected_teachers) != len(teacher_ids):
        raise HTTPException(status_code=400, detail="Выбранный преподаватель не найден")
    for teacher in selected_teachers:
        if not teacher_works_on_day(teacher, data.day_of_week):
            raise HTTPException(status_code=409, detail=f"Преподаватель {teacher.name} не работает в выбранный день")
        if teacher_is_on_vacation(teacher, data.week_number):
            raise HTTPException(status_code=409, detail=f"Преподаватель {teacher.name} находится в отпуске на этой неделе")
    blocked = db.query(models.CuratorHour).filter(
        models.CuratorHour.group_id.in_([0, data.group_id]),
        models.CuratorHour.day_of_week == data.day_of_week,
        models.CuratorHour.is_active.is_(True),
        models.CuratorHour.time_slot <= data.time_slot,
        models.CuratorHour.time_slot + models.CuratorHour.duration > data.time_slot,
    ).first()
    if blocked:
        raise HTTPException(status_code=409, detail="Этот слот заблокирован кураторским часом")
    query = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == data.week_number,
        models.ScheduleEntry.day_of_week == data.day_of_week,
        models.ScheduleEntry.time_slot == data.time_slot,
        models.ScheduleEntry.status != "canceled",
    )
    if exclude_entry_id:
        query = query.filter(models.ScheduleEntry.id != exclude_entry_id)
    entries = query.all()
    for entry in entries:
        if entry.group_id == data.group_id:
            raise HTTPException(status_code=409, detail="У группы уже есть занятие в этот день и этот урок")
        existing_teachers = {teacher_id for teacher_id in (entry.teacher_id, entry.teacher2_id) if teacher_id}
        new_teachers = {teacher_id for teacher_id in (data.teacher_id, data.teacher2_id) if teacher_id}
        if existing_teachers & new_teachers:
            raise HTTPException(status_code=409, detail="Преподаватель уже занят в этот день и этот урок")
        same_room = bool(data.room_name and entry.room_name and data.room_name.strip() == entry.room_name.strip() and data.room_name.strip() != "Без кабинета")
        shared_pe = is_physical_education(data.subject_name) and is_physical_education(entry.subject_name) and not (existing_teachers & new_teachers)
        if same_room and not shared_pe:
            raise HTTPException(status_code=409, detail="Кабинет уже занят в этот день и этот урок")
        if same_room and shared_pe:
            pe_in_room = [item for item in entries if item.room_name and item.room_name.strip() == data.room_name.strip() and is_physical_education(item.subject_name)]
            if len(pe_in_room) >= 2:
                raise HTTPException(status_code=409, detail="В одном спортивном зале одновременно допустимы максимум две группы")

@app.post("/schedule/", response_model=schemas.ScheduleEntryOut)
def create_schedule_entry(entry_data: schemas.ScheduleEntryCreate, db: Session = Depends(database.get_db)):
    validate_schedule_conflicts(entry_data, db)
    new_entry = models.ScheduleEntry(**entry_data.dict())
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return new_entry

@app.put("/schedule/{entry_id}", response_model=schemas.ScheduleEntryOut)
def update_schedule_entry(entry_id: int, update_data: schemas.ScheduleEntryUpdate, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Занятие не найдено")
    validate_schedule_conflicts(update_data, db, exclude_entry_id=entry_id)
    for key, value in update_data.dict().items(): setattr(entry, key, value)
    db.commit()
    db.refresh(entry)
    return entry

@app.delete("/schedule/{entry_id}")
def delete_schedule_entry(entry_id: int, db: Session = Depends(database.get_db)):
    db.query(models.ScheduleEntry).filter_by(id=entry_id).delete()
    db.commit()
    return {"ok": True}

# --- НОВЫЕ ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ БОЛЕЗНЯМИ ---
@app.post("/schedule/{entry_id}/cancel", response_model=schemas.ScheduleEntryOut)
def cancel_schedule_entry(entry_id: int, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
    entry.status = "canceled"
    db.commit()
    db.refresh(entry)
    return entry

@app.post("/schedule/{entry_id}/restore", response_model=schemas.ScheduleEntryOut)
def restore_schedule_entry(entry_id: int, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
    entry.status = "planned"
    db.commit()
    db.refresh(entry)
    return entry


# Frontend is served by the same process, so Docker needs only one service.
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
