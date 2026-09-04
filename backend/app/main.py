from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, text

from . import models, schemas, database, solver

models.Base.metadata.create_all(bind=database.engine)

try:
    with database.engine.begin() as conn:
        cols_cp = [r[1] for r in conn.execute(text("PRAGMA table_info(course_plans)")).fetchall()]
        if "max_weekly_hours" not in cols_cp:
            conn.execute(text("ALTER TABLE course_plans ADD COLUMN max_weekly_hours INTEGER DEFAULT 4"))

        cols_gr = [r[1] for r in conn.execute(text("PRAGMA table_info(groups)")).fetchall()]
        if "course" not in cols_gr:
            conn.execute(text("ALTER TABLE groups ADD COLUMN course INTEGER DEFAULT 1"))
except Exception as e:
    print("Ошибка миграции БД:", e)

app = FastAPI(title="Schedule System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/teachers/", response_model=schemas.TeacherOut)
def create_teacher(teacher: schemas.TeacherCreate, db: Session = Depends(database.get_db)):
    db_teacher = models.Teacher(**teacher.dict())
    db.add(db_teacher)
    db.commit()
    db.refresh(db_teacher)
    return db_teacher


@app.get("/teachers/", response_model=List[schemas.TeacherOut])
def read_teachers(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return db.query(models.Teacher).offset(skip).limit(limit).all()


@app.put("/teachers/{teacher_id}", response_model=schemas.TeacherOut)
def update_teacher(teacher_id: int, teacher_data: schemas.TeacherCreate, db: Session = Depends(database.get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    for key, value in teacher_data.dict().items():
        setattr(teacher, key, value)
    db.commit()
    db.refresh(teacher)
    return teacher


@app.delete("/teachers/{teacher_id}")
def delete_teacher(teacher_id: int, db: Session = Depends(database.get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    db.delete(teacher)
    db.commit()
    return {"ok": True}


@app.get("/teachers-workload/")
def get_teachers_workload(db: Session = Depends(database.get_db)):
    teachers = db.query(models.Teacher).all()
    result = []
    for t in teachers:
        plans = db.query(models.CoursePlan).filter(
            or_(
                models.CoursePlan.teacher_id == t.id,
                models.CoursePlan.teacher2_id == t.id
            )
        ).all()
        # Считаем сумму часов на семестр и делим на 20 недель
        total_semester_hours = sum(p.total_hours for p in plans)
        weekly_hours = total_semester_hours / 20.0

        result.append({
            "id": t.id,
            "name": t.name,
            "default_room": t.default_room,
            "max_hours_per_week": t.max_hours_per_week,
            "assigned_weekly_hours": round(weekly_hours, 1),
            "total_semester_hours": total_semester_hours
        })
    return result


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
    db.commit()
    db.refresh(group)
    return group


@app.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    db.delete(group)
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
    plan.max_weekly_hours = plan_data.max_weekly_hours  # <-- ВОТ ОНО
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
        actual_count = db.query(models.ScheduleEntry).filter_by(group_id=p.group_id,
                                                                subject_name=p.subject_name).count()
        percentage = round((actual_count / p.total_hours * 100), 1) if p.total_hours > 0 else 0
        result.append({
            "plan_id": p.id, "group_id": p.group_id, "group_number": groups_dict.get(p.group_id, str(p.group_id)),
            "subject_name": p.subject_name, "teacher_name": teachers_dict.get(p.teacher_id, "Не назначен"),
            "total_hours": p.total_hours, "scheduled_hours": actual_count, "diff": actual_count - p.total_hours,
            "percentage": min(percentage, 100.0), "raw_percentage": percentage
        })
    return result


@app.get("/archived-weeks/status")
def get_archive_status(group_id: int, week_number: int, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter_by(group_id=group_id, week_number=week_number).first()
    return {"is_archived": record.is_archived if record else False}


@app.post("/archived-weeks/toggle")
def toggle_archive(data: schemas.ArchivedWeekToggle, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter_by(group_id=data.group_id, week_number=data.week_number).first()
    if record:
        record.is_archived = not record.is_archived
    else:
        db.add(models.ArchivedWeek(group_id=data.group_id, week_number=data.week_number, is_archived=True))
    db.commit()
    return {"is_archived": record.is_archived if record else True}


@app.post("/generate_schedule/")
def trigger_generation(group_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    teachers = db.query(models.Teacher).filter_by(is_active=True).all()
    groups = db.query(models.Group).all()
    course_plans = db.query(models.CoursePlan).all()
    success = solver.generate_and_save_schedule(db, teachers, groups, course_plans, target_group_id=group_id)
    if not success: raise HTTPException(status_code=400, detail="Ошибка: Невозможно уместить предметы в сетку.")
    return {"message": "Успешно!"}


@app.get("/schedule/", response_model=List[schemas.ScheduleEntryOut])
def get_schedule(group_id: Optional[int] = None, week_number: int = 1, db: Session = Depends(database.get_db)):
    query = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.week_number == week_number)
    if group_id: query = query.filter(models.ScheduleEntry.group_id == group_id)
    return query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.time_slot).all()


@app.post("/schedule/", response_model=schemas.ScheduleEntryOut)
def create_schedule_entry(entry_data: schemas.ScheduleEntryCreate, db: Session = Depends(database.get_db)):
    new_entry = models.ScheduleEntry(**entry_data.dict())
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return new_entry


@app.put("/schedule/{entry_id}", response_model=schemas.ScheduleEntryOut)
def update_schedule_entry(entry_id: int, update_data: schemas.ScheduleEntryUpdate,
                          db: Session = Depends(database.get_db)):
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
