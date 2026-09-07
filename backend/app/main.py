import hashlib
import hmac
import os
import secrets
import time
import io
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
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
if "working_days" not in {column["name"] for column in inspect(database.engine).get_columns("teachers")}:
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE teachers ADD COLUMN working_days VARCHAR DEFAULT '1,2,3,4,5' NOT NULL"))

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
    public_paths = {"/login.html", "/auth/login", "/docs", "/openapi.json", "/redoc"}
    public_asset = request.url.path.endswith((".js", ".css"))
    if request.url.path not in public_paths and not public_asset and not request.url.path.startswith(("/docs/", "/redoc/")):
        db = database.SessionLocal()
        try:
            current_user(request, db)
        except HTTPException:
            if request.url.path.endswith(".html") or request.url.path == "/":
                return Response(status_code=307, headers={"Location": "/login.html"})
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
    return {"login": user.login, "is_admin": user.is_admin}


@app.get("/users/", response_model=List[schemas.UserOut])
def read_users(_: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    return db.query(models.User).order_by(models.User.login).all()


@app.post("/users/", response_model=schemas.UserOut)
def create_user(data: schemas.UserCreate, _: models.User = Depends(require_admin), db: Session = Depends(database.get_db)):
    login_name = data.login.strip()
    if not login_name or not data.password:
        raise HTTPException(status_code=400, detail="Логин и пароль обязательны")
    if db.query(models.User).filter_by(login=login_name).first():
        raise HTTPException(status_code=409, detail="Такой логин уже существует")
    user = models.User(login=login_name, password_hash=hash_password(data.password), is_admin=data.is_admin)
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

        for p in plans:
            total_semester_hours += p.total_hours
            g = db.query(models.Group).filter(models.Group.id == p.group_id).first()
            weeks = getattr(g, 'semester_weeks', 20) or 20
            total_weekly_hours_estimated += (p.total_hours / weeks)

        result.append({
            "id": t.id,
            "name": t.name,
            "room_name": rooms_dict.get(t.room_id, "Без кабинета"),
            "max_hours_per_week": t.max_hours_per_week,
            "assigned_weekly_hours": round(total_weekly_hours_estimated, 1),
            "total_semester_hours": total_semester_hours
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
def trigger_generation(db: Session = Depends(database.get_db)):
    # Генерируем ВЕСЬ семестр сразу
    success, msg = solver.trigger_global_generation(db)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}


# ----------------- РАСПИСАНИЕ И СТАТУСЫ -----------------
@app.get("/schedule/", response_model=List[schemas.ScheduleEntryOut])
def get_schedule(group_id: Optional[int] = None, week_number: int = 1, db: Session = Depends(database.get_db)):
    query = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.week_number == week_number)
    if group_id: query = query.filter(models.ScheduleEntry.group_id == group_id)
    return query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.time_slot).all()


def build_schedule_pdf(db: Session, week_number: int, day: Optional[int] = None):
    font_path = next((path for path in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial Unicode.ttf"] if Path(path).exists()), None)
    if not font_path:
        raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
    pdfmetrics.registerFont(TTFont("ScheduleFont", font_path))
    groups = {group.id: group.number for group in db.query(models.Group).all()}
    teachers = {teacher.id: teacher.name for teacher in db.query(models.Teacher).all()}
    query = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.week_number == week_number, models.ScheduleEntry.status != "canceled")
    if day is not None:
        query = query.filter(models.ScheduleEntry.day_of_week == day)
    entries = query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.group_id, models.ScheduleEntry.time_slot).all()
    days = [day] if day else sorted({entry.day_of_week for entry in entries}) or list(range(1, 6))
    day_names = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА"]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=5 * mm, rightMargin=5 * mm, topMargin=10 * mm, bottomMargin=10 * mm)
    title = ParagraphStyle("Title", fontName="ScheduleFont", fontSize=15, leading=18, alignment=1, spaceAfter=2)
    story = []
    for index, selected_day in enumerate(days):
        story.extend([Paragraph("РАСПИСАНИЕ НА", title), Paragraph(f"{day_names[selected_day - 1]} | НЕДЕЛЯ {week_number}", title), Spacer(1, 4 * mm)])
        day_entries = [item for item in entries if item.day_of_week == selected_day]
        rows = [["№ гр", "№ ур", "Предмет", "Ауд", "Преподаватель"]]
        for entry in day_entries:
            name = teachers.get(entry.teacher_id, "Не назначен")
            if entry.teacher2_id:
                name += f" / {teachers.get(entry.teacher2_id, '')}"
            rows.append([str(groups.get(entry.group_id, entry.group_id)), str(entry.time_slot), entry.subject_name, entry.room_name or "-", name])
        subject_width = max([stringWidth(str(entry.subject_name or "Предмет"), "ScheduleFont", 8) + 10 * mm for entry in day_entries] or [45 * mm])
        subject_width = min(max(subject_width, 45 * mm), 130 * mm)
        table = Table(rows, colWidths=[18 * mm, 16 * mm, subject_width, 23 * mm, 41 * mm], repeatRows=1, hAlign="CENTER")
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, -1), "ScheduleFont"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(table)
        if index < len(days) - 1: story.append(PageBreak())
    document.build(story)
    buffer.seek(0)
    return buffer


@app.get("/export/schedule.pdf")
def export_schedule_pdf(week_number: int = 1, day: Optional[int] = None, _: models.User = Depends(require_user), db: Session = Depends(database.get_db)):
    filename = f"schedule_week_{week_number}" + (f"_day_{day}" if day else "") + ".pdf"
    return StreamingResponse(build_schedule_pdf(db, week_number, day), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@app.post("/schedule/", response_model=schemas.ScheduleEntryOut)
def create_schedule_entry(entry_data: schemas.ScheduleEntryCreate, db: Session = Depends(database.get_db)):
    new_entry = models.ScheduleEntry(**entry_data.dict())
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return new_entry

@app.put("/schedule/{entry_id}", response_model=schemas.ScheduleEntryOut)
def update_schedule_entry(entry_id: int, update_data: schemas.ScheduleEntryUpdate, db: Session = Depends(database.get_db)):
    entry = db.query(models.ScheduleEntry).filter_by(id=entry_id).first()
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
