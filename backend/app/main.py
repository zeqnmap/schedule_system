import hashlib
import hmac
import os
import secrets
import time
import io
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional
from xml.sax.saxutils import escape
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


def calendar_week_for_date(year_start: date, target: date) -> int:
    """Return the Monday-based timetable week containing target."""
    year_monday = year_start - timedelta(days=year_start.isoweekday() - 1)
    return max(1, ((target - year_monday).days // 7) + 1)


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
    if "hour_type" not in curator_columns:
        connection.execute(text("ALTER TABLE curator_hours ADD COLUMN hour_type VARCHAR DEFAULT 'curator' NOT NULL"))
override_columns = {column["name"] for column in inspect(database.engine).get_columns("group_curator_hour_overrides")}
with database.engine.begin() as connection:
    if "schedule_date" not in override_columns:
        connection.execute(text("ALTER TABLE group_curator_hour_overrides ADD COLUMN schedule_date DATE"))
        # Earlier versions applied an override to every week. They are invalid
        # once overrides become date-specific, so restore those cards.
        connection.execute(text("DELETE FROM group_curator_hour_overrides"))
group_columns = {column["name"] for column in inspect(database.engine).get_columns("groups")}
with database.engine.begin() as connection:
    if "curator_teacher_id" not in group_columns:
        connection.execute(text("ALTER TABLE groups ADD COLUMN curator_teacher_id INTEGER"))
    if "curator_room_name" not in group_columns:
        connection.execute(text("ALTER TABLE groups ADD COLUMN curator_room_name VARCHAR"))
term_columns = {column["name"] for column in inspect(database.engine).get_columns("group_terms")}
with database.engine.begin() as connection:
    course_plan_columns = {column["name"] for column in inspect(database.engine).get_columns("course_plans")}
    schedule_entry_columns = {column["name"] for column in inspect(database.engine).get_columns("schedule_entries")}
    if "term_id" not in course_plan_columns:
        connection.execute(text("ALTER TABLE course_plans ADD COLUMN term_id INTEGER"))
    if "term_id" not in schedule_entry_columns:
        connection.execute(text("ALTER TABLE schedule_entries ADD COLUMN term_id INTEGER"))
    if "is_locked" not in term_columns:
        connection.execute(text("ALTER TABLE group_terms ADD COLUMN is_locked BOOLEAN DEFAULT 0 NOT NULL"))
    if "start_date" not in term_columns:
        connection.execute(text("ALTER TABLE group_terms ADD COLUMN start_date DATE"))
    if "end_date" not in term_columns:
        connection.execute(text("ALTER TABLE group_terms ADD COLUMN end_date DATE"))
    plan_columns = {column["name"] for column in inspect(database.engine).get_columns("course_plans")}
    schedule_columns = {column["name"] for column in inspect(database.engine).get_columns("schedule_entries")}
    archive_columns = {column["name"] for column in inspect(database.engine).get_columns("archived_weeks")}
    if "academic_year_id" not in plan_columns:
        connection.execute(text("ALTER TABLE course_plans ADD COLUMN academic_year_id INTEGER"))
    if "academic_year_id" not in schedule_columns:
        connection.execute(text("ALTER TABLE schedule_entries ADD COLUMN academic_year_id INTEGER"))
    if "academic_year_id" not in archive_columns:
        connection.execute(text("ALTER TABLE archived_weeks ADD COLUMN academic_year_id INTEGER"))
    if "schedule_date" not in schedule_columns:
        connection.execute(text("ALTER TABLE schedule_entries ADD COLUMN schedule_date DATE"))
    connection.execute(text("UPDATE group_terms SET start_date = COALESCE(start_date, '2026-09-01'), end_date = COALESCE(end_date, date('2026-09-01', '+' || (start_week + weeks - 2) || ' days'))"))
    connection.execute(text("UPDATE course_plans SET term_id = (SELECT id FROM group_terms WHERE group_terms.group_id = course_plans.group_id AND group_terms.term_number = 1 LIMIT 1) WHERE term_id IS NULL"))
    connection.execute(text("UPDATE course_plans SET academic_year_id = (SELECT academic_year_id FROM group_terms WHERE group_terms.id = course_plans.term_id) WHERE academic_year_id IS NULL"))
    connection.execute(text("UPDATE schedule_entries SET term_id = (SELECT id FROM group_terms WHERE group_terms.group_id = schedule_entries.group_id AND schedule_entries.week_number >= group_terms.start_week AND schedule_entries.week_number < group_terms.start_week + group_terms.weeks ORDER BY group_terms.term_number LIMIT 1) WHERE term_id IS NULL"))
    connection.execute(text("UPDATE schedule_entries SET academic_year_id = (SELECT academic_year_id FROM group_terms WHERE group_terms.id = schedule_entries.term_id) WHERE academic_year_id IS NULL"))

# Convert legacy week-based rows once so calendar exclusions also remove
# previously generated lessons, not only new ones.
with database.SessionLocal() as migration_db:
    legacy_entries = migration_db.query(models.ScheduleEntry).filter(models.ScheduleEntry.schedule_date.is_(None)).all()
    years_by_id = {year.id: year for year in migration_db.query(models.AcademicYear).all()}
    for entry in legacy_entries:
        year = years_by_id.get(entry.academic_year_id)
        if not year:
            continue
        monday = year.start_date - timedelta(days=year.start_date.isoweekday() - 1)
        entry.schedule_date = monday + timedelta(days=(entry.week_number - 1) * 7 + entry.day_of_week - 1)
    migration_db.commit()

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
                "/vedomost.html", "/html/teachers.html", "/html/groups_subjects.html", "/html/admin.html", "/html/progress.html", "/html/vedomost.html", "/html/users.html", "/html/algorithm_settings.html",
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


def remove_entries_for_dates(
    db: Session,
    academic_year_id: int,
    start_date: date,
    end_date: date,
    teacher_id: Optional[int] = None,
    group_id: Optional[int] = None,
) -> int:
    query = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.academic_year_id == academic_year_id,
        models.ScheduleEntry.schedule_date >= start_date,
        models.ScheduleEntry.schedule_date <= end_date,
    )
    if teacher_id is not None:
        query = query.filter(or_(models.ScheduleEntry.teacher_id == teacher_id, models.ScheduleEntry.teacher2_id == teacher_id))
    if group_id is not None:
        query = query.filter(models.ScheduleEntry.group_id == group_id)
    return query.delete(synchronize_session=False)


@app.get("/teacher-vacations/", response_model=List[schemas.TeacherVacationOut])
def read_teacher_vacations(teacher_id: Optional[int] = None, academic_year_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.TeacherVacation)
    if teacher_id:
        query = query.filter(models.TeacherVacation.teacher_id == teacher_id)
    if academic_year_id:
        query = query.filter(models.TeacherVacation.academic_year_id == academic_year_id)
    return query.order_by(models.TeacherVacation.start_date).all()


@app.post("/teacher-vacations/", response_model=schemas.TeacherVacationOut)
def create_teacher_vacation(data: schemas.TeacherVacationCreate, response: Response, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    if data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail="Дата окончания отпуска раньше даты начала")
    if not db.query(models.Teacher).filter_by(id=data.teacher_id).first() or not db.query(models.AcademicYear).filter_by(id=data.academic_year_id).first():
        raise HTTPException(status_code=404, detail="Преподаватель или учебный год не найден")
    vacation = models.TeacherVacation(**data.dict())
    db.add(vacation)
    removed = remove_entries_for_dates(db, data.academic_year_id, data.start_date, data.end_date, data.teacher_id)
    db.commit(); db.refresh(vacation); response.headers["X-Removed-Schedule-Entries"] = str(removed)
    return vacation


@app.delete("/teacher-vacations/{vacation_id}")
def delete_teacher_vacation(vacation_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    vacation = db.query(models.TeacherVacation).filter_by(id=vacation_id).first()
    if not vacation:
        raise HTTPException(status_code=404, detail="Период отпуска не найден")
    db.delete(vacation); db.commit()
    return {"ok": True}


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
@app.get("/academic-years/", response_model=List[schemas.AcademicYearOut])
def read_academic_years(db: Session = Depends(database.get_db)):
    return db.query(models.AcademicYear).order_by(models.AcademicYear.start_date.desc(), models.AcademicYear.id.desc()).all()


@app.post("/academic-years/", response_model=schemas.AcademicYearOut)
def create_academic_year(data: schemas.AcademicYearCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    name = data.name.strip()
    if not name or data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail="Проверьте название и даты учебного года")
    if db.query(models.AcademicYear).filter(models.AcademicYear.name == name).first():
        raise HTTPException(status_code=409, detail="Такой учебный год уже существует")
    if data.is_active:
        db.query(models.AcademicYear).update({models.AcademicYear.is_active: False}, synchronize_session=False)
    year = models.AcademicYear(name=name, start_date=data.start_date, end_date=data.end_date, is_active=data.is_active)
    db.add(year); db.commit(); db.refresh(year)
    return year


@app.put("/academic-years/{year_id}", response_model=schemas.AcademicYearOut)
def update_academic_year(year_id: int, data: schemas.AcademicYearCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    year = db.query(models.AcademicYear).filter_by(id=year_id).first()
    if not year: raise HTTPException(status_code=404, detail="Учебный год не найден")
    if data.end_date < data.start_date: raise HTTPException(status_code=400, detail="Дата окончания раньше даты начала")
    duplicate = db.query(models.AcademicYear).filter(models.AcademicYear.name == data.name.strip(), models.AcademicYear.id != year_id).first()
    if duplicate: raise HTTPException(status_code=409, detail="Такой учебный год уже существует")
    year.name, year.start_date, year.end_date, year.is_active = data.name.strip(), data.start_date, data.end_date, data.is_active
    db.commit(); db.refresh(year); return year


@app.post("/academic-years/{year_id}/activate", response_model=schemas.AcademicYearOut)
def activate_academic_year(year_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    year = db.query(models.AcademicYear).filter_by(id=year_id).first()
    if not year: raise HTTPException(status_code=404, detail="Учебный год не найден")
    db.query(models.AcademicYear).update({models.AcademicYear.is_active: False}, synchronize_session=False)
    year.is_active = True; db.commit(); db.refresh(year); return year


@app.get("/academic-days-off/", response_model=List[schemas.AcademicDayOffOut])
def read_academic_days_off(academic_year_id: int, db: Session = Depends(database.get_db)):
    return db.query(models.AcademicDayOff).filter_by(academic_year_id=academic_year_id).order_by(models.AcademicDayOff.day_date).all()


@app.post("/academic-days-off/", response_model=schemas.AcademicDayOffOut)
def create_academic_day_off(data: schemas.AcademicDayOffCreate, response: Response, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    year = db.query(models.AcademicYear).filter_by(id=data.academic_year_id).first()
    if not year or data.day_date < year.start_date or data.day_date > year.end_date:
        raise HTTPException(status_code=400, detail="Дата должна находиться внутри выбранного учебного года")
    existing = db.query(models.AcademicDayOff).filter_by(academic_year_id=data.academic_year_id, day_date=data.day_date).first()
    if existing:
        return existing
    day_off = models.AcademicDayOff(**data.dict())
    db.add(day_off)
    removed = remove_entries_for_dates(db, data.academic_year_id, data.day_date, data.day_date)
    db.commit(); db.refresh(day_off); response.headers["X-Removed-Schedule-Entries"] = str(removed)
    return day_off


@app.delete("/academic-days-off/{day_off_id}")
def delete_academic_day_off(day_off_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    day_off = db.query(models.AcademicDayOff).filter_by(id=day_off_id).first()
    if not day_off:
        raise HTTPException(status_code=404, detail="Выходной не найден")
    db.delete(day_off); db.commit()
    return {"ok": True}


@app.get("/group-break-days/", response_model=List[schemas.GroupBreakDayOut])
def read_group_break_days(group_id: int, academic_year_id: int, db: Session = Depends(database.get_db)):
    return db.query(models.GroupBreakDay).filter_by(
        group_id=group_id, academic_year_id=academic_year_id
    ).order_by(models.GroupBreakDay.day_date).all()


@app.post("/group-break-days/", response_model=schemas.GroupBreakDayOut)
def create_group_break_day(data: schemas.GroupBreakDayCreate, response: Response, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter_by(id=data.group_id).first()
    year = db.query(models.AcademicYear).filter_by(id=data.academic_year_id).first()
    if not group or not year:
        raise HTTPException(status_code=404, detail="Группа или учебный год не найдены")
    in_group_term = db.query(models.GroupTerm).filter(
        models.GroupTerm.group_id == data.group_id,
        models.GroupTerm.academic_year_id == data.academic_year_id,
        models.GroupTerm.start_date <= data.day_date,
        models.GroupTerm.end_date >= data.day_date,
    ).first()
    if not in_group_term:
        raise HTTPException(status_code=400, detail="Дата должна находиться в одном из семестров выбранной группы")
    existing = db.query(models.GroupBreakDay).filter_by(
        group_id=data.group_id, academic_year_id=data.academic_year_id, day_date=data.day_date
    ).first()
    if existing:
        return existing
    item = models.GroupBreakDay(**data.dict())
    db.add(item)
    removed = remove_entries_for_dates(
        db, data.academic_year_id, data.day_date, data.day_date, group_id=data.group_id
    )
    db.commit(); db.refresh(item)
    response.headers["X-Removed-Schedule-Entries"] = str(removed)
    return item


@app.post("/group-break-days/range/", response_model=List[schemas.GroupBreakDayOut])
def create_group_break_range(data: schemas.GroupBreakRangeCreate, response: Response, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter_by(id=data.group_id).first()
    year = db.query(models.AcademicYear).filter_by(id=data.academic_year_id).first()
    if not group or not year:
        raise HTTPException(status_code=404, detail="Группа или учебный год не найдены")

    start_date, end_date = sorted((data.start_date, data.end_date))
    terms = db.query(models.GroupTerm).filter_by(
        group_id=data.group_id, academic_year_id=data.academic_year_id
    ).all()
    if not terms:
        raise HTTPException(status_code=400, detail="Для выбранной группы нет семестров в этом учебном году")

    common_days_off = {
        item.day_date for item in db.query(models.AcademicDayOff).filter_by(academic_year_id=data.academic_year_id).all()
    }
    existing_dates = {
        item.day_date for item in db.query(models.GroupBreakDay).filter_by(
            group_id=data.group_id, academic_year_id=data.academic_year_id
        ).all()
    }
    new_items, removed = [], 0
    current = start_date
    while current <= end_date:
        in_term = any(term.start_date <= current <= term.end_date for term in terms)
        is_working_day = current.weekday() != 6 and (group.has_saturday or current.weekday() != 5)
        if in_term and is_working_day and current not in common_days_off and current not in existing_dates:
            item = models.GroupBreakDay(
                academic_year_id=data.academic_year_id,
                group_id=data.group_id,
                day_date=current,
                title=data.title or "Перерыв группы",
            )
            db.add(item)
            new_items.append(item)
            removed += remove_entries_for_dates(db, data.academic_year_id, current, current, group_id=data.group_id)
        current += timedelta(days=1)

    if not new_items:
        raise HTTPException(status_code=400, detail="В выбранном диапазоне нет доступных учебных дней")
    db.commit()
    for item in new_items:
        db.refresh(item)
    response.headers["X-Removed-Schedule-Entries"] = str(removed)
    response.headers["X-Added-Group-Break-Days"] = str(len(new_items))
    return new_items


@app.delete("/group-break-days/{break_day_id}")
def delete_group_break_day(break_day_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    item = db.query(models.GroupBreakDay).filter_by(id=break_day_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Перерыв группы не найден")
    db.delete(item); db.commit()
    return {"ok": True}


@app.post("/groups/", response_model=schemas.GroupOut)
def create_group(group: schemas.GroupCreate, db: Session = Depends(database.get_db)):
    validate_group_curator_assignment(None, group.curator_teacher_id, group.curator_room_name, db)
    db_group = models.Group(**group.dict())
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    ensure_group_first_term(db, db_group)
    return db_group


@app.get("/groups/", response_model=List[schemas.GroupOut])
def read_groups(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return db.query(models.Group).offset(skip).limit(limit).all()


@app.put("/groups/{group_id}", response_model=schemas.GroupOut)
def update_group(group_id: int, group_data: schemas.GroupCreate, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    validate_group_curator_assignment(group_id, group_data.curator_teacher_id, group_data.curator_room_name, db)
    group.number = group_data.number
    group.course = group_data.course
    group.has_saturday = group_data.has_saturday
    group.semester_weeks = group_data.semester_weeks
    group.curator_teacher_id = group_data.curator_teacher_id
    group.curator_room_name = group_data.curator_room_name
    db.commit()
    ensure_group_first_term(db, group)
    db.refresh(group)
    return group


def ensure_group_first_term(db: Session, group: models.Group):
    """Keep the legacy 'weeks in semester' field as the default first term length."""
    term = db.query(models.GroupTerm).filter_by(group_id=group.id, term_number=1).first()
    if term:
        if not term.is_locked and int(group.semester_weeks or 0) > 0:
            term.weeks = int(group.semester_weeks)
            if term.start_date:
                term.end_date = term.start_date + __import__('datetime').timedelta(days=term.weeks * 7 - 1)
            db.commit()
        return term
    year = db.query(models.AcademicYear).filter_by(is_active=True).first()
    if not year:
        year = models.AcademicYear(name="2026–2027", start_date=date(2026, 9, 1), end_date=date(2027, 8, 31), is_active=True)
        db.add(year); db.flush()
    weeks = max(1, int(group.semester_weeks or 20))
    term = models.GroupTerm(academic_year_id=year.id, group_id=group.id, term_number=1, name="1 семестр", start_week=1, weeks=weeks, start_date=year.start_date, end_date=year.start_date + __import__('datetime').timedelta(days=weeks * 7 - 1), is_active=True, is_locked=False)
    db.add(term); db.flush()
    db.query(models.CoursePlan).filter(models.CoursePlan.group_id == group.id, models.CoursePlan.term_id.is_(None)).update({"term_id": term.id}, synchronize_session=False)
    db.commit()
    return term

def validate_group_curator_assignment(group_id: int, teacher_id: Optional[int], room_name: Optional[str], db: Session):
    """A global curator slot is simultaneous for every group; resources cannot be shared."""
    if teacher_id is not None and not db.query(models.Teacher).filter_by(id=teacher_id).first():
        raise HTTPException(status_code=404, detail="Куратор не найден")
    room_name = (room_name or "").strip() or None
    slots = db.query(models.CuratorHour).filter(
        models.CuratorHour.is_active.is_(True), models.CuratorHour.group_id.in_([0, None])
    ).all()
    for slot in slots:
        occupied_entries = db.query(models.ScheduleEntry).filter(
            models.ScheduleEntry.day_of_week == slot.day_of_week,
            models.ScheduleEntry.time_slot >= slot.time_slot,
            models.ScheduleEntry.time_slot < slot.time_slot + slot.duration,
            models.ScheduleEntry.status != "canceled",
        ).all()
        if teacher_id and any(teacher_id in {entry.teacher_id, entry.teacher2_id} for entry in occupied_entries):
            raise HTTPException(status_code=409, detail="Этот куратор уже ведёт занятие в выбранный день и урок")
        if room_name and any((entry.room_name or '').strip() == room_name for entry in occupied_entries):
            raise HTTPException(status_code=409, detail="Этот кабинет уже занят в выбранный день и урок")
        for other in db.query(models.Group).filter(models.Group.id != group_id if group_id is not None else True).all():
            if teacher_id and teacher_id == other.curator_teacher_id:
                raise HTTPException(status_code=409, detail="Этот куратор уже назначен другой группе на общий кураторский час")
            if room_name and room_name == (other.curator_room_name or '').strip():
                raise HTTPException(status_code=409, detail="Этот кабинет уже назначен другой группе на общий кураторский час")

@app.patch("/groups/{group_id}/curator-assignment", response_model=schemas.GroupOut)
def update_group_curator_assignment(group_id: int, data: dict, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    teacher_id = data.get("curator_teacher_id")
    validate_group_curator_assignment(group_id, teacher_id, data.get("curator_room_name"), db)
    group.curator_teacher_id = teacher_id
    group.curator_room_name = (data.get("curator_room_name") or "").strip() or None
    db.commit(); db.refresh(group)
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


@app.get("/group-terms/", response_model=List[schemas.GroupTermOut])
def read_group_terms(group_id: Optional[int] = None, academic_year_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.GroupTerm).filter(models.GroupTerm.is_active.is_(True))
    if group_id:
        query = query.filter(models.GroupTerm.group_id == group_id)
    if academic_year_id:
        query = query.filter(models.GroupTerm.academic_year_id == academic_year_id)
    return query.order_by(models.GroupTerm.group_id, models.GroupTerm.term_number).all()


@app.post("/group-terms/", response_model=schemas.GroupTermOut)
def create_group_term(data: schemas.GroupTermCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter_by(id=data.group_id).first()
    if not group or data.end_date < data.start_date or data.term_number not in (1, 2):
        raise HTTPException(status_code=400, detail="Проверьте группу, даты и номер семестра")
    year = db.query(models.AcademicYear).filter_by(id=data.academic_year_id).first() if data.academic_year_id else db.query(models.AcademicYear).filter_by(is_active=True).first()
    if not year:
        year = models.AcademicYear(name=f"{data.start_date.year}–{data.end_date.year if data.end_date.year != data.start_date.year else data.start_date.year + 1}", start_date=data.start_date, end_date=data.end_date, is_active=True)
        db.add(year); db.flush()
    overlap = db.query(models.GroupTerm).filter(
        models.GroupTerm.group_id == data.group_id,
        models.GroupTerm.is_active.is_(True),
        models.GroupTerm.start_date <= data.end_date,
        models.GroupTerm.end_date >= data.start_date,
    ).first()
    if overlap:
        raise HTTPException(status_code=409, detail=f"Даты пересекаются с периодом «{overlap.name}» этой группы")
    start_week = calendar_week_for_date(year.start_date, data.start_date)
    weeks = max(1, ((data.end_date - data.start_date).days + 7) // 7)
    term = models.GroupTerm(academic_year_id=year.id, group_id=data.group_id, term_number=data.term_number, name=data.name, start_week=start_week, weeks=weeks, start_date=data.start_date, end_date=data.end_date, is_active=True, is_locked=False)
    db.add(term); db.commit(); db.refresh(term)
    return term


@app.put("/group-terms/{term_id}", response_model=schemas.GroupTermOut)
def update_group_term(term_id: int, data: schemas.GroupTermCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    term = db.query(models.GroupTerm).filter_by(id=term_id).first()
    if not term:
        raise HTTPException(status_code=404, detail="Семестр не найден")
    if term.is_locked:
        raise HTTPException(status_code=409, detail="Завершённый семестр нельзя менять: уроки в нём доступны только для ручного редактирования")
    if data.group_id != term.group_id:
        raise HTTPException(status_code=400, detail="Проверьте группу и дату начала семестра")
    from datetime import timedelta
    if data.end_date < data.start_date:
        raise HTTPException(status_code=400, detail="Дата окончания раньше даты начала")
    calculated_end = data.end_date
    weeks = max(1, ((calculated_end - data.start_date).days + 7) // 7)
    overlaps = db.query(models.GroupTerm).filter(
        models.GroupTerm.id != term.id,
        models.GroupTerm.group_id == term.group_id,
        models.GroupTerm.is_active.is_(True),
        models.GroupTerm.start_date <= calculated_end,
        models.GroupTerm.end_date >= data.start_date,
    ).first()
    if overlaps:
        raise HTTPException(status_code=409, detail=f"Даты пересекаются с периодом «{overlaps.name}» этой группы")
    year = db.query(models.AcademicYear).filter_by(id=term.academic_year_id).first()
    term.name = data.name.strip() or term.name
    term.start_date = data.start_date
    term.end_date = calculated_end
    term.start_week = calendar_week_for_date(year.start_date, data.start_date)
    term.weeks = weeks
    db.commit(); db.refresh(term)
    return term


@app.post("/group-terms/{term_id}/lock", response_model=schemas.GroupTermOut)
def lock_group_term(term_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    term = db.query(models.GroupTerm).filter_by(id=term_id).first()
    if not term: raise HTTPException(status_code=404, detail="Семестр не найден")
    term.is_locked = True
    db.commit(); db.refresh(term)
    return term

@app.delete("/group-terms/{term_id}")
def delete_group_term(term_id: int, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    term = db.query(models.GroupTerm).filter_by(id=term_id).first()
    if not term: raise HTTPException(status_code=404, detail="Семестр не найден")
    if term.is_locked: raise HTTPException(status_code=409, detail="Завершённый семестр нельзя удалить")
    db.delete(term); db.commit()
    return {"ok": True}


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
    if plan.term_id:
        term = db.query(models.GroupTerm).filter_by(id=plan.term_id, group_id=plan.group_id).first()
        if not term:
            raise HTTPException(status_code=400, detail="Выбранный семестр не принадлежит этой группе")
        plan.academic_year_id = term.academic_year_id
    db_plan = models.CoursePlan(**plan.dict())
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


@app.get("/course_plans/", response_model=List[schemas.CoursePlanOut])
def read_course_plans(skip: int = 0, limit: int = 100, academic_year_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.CoursePlan)
    if academic_year_id: query = query.filter(models.CoursePlan.academic_year_id == academic_year_id)
    return query.offset(skip).limit(limit).all()


@app.put("/course_plans/{plan_id}", response_model=schemas.CoursePlanOut)
def update_course_plan(plan_id: int, plan_data: schemas.CoursePlanCreate, db: Session = Depends(database.get_db)):
    plan = db.query(models.CoursePlan).filter(models.CoursePlan.id == plan_id).first()
    if plan_data.term_id and not db.query(models.GroupTerm).filter_by(id=plan_data.term_id, group_id=plan_data.group_id).first():
        raise HTTPException(status_code=400, detail="Выбранный семестр не принадлежит этой группе")
    plan.subject_name = plan_data.subject_name
    plan.total_hours = plan_data.total_hours
    plan.max_weekly_hours = plan_data.max_weekly_hours
    plan.group_id = plan_data.group_id
    plan.teacher_id = plan_data.teacher_id
    plan.teacher2_id = plan_data.teacher2_id
    plan.term_id = plan_data.term_id
    if plan_data.term_id:
        plan.academic_year_id = db.query(models.GroupTerm).filter_by(id=plan_data.term_id).first().academic_year_id
    else:
        plan.academic_year_id = None
    db.commit()
    db.refresh(plan)
    return plan


@app.delete("/course_plans/{plan_id}")
def delete_course_plan(plan_id: int, db: Session = Depends(database.get_db)):
    db.query(models.CoursePlan).filter(models.CoursePlan.id == plan_id).delete()
    db.commit()
    return {"ok": True}


@app.get("/plans-progress/")
def get_plans_progress(group_id: Optional[int] = None, academic_year_id: Optional[int] = None, term_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.CoursePlan)
    if group_id: query = query.filter(models.CoursePlan.group_id == group_id)
    if academic_year_id: query = query.filter(models.CoursePlan.academic_year_id == academic_year_id)
    if term_id: query = query.filter(models.CoursePlan.term_id == term_id)
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
        if p.term_id:
            actual_count = db.query(models.ScheduleEntry).filter(
                models.ScheduleEntry.group_id == p.group_id,
                models.ScheduleEntry.subject_name == p.subject_name,
                models.ScheduleEntry.term_id == p.term_id,
                models.ScheduleEntry.status != 'canceled'
            ).count()

        percentage = round((actual_count / p.total_hours * 100), 1) if p.total_hours > 0 else 0
        term = db.query(models.GroupTerm).filter_by(id=p.term_id).first() if p.term_id else None
        result.append({
            "plan_id": p.id, "group_id": p.group_id, "group_number": groups_dict.get(p.group_id, str(p.group_id)),
            "subject_name": p.subject_name, "teacher_name": teachers_dict.get(p.teacher_id, "Не назначен"),
            "total_hours": p.total_hours, "scheduled_hours": actual_count, "diff": actual_count - p.total_hours,
            "percentage": min(percentage, 100.0), "raw_percentage": percentage,
            "term_id": p.term_id, "term_name": term.name if term else "Семестр не указан"
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
    return db.query(models.CuratorHour).filter(models.CuratorHour.is_active.is_(True), models.CuratorHour.group_id.in_([0, None])).order_by(models.CuratorHour.day_of_week, models.CuratorHour.time_slot).all()


@app.get("/group-curator-hour-overrides/", response_model=List[schemas.GroupCuratorHourOverrideOut])
def read_group_curator_hour_overrides(group_id: int, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    return db.query(models.GroupCuratorHourOverride).filter_by(group_id=group_id).all()


@app.put("/group-curator-hour-overrides/{group_id}/{curator_hour_id}", response_model=schemas.GroupCuratorHourOverrideOut)
def save_group_curator_hour_override(group_id: int, curator_hour_id: int, data: schemas.GroupCuratorHourOverrideCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter_by(id=group_id).first()
    hour = db.query(models.CuratorHour).filter(
        models.CuratorHour.id == curator_hour_id,
        models.CuratorHour.is_active.is_(True),
        models.CuratorHour.group_id.in_([0, None]),
    ).first()
    if not group or not hour:
        raise HTTPException(status_code=404, detail="Группа или общий час не найдены")
    if data.schedule_date.isoweekday() != hour.day_of_week:
        raise HTTPException(status_code=400, detail="Дата не соответствует дню этого часа")
    if data.teacher_id is not None and not db.query(models.Teacher).filter_by(id=data.teacher_id).first():
        raise HTTPException(status_code=404, detail="Преподаватель не найден")
    room_name = (data.room_name or "").strip() or None
    item = db.query(models.GroupCuratorHourOverride).filter_by(
        group_id=group_id, curator_hour_id=curator_hour_id, schedule_date=data.schedule_date
    ).first()
    if not item:
        item = models.GroupCuratorHourOverride(group_id=group_id, curator_hour_id=curator_hour_id, schedule_date=data.schedule_date)
        db.add(item)
    item.teacher_id = data.teacher_id
    item.room_name = room_name
    item.is_hidden = data.is_hidden
    db.commit(); db.refresh(item)
    return item


@app.post("/curator-hours/", response_model=schemas.CuratorHourOut)
def create_curator_hour(data: schemas.CuratorHourCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    if not 1 <= data.day_of_week <= 6 or not 1 <= data.time_slot <= 12 or data.duration not in (1, 2):
        raise HTTPException(status_code=400, detail="Выберите корректный день, урок и длительность 1 или 2 урока")
    if data.duration == 2 and data.time_slot == 12:
        raise HTTPException(status_code=400, detail="Для двух уроков нужен не последний слот")
    group_id = data.group_id if data.group_id is not None else 0
    if data.hour_type not in {"curator", "information"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип часа")
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
    if data.hour_type not in {"curator", "information"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип часа")
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

@app.get("/archived-weeks/status-all")
def get_archive_status_all(week_number: int, db: Session = Depends(database.get_db)):
    """Return whether the selected week is locked for every current group."""
    group_ids = [row[0] for row in db.query(models.Group.id).all()]
    if not group_ids:
        return {"is_archived": False, "archived_count": 0, "total_groups": 0}
    archived_count = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.week_number == week_number,
        models.ArchivedWeek.group_id.in_(group_ids),
        models.ArchivedWeek.is_archived.is_(True),
    ).count()
    return {"is_archived": archived_count == len(group_ids), "archived_count": archived_count, "total_groups": len(group_ids)}

@app.post("/archived-weeks/toggle")
def toggle_archive(data: schemas.ArchivedWeekToggle, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter_by(group_id=data.group_id, week_number=data.week_number).first()
    if record: record.is_archived = not record.is_archived
    else: db.add(models.ArchivedWeek(group_id=data.group_id, week_number=data.week_number, is_archived=True))
    db.commit()
    return {"is_archived": record.is_archived if record else True}

def ensure_week_editable(group_id: int, week_number: int, db: Session):
    record = db.query(models.ArchivedWeek).filter_by(group_id=group_id, week_number=week_number).first()
    if record and record.is_archived:
        raise HTTPException(status_code=423, detail="Эта неделя заблокирована для изменений")

@app.post("/archived-weeks/toggle-all")
def toggle_archive_all(data: schemas.ArchivedWeekAllToggle, db: Session = Depends(database.get_db)):
    """Lock/unlock one week for every group atomically."""
    group_ids = [row[0] for row in db.query(models.Group.id).all()]
    if not group_ids:
        return {"is_archived": False, "archived_count": 0, "total_groups": 0}
    records = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.week_number == data.week_number,
        models.ArchivedWeek.group_id.in_(group_ids),
    ).all()
    by_group = {record.group_id: record for record in records}
    all_archived = all(by_group.get(group_id) and by_group[group_id].is_archived for group_id in group_ids)
    target_state = not all_archived
    for group_id in group_ids:
        record = by_group.get(group_id)
        if record:
            record.is_archived = target_state
        else:
            db.add(models.ArchivedWeek(group_id=group_id, week_number=data.week_number, is_archived=target_state))
    db.commit()
    return {"is_archived": target_state, "archived_count": len(group_ids) if target_state else 0, "total_groups": len(group_ids)}

@app.post("/generate_schedule/", response_model=schemas.GenerateResponse)
def trigger_generation(approve_adjustments: bool = False, db: Session = Depends(database.get_db)):
    # Генерируем ВЕСЬ семестр сразу
    success, msg = solver.trigger_global_generation(db, approve_adjustments=approve_adjustments)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


# ----------------- РАСПИСАНИЕ И СТАТУСЫ -----------------
@app.get("/schedule/", response_model=List[schemas.ScheduleEntryOut])
def get_schedule(group_id: Optional[int] = None, week_number: int = 1, term_id: Optional[int] = None, academic_year_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.week_number == week_number)
    if group_id: query = query.filter(models.ScheduleEntry.group_id == group_id)
    if term_id: query = query.filter(models.ScheduleEntry.term_id == term_id)
    if academic_year_id: query = query.filter(models.ScheduleEntry.academic_year_id == academic_year_id)
    return query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.time_slot).all()


def build_schedule_pdf(db: Session, week_number: int, day: Optional[int] = None, schedule_date: Optional[date] = None):
    font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"] if Path(path).exists()), None)
    if not font_path:
        raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
    pdfmetrics.registerFont(TTFont("ScheduleFont", font_path))
    bold_font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"] if Path(path).exists()), font_path)
    pdfmetrics.registerFont(TTFont("ScheduleFont-Bold", bold_font_path))
    group_records = {group.id: group for group in db.query(models.Group).all()}
    group_numbers = {group_id: group.number for group_id, group in group_records.items()}
    teachers = {teacher.id: teacher.name for teacher in db.query(models.Teacher).all()}
    curator_hours = db.query(models.CuratorHour).filter(models.CuratorHour.is_active.is_(True), models.CuratorHour.group_id.in_([0, None])).all()
    curator_overrides = {
        (item.group_id, item.curator_hour_id, item.schedule_date): item
        for item in db.query(models.GroupCuratorHourOverride).all()
    }
    common_day_off = bool(schedule_date and db.query(models.AcademicDayOff).filter_by(day_date=schedule_date).first())
    group_break_ids = {
        item.group_id for item in db.query(models.GroupBreakDay).filter_by(day_date=schedule_date).all()
    } if schedule_date else set()
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
                if common_day_off or group_id in group_break_ids:
                    continue
                override = curator_overrides.get((group_id, item.id, schedule_date)) if schedule_date else None
                if override and override.is_hidden:
                    continue
                slot_label = str(item.time_slot) if item.duration == 1 else f"{item.time_slot}-{item.time_slot + item.duration - 1}"
                group = group_records.get(group_id)
                curator_teacher_id = override.teacher_id if override and override.teacher_id is not None else (item.teacher_id or getattr(group, "curator_teacher_id", None))
                curator_room = override.room_name if override and override.room_name is not None else (item.room_name or getattr(group, "curator_room_name", None))
                hour_name = "Информационный час" if item.hour_type == "information" else "Кураторский час"
                rows_by_group.setdefault(group_id, []).append((
                    item.time_slot,
                    1,
                    [str(group_numbers.get(group_id, group_id)), slot_label, hour_name, curator_room or "-", teachers.get(curator_teacher_id, "Куратор не указан")],
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
    group_records = {group.id: group for group in db.query(models.Group).all()}
    groups = {group_id: group.number for group_id, group in group_records.items()}
    teachers = {teacher.id: teacher.name for teacher in db.query(models.Teacher).all()}
    curator_hours = db.query(models.CuratorHour).filter(models.CuratorHour.is_active.is_(True), models.CuratorHour.group_id.in_([0, None])).all()
    curator_overrides = {
        (item.group_id, item.curator_hour_id, item.schedule_date): item
        for item in db.query(models.GroupCuratorHourOverride).all()
    }
    common_day_off = bool(schedule_date and db.query(models.AcademicDayOff).filter_by(day_date=schedule_date).first())
    group_break_ids = {
        item.group_id for item in db.query(models.GroupBreakDay).filter_by(day_date=schedule_date).all()
    } if schedule_date else set()
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
        # Timetable grid: teachers form the rows, lesson numbers form the columns.
        # Paragraphs deliberately wrap full subject names instead of abbreviating them.
        rows_by_teacher = {}
        for entry in (entry for entry in entries if entry.day_of_week == selected_day):
            for teacher_id in {teacher_id for teacher_id in (entry.teacher_id, entry.teacher2_id) if teacher_id}:
                rows_by_teacher.setdefault(teacher_id, {})[entry.time_slot] = {
                    "subject": entry.subject_name or "—",
                    "group": groups.get(entry.group_id, entry.group_id),
                    "room": entry.room_name or "—",
                    "curator": False,
                }
        for item in (item for item in curator_hours if item.day_of_week == selected_day):
            target_groups = [item.group_id] if item.group_id not in (0, None) else list(groups)
            for group_id in target_groups:
                if common_day_off or group_id in group_break_ids:
                    continue
                override = curator_overrides.get((group_id, item.id, schedule_date)) if schedule_date else None
                if override and override.is_hidden:
                    continue
                group = group_records.get(group_id)
                curator_teacher_id = override.teacher_id if override and override.teacher_id is not None else (item.teacher_id or getattr(group, "curator_teacher_id", None))
                curator_room = override.room_name if override and override.room_name is not None else (item.room_name or getattr(group, "curator_room_name", None))
                if not curator_teacher_id:
                    continue
                group_label = f"гр. {groups.get(group_id, group_id)}"
                for slot in range(item.time_slot, item.time_slot + item.duration):
                    existing = rows_by_teacher.setdefault(curator_teacher_id, {}).get(slot)
                    if existing and existing.get("curator"):
                        existing["group"] = f"{existing['group']}, {group_label}"
                    else:
                        rows_by_teacher[curator_teacher_id][slot] = {
                            "subject": "Информационный час" if item.hour_type == "information" else "Кураторский час",
                            "group": group_label,
                            "room": curator_room or "—",
                            "curator": True,
                        }

        lesson_slots = list(range(1, max([entry.time_slot for entry in entries if entry.day_of_week == selected_day] + [item.time_slot + item.duration - 1 for item in curator_hours if item.day_of_week == selected_day] + [12]) + 1))
        rows = [["Преподаватель"] + [str(slot) for slot in lesson_slots]]
        ordered_teachers = sorted(rows_by_teacher, key=lambda teacher_id: teachers.get(teacher_id, "").casefold())
        cell_style = ParagraphStyle("TeachersGridCell", fontName="ScheduleFont", fontSize=6.4, leading=7.4, alignment=1, textColor=colors.HexColor("#0f172a"))
        for teacher_id in ordered_teachers:
            row = [Paragraph(teachers.get(teacher_id, f"Преподаватель {teacher_id}"), ParagraphStyle("TeacherName", parent=cell_style, fontName="ScheduleFont-Bold", fontSize=7.3, leading=8.5, textColor=colors.HexColor("#0f766e")))]
            for slot in lesson_slots:
                item = rows_by_teacher[teacher_id].get(slot)
                if not item:
                    row.append("")
                    continue
                label = f"{item['subject']}<br/><font size=5.7>Гр. {item['group']} · каб. {item['room']}</font>"
                row.append(Paragraph(label, cell_style))
            rows.append(row)

        page_width = landscape(A4)[0] - 16 * mm
        teacher_width = min(48 * mm, max(36 * mm, page_width * 0.18))
        slot_width = (page_width - teacher_width) / len(lesson_slots)
        table = Table(rows, colWidths=[teacher_width] + [slot_width] * len(lesson_slots), repeatRows=1, hAlign="CENTER")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "ScheduleFont"),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for row_index in range(1, len(rows)):
            style.append(("BACKGROUND", (0, row_index), (0, row_index), colors.HexColor("#e8f3f1") if row_index % 2 else colors.HexColor("#eef2f7")))
        table.setStyle(TableStyle(style))
        story.append(table)
        if index < len(days) - 1:
            story.append(PageBreak())
    document.build(story)
    buffer.seek(0)
    return buffer


def build_hours_statement_pdf(db: Session, group_id: int, start_date: date, end_date: date):
    group = db.query(models.Group).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="Дата окончания раньше даты начала")

    font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"] if Path(path).exists()), None)
    if not font_path:
        raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
    pdfmetrics.registerFont(TTFont("StatementFont", font_path))
    bold_font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"] if Path(path).exists()), font_path)
    pdfmetrics.registerFont(TTFont("StatementFont-Bold", bold_font_path))

    term_by_id = {term.id: term for term in db.query(models.GroupTerm).filter_by(group_id=group.id).all()}
    teachers = {teacher.id: teacher.name for teacher in db.query(models.Teacher).all()}
    plans = []
    for plan in db.query(models.CoursePlan).filter_by(group_id=group.id).all():
        term = term_by_id.get(plan.term_id)
        if term and (term.end_date < start_date or term.start_date > end_date):
            continue
        plans.append(plan)

    entries = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.group_id == group.id,
        models.ScheduleEntry.status != "canceled",
        models.ScheduleEntry.schedule_date.isnot(None),
        models.ScheduleEntry.schedule_date >= start_date,
        models.ScheduleEntry.schedule_date <= end_date,
    ).order_by(models.ScheduleEntry.schedule_date, models.ScheduleEntry.time_slot).all()

    def signature(term_id, subject_name, teacher_id, teacher2_id):
        return (term_id, (subject_name or "").strip(), teacher_id, teacher2_id)

    plans_by_signature = {
        signature(plan.term_id, plan.subject_name, plan.teacher_id, plan.teacher2_id): plan
        for plan in plans
    }
    rows_by_key = {}
    for plan in plans:
        key = ("plan", plan.id)
        rows_by_key[key] = {
            "subject": plan.subject_name or "Без названия",
            "teacher": " / ".join(filter(None, [teachers.get(plan.teacher_id), teachers.get(plan.teacher2_id)])) or "Не назначен",
            "plan": int(plan.total_hours or 0),
            "hours": {},
        }
    for entry in entries:
        plan = plans_by_signature.get(signature(entry.term_id, entry.subject_name, entry.teacher_id, entry.teacher2_id))
        key = ("plan", plan.id) if plan else ("entry", entry.term_id, entry.subject_name, entry.teacher_id, entry.teacher2_id)
        if key not in rows_by_key:
            rows_by_key[key] = {
                "subject": entry.subject_name or "Без названия",
                "teacher": " / ".join(filter(None, [teachers.get(entry.teacher_id), teachers.get(entry.teacher2_id)])) or "Не назначен",
                "plan": 0,
                "hours": {},
            }
        rows_by_key[key]["hours"][entry.schedule_date] = rows_by_key[key]["hours"].get(entry.schedule_date, 0) + 1

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=8 * mm, rightMargin=8 * mm, topMargin=9 * mm, bottomMargin=9 * mm)
    title = ParagraphStyle("StatementTitle", fontName="StatementFont-Bold", fontSize=15, leading=18, alignment=1, textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    subtitle = ParagraphStyle("StatementSubtitle", fontName="StatementFont", fontSize=9, leading=12, alignment=1, textColor=colors.HexColor("#475569"), spaceAfter=4)
    cell_subject = ParagraphStyle("StatementSubject", fontName="StatementFont-Bold", fontSize=6.3, leading=7.6, textColor=colors.HexColor("#0f172a"))
    cell_teacher = ParagraphStyle("StatementTeacher", fontName="StatementFont", fontSize=6.1, leading=7.4, textColor=colors.HexColor("#0f172a"))
    months_ru = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    story = [
        Paragraph("ВЕДОМОСТЬ УЧЁТА ПРОВЕДЁННЫХ ЧАСОВ", title),
        Paragraph(f"Группа {group.number}  •  период: {start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}", subtitle),
        Spacer(1, 3 * mm),
    ]

    month_start = date(start_date.year, start_date.month, 1)
    while month_start <= end_date:
        next_month = date(month_start.year + (month_start.month == 12), 1 if month_start.month == 12 else month_start.month + 1, 1)
        month_end = next_month - timedelta(days=1)
        visible_start, visible_end = max(start_date, month_start), min(end_date, month_end)
        days = [visible_start + timedelta(days=offset) for offset in range((visible_end - visible_start).days + 1)]
        story.append(Paragraph(f"{months_ru[month_start.month - 1]} {month_start.year}", ParagraphStyle("StatementMonth", parent=subtitle, fontName="StatementFont-Bold", fontSize=11, textColor=colors.HexColor("#0f766e"), spaceAfter=2)))
        header = ["№", "Предмет", "Преподаватель"] + [str(day.day) for day in days] + ["Итого", "План"]
        table_rows = [header]
        ordered_rows = sorted(rows_by_key.values(), key=lambda row: (row["subject"].casefold(), row["teacher"].casefold()))
        for index, row in enumerate(ordered_rows, 1):
            monthly_total = sum(row["hours"].get(day, 0) for day in days)
            table_rows.append([
                str(index),
                Paragraph(escape(row["subject"]), cell_subject),
                Paragraph(escape(row["teacher"]), cell_teacher),
            ] + [str(row["hours"].get(day, "")) for day in days] + [str(monthly_total) if monthly_total else "", str(row["plan"]) if row["plan"] else "—"])
        if not ordered_rows:
            table_rows.append(["", "Занятий за выбранный период нет", ""] + [""] * (len(days) + 2))
        fixed_width = 7 * mm + 46 * mm + 41 * mm + 10 * mm + 10 * mm
        page_width = landscape(A4)[0] - document.leftMargin - document.rightMargin
        day_width = max(4.3 * mm, (page_width - fixed_width) / max(1, len(days)))
        widths = [7 * mm, 46 * mm, 41 * mm] + [day_width] * len(days) + [10 * mm, 10 * mm]
        table = Table(table_rows, colWidths=widths, repeatRows=1)
        style = [
            ("FONTNAME", (0, 0), (-1, 0), "StatementFont-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 6.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 1), (-1, -1), "StatementFont"), ("FONTSIZE", (0, 1), (-1, -1), 6.3),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"), ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for row_index in range(1, len(table_rows)):
            if row_index % 2 == 0:
                style.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#f8fafc")))
        table.setStyle(TableStyle(style))
        story.append(table)
        if next_month <= end_date:
            story.append(PageBreak())
        month_start = next_month
    document.build(story)
    buffer.seek(0)
    return buffer


def build_teacher_hours_statement_pdf(db: Session, teacher_id: int, start_date: date, end_date: date):
    teacher = db.query(models.Teacher).filter_by(id=teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="Дата окончания раньше даты начала")

    font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"] if Path(path).exists()), None)
    if not font_path:
        raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
    pdfmetrics.registerFont(TTFont("TeacherStatementFont", font_path))
    bold_font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"] if Path(path).exists()), font_path)
    pdfmetrics.registerFont(TTFont("TeacherStatementFont-Bold", bold_font_path))

    groups = {group.id: group for group in db.query(models.Group).all()}
    plans = db.query(models.CoursePlan).filter(
        (models.CoursePlan.teacher_id == teacher_id) | (models.CoursePlan.teacher2_id == teacher_id)
    ).all()
    plan_by_group = {}
    for plan in plans:
        plan_by_group[plan.group_id] = plan_by_group.get(plan.group_id, 0) + int(plan.total_hours or 0)
    entries = db.query(models.ScheduleEntry).filter(
        (models.ScheduleEntry.teacher_id == teacher_id) | (models.ScheduleEntry.teacher2_id == teacher_id),
        models.ScheduleEntry.status != "canceled",
        models.ScheduleEntry.schedule_date.isnot(None),
        models.ScheduleEntry.schedule_date >= start_date,
        models.ScheduleEntry.schedule_date <= end_date,
    ).order_by(models.ScheduleEntry.schedule_date, models.ScheduleEntry.time_slot).all()
    actual = {}
    for entry in entries:
        actual[(entry.group_id, entry.schedule_date)] = actual.get((entry.group_id, entry.schedule_date), 0) + 1
    group_ids = sorted(set(plan_by_group) | {entry.group_id for entry in entries}, key=lambda group_id: int(groups.get(group_id).number if groups.get(group_id) else group_id))
    if not group_ids:
        group_ids = []

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=8 * mm, rightMargin=8 * mm, topMargin=9 * mm, bottomMargin=9 * mm)
    title = ParagraphStyle("TeacherStatementTitle", fontName="TeacherStatementFont-Bold", fontSize=15, leading=18, alignment=1, textColor=colors.HexColor("#0f172a"), spaceAfter=2)
    subtitle = ParagraphStyle("TeacherStatementSubtitle", fontName="TeacherStatementFont", fontSize=9, leading=12, alignment=1, textColor=colors.HexColor("#475569"), spaceAfter=4)
    month_cell = ParagraphStyle("TeacherStatementMonthCell", fontName="TeacherStatementFont-Bold", fontSize=7, leading=8, alignment=0, textColor=colors.HexColor("#0f172a"))
    months_ru = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    story = [
        Paragraph("УЧЁТ ЧАСОВ УЧЕБНОЙ РАБОТЫ", title),
        Paragraph(f"Преподаватель: {escape(teacher.name)}  •  период: {start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}", subtitle),
        Spacer(1, 3 * mm),
    ]
    page_width = landscape(A4)[0] - document.leftMargin - document.rightMargin
    months = []
    month_start = date(start_date.year, start_date.month, 1)
    while month_start <= end_date:
        next_month = date(month_start.year + (month_start.month == 12), 1 if month_start.month == 12 else month_start.month + 1, 1)
        visible_start, visible_end = max(start_date, month_start), min(end_date, next_month - timedelta(days=1))
        days = [visible_start + timedelta(days=offset) for offset in range((visible_end - visible_start).days + 1)]
        months.append((months_ru[month_start.month - 1], month_start.year, days))
        month_start = next_month
    groups_per_page = 12
    chunks = [group_ids[index:index + groups_per_page] for index in range(0, len(group_ids), groups_per_page)] or [[]]
    for chunk_index, chunk in enumerate(chunks):
        if chunk_index:
            story.append(PageBreak())
        header = ["Месяц"] + [str(groups[group_id].number) for group_id in chunk] + ["Итого"]
        rows = [header]
        month_values = []
        for month_name, year, days in months:
            values = [sum(actual.get((group_id, day), 0) for day in days) or "" for group_id in chunk]
            month_total = sum(value for value in values if isinstance(value, int))
            month_values.append((values, month_total))
            rows.append([Paragraph(escape(month_name), month_cell)] + values + [month_total or ""])
        total_values = [sum(value for values, _ in month_values for value in [values[index]] if isinstance(value, int)) or "" for index in range(len(chunk))]
        total_actual = sum(value for value in total_values if isinstance(value, int))
        plan_values = [plan_by_group.get(group_id, 0) or "" for group_id in chunk]
        plan_total = sum(plan_by_group.get(group_id, 0) for group_id in chunk)
        rows.extend([
            ["Всего"] + total_values + [total_actual or ""],
            ["По плану"] + plan_values + [plan_total or ""],
            ["Не выполнено"] + [max(0, (plan_by_group.get(group_id, 0) - (total_values[index] or 0))) or "" for index, group_id in enumerate(chunk)] + [max(0, plan_total - total_actual) or ""],
            ["Сверх плана"] + [max(0, ((total_values[index] or 0) - plan_by_group.get(group_id, 0))) or "" for index, group_id in enumerate(chunk)] + [max(0, total_actual - plan_total) or ""],
        ])
        first_width = 23 * mm
        group_width = (page_width - first_width) / max(1, len(chunk) + 1)
        table = Table(rows, colWidths=[first_width] + [group_width] * (len(chunk) + 1), repeatRows=1)
        style = [
            ("FONTNAME", (0, 0), (-1, 0), "TeacherStatementFont-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")), ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("FONTNAME", (0, len(rows) - 4), (-1, -1), "TeacherStatementFont-Bold"), ("LINEABOVE", (0, len(rows) - 4), (-1, len(rows) - 4), 0.8, colors.HexColor("#0f172a")),
        ]
        for row_index in range(1, len(rows)):
            if row_index % 2 == 0:
                style.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#f8fafc")))
        table.setStyle(TableStyle(style))
        story.append(table)
    document.build(story)
    buffer.seek(0)
    return buffer


@app.get("/export/hours-statement.pdf")
def export_hours_statement_pdf(group_id: int, start_date: date, end_date: date, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    filename = f"vedomost_group_{group_id}_{start_date.isoformat()}_{end_date.isoformat()}.pdf"
    return StreamingResponse(build_hours_statement_pdf(db, group_id, start_date, end_date), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/export/teacher-hours-statement.pdf")
def export_teacher_hours_statement_pdf(teacher_id: int, start_date: date, end_date: date, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    filename = f"vedomost_teacher_{teacher_id}_{start_date.isoformat()}_{end_date.isoformat()}.pdf"
    return StreamingResponse(build_teacher_hours_statement_pdf(db, teacher_id, start_date, end_date), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def resolved_export_schedule_date(db: Session, week_number: int, day: Optional[int], academic_year_id: Optional[int], fallback: Optional[date]) -> Optional[date]:
    if day is None:
        return fallback
    year = db.query(models.AcademicYear).filter_by(id=academic_year_id).first() if academic_year_id else db.query(models.AcademicYear).filter_by(is_active=True).first()
    if not year:
        return fallback
    first_monday = year.start_date - timedelta(days=year.start_date.isoweekday() - 1)
    return first_monday + timedelta(days=(week_number - 1) * 7 + day - 1)


@app.get("/export/schedule.pdf")
def export_schedule_pdf(week_number: int = 1, day: Optional[int] = None, schedule_date: Optional[date] = None, academic_year_id: Optional[int] = None, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    schedule_date = resolved_export_schedule_date(db, week_number, day, academic_year_id, schedule_date)
    date_suffix = f"_{schedule_date.isoformat()}" if schedule_date else ""
    filename = f"schedule_week_{week_number}{date_suffix}" + (f"_day_{day}" if day else "") + ".pdf"
    return StreamingResponse(build_schedule_pdf(db, week_number, day, schedule_date), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/export/teachers.pdf")
def export_teachers_schedule_pdf(week_number: int = 1, day: Optional[int] = None, schedule_date: Optional[date] = None, academic_year_id: Optional[int] = None, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    schedule_date = resolved_export_schedule_date(db, week_number, day, academic_year_id, schedule_date)
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


def entry_calendar_date(data: schemas.ScheduleEntryBase, db: Session) -> Optional[date]:
    if data.schedule_date:
        return data.schedule_date
    year = db.query(models.AcademicYear).filter_by(id=data.academic_year_id).first() if data.academic_year_id else None
    if not year:
        return None
    monday = year.start_date - timedelta(days=year.start_date.isoweekday() - 1)
    return monday + timedelta(days=(data.week_number - 1) * 7 + data.day_of_week - 1)


def is_teacher_on_date_vacation(teacher_id: int, academic_year_id: Optional[int], target_date: Optional[date], db: Session) -> bool:
    if not target_date or not academic_year_id:
        return False
    return db.query(models.TeacherVacation).filter(
        models.TeacherVacation.teacher_id == teacher_id,
        models.TeacherVacation.academic_year_id == academic_year_id,
        models.TeacherVacation.start_date <= target_date,
        models.TeacherVacation.end_date >= target_date,
    ).first() is not None


def validate_schedule_conflicts(data: schemas.ScheduleEntryBase, db: Session, exclude_entry_id: Optional[int] = None):
    target_date = entry_calendar_date(data, db)
    if data.term_id and target_date:
        term = db.query(models.GroupTerm).filter_by(id=data.term_id, group_id=data.group_id).first()
        if term and (target_date < term.start_date or target_date > term.end_date):
            raise HTTPException(status_code=409, detail="Дата занятия находится за пределами выбранного семестра")
    if target_date and target_date.isoweekday() == 7:
        raise HTTPException(status_code=409, detail="В воскресенье занятия не проводятся")
    if target_date and data.academic_year_id and db.query(models.AcademicDayOff).filter_by(academic_year_id=data.academic_year_id, day_date=target_date).first():
        raise HTTPException(status_code=409, detail="Эта дата объявлена общим выходным")
    if target_date and data.academic_year_id and db.query(models.GroupBreakDay).filter_by(
        academic_year_id=data.academic_year_id, group_id=data.group_id, day_date=target_date
    ).first():
        raise HTTPException(status_code=409, detail="Для этой группы установлен перерыв на выбранную дату")
    teacher_ids = {teacher_id for teacher_id in (data.teacher_id, data.teacher2_id) if teacher_id}
    selected_teachers = db.query(models.Teacher).filter(models.Teacher.id.in_(teacher_ids)).all()
    if len(selected_teachers) != len(teacher_ids):
        raise HTTPException(status_code=400, detail="Выбранный преподаватель не найден")
    for teacher in selected_teachers:
        if not teacher_works_on_day(teacher, data.day_of_week):
            raise HTTPException(status_code=409, detail=f"Преподаватель {teacher.name} не работает в выбранный день")
        if teacher_is_on_vacation(teacher, data.week_number):
            raise HTTPException(status_code=409, detail=f"Преподаватель {teacher.name} находится в отпуске на этой неделе")
        if is_teacher_on_date_vacation(teacher.id, data.academic_year_id, target_date, db):
            raise HTTPException(status_code=409, detail=f"Преподаватель {teacher.name} находится в отпуске в эту дату")
    blocked_hours = db.query(models.CuratorHour).filter(
        models.CuratorHour.group_id.in_([0, data.group_id]),
        models.CuratorHour.day_of_week == data.day_of_week,
        models.CuratorHour.is_active.is_(True),
        models.CuratorHour.time_slot <= data.time_slot,
        models.CuratorHour.time_slot + models.CuratorHour.duration > data.time_slot,
    ).all()
    hidden_hour_ids = {
        item.curator_hour_id for item in db.query(models.GroupCuratorHourOverride).filter_by(
            group_id=data.group_id, schedule_date=target_date, is_hidden=True
        ).all()
    }
    blocked = next((item for item in blocked_hours if item.id not in hidden_hour_ids), None)
    if blocked:
        raise HTTPException(status_code=409, detail="Этот слот заблокирован кураторским часом")
    group = db.query(models.Group).filter_by(id=data.group_id).first()
    global_hours = db.query(models.CuratorHour).filter(
        models.CuratorHour.group_id.in_([0, None]), models.CuratorHour.day_of_week == data.day_of_week,
        models.CuratorHour.is_active.is_(True), models.CuratorHour.time_slot <= data.time_slot,
        models.CuratorHour.time_slot + models.CuratorHour.duration > data.time_slot,
    ).all()
    global_hours = [item for item in global_hours if item.id not in hidden_hour_ids]
    if group and global_hours:
        if group.curator_teacher_id and group.curator_teacher_id in teacher_ids:
            raise HTTPException(status_code=409, detail="Куратор группы уже занят общим часом")
        if group.curator_room_name and data.room_name.strip() == group.curator_room_name.strip():
            raise HTTPException(status_code=409, detail="Кабинет группы занят общим часом")
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
    ensure_week_editable(entry_data.group_id, entry_data.week_number, db)
    if entry_data.term_id:
        term = db.query(models.GroupTerm).filter_by(id=entry_data.term_id, group_id=entry_data.group_id).first()
        if term: entry_data.academic_year_id = term.academic_year_id
    entry_data.schedule_date = entry_calendar_date(entry_data, db)
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
    ensure_week_editable(entry.group_id, entry.week_number, db)
    ensure_week_editable(update_data.group_id, update_data.week_number, db)
    if update_data.term_id:
        term = db.query(models.GroupTerm).filter_by(id=update_data.term_id, group_id=update_data.group_id).first()
        if term: update_data.academic_year_id = term.academic_year_id
    update_data.schedule_date = entry_calendar_date(update_data, db)
    validate_schedule_conflicts(update_data, db, exclude_entry_id=entry_id)
    for key, value in update_data.dict().items(): setattr(entry, key, value)
    db.commit()
    db.refresh(entry)
    return entry

@app.delete("/schedule/{entry_id}")
def delete_schedule_entry(entry_id: int, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Занятие не найдено")
    ensure_week_editable(entry.group_id, entry.week_number, db)
    db.delete(entry)
    db.commit()
    return {"ok": True}

# --- НОВЫЕ ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ БОЛЕЗНЯМИ ---
@app.post("/schedule/{entry_id}/cancel", response_model=schemas.ScheduleEntryOut)
def cancel_schedule_entry(entry_id: int, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Занятие не найдено")
    ensure_week_editable(entry.group_id, entry.week_number, db)
    entry.status = "canceled"
    db.commit()
    db.refresh(entry)
    return entry

@app.post("/schedule/{entry_id}/restore", response_model=schemas.ScheduleEntryOut)
def restore_schedule_entry(entry_id: int, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Занятие не найдено")
    ensure_week_editable(entry.group_id, entry.week_number, db)
    entry.status = "planned"
    db.commit()
    db.refresh(entry)
    return entry


# Frontend is served by the same process, so Docker needs only one service.
frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
