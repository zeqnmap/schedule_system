from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List


from . import models, schemas, database, solver

# Создаем таблицы в БД (если их еще нет)
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Schedule System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Разрешаем запросы откуда угодно (для бета-теста)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- ЭНДПОИНТЫ ДЛЯ ПРЕПОДАВАТЕЛЕЙ -----------------

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


# ----------------- ЭНДПОИНТЫ ДЛЯ ГРУПП -----------------

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


# ----------------- ЭНДПОИНТЫ ДЛЯ УЧЕБНОГО ПЛАНА -----------------

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


# ----------------- ГЕНЕРАЦИЯ И РУЧНАЯ ПРАВКА РАСПИСАНИЯ -----------------

@app.post("/generate_schedule/")
def trigger_generation(db: Session = Depends(database.get_db)):
    """
    Запускает математический алгоритм на данных из БД.
    В реальном проекте параметры (недели, часы) можно передавать в теле запроса.
    """
    teachers = db.query(models.Teacher).filter(
        models.Teacher.is_active == True,
        models.Teacher.on_vacation == False,
        models.Teacher.is_sick == False
    ).all()

    groups = db.query(models.Group).all()
    course_plans = db.query(models.CoursePlan).all()

    if not teachers or not groups or not course_plans:
        raise HTTPException(status_code=400, detail="Недостаточно данных (нет активных учителей, групп или планов).")

    # Вызываем функцию автогенерации (которую мы допишем ниже)
    success = solver.generate_and_save_schedule(db, teachers, groups, course_plans)

    if not success:
        raise HTTPException(status_code=400,
                            detail="Не удалось составить расписание без конфликтов. Измените параметры.")

    return {"message": "Расписание успешно сгенерировано на полугодие!"}



@app.put("/schedule/{entry_id}", response_model=schemas.ScheduleEntryOut)
def update_schedule_entry(
        entry_id: int,
        update_data: schemas.ScheduleEntryUpdate,
        db: Session = Depends(database.get_db)
):
    archived_check = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.group_id == update_data.group_id,
        models.ArchivedWeek.week_number == update_data.week_number,
        models.ArchivedWeek.is_archived == True
    ).first()

    if archived_check:
        raise HTTPException(
            status_code=403,
            detail={
                "error_type": "archived_week",
                "message": "Ошибка: Неделя заархивирована. Изменения запрещены.",
                "field": "general"
            }
        )

    entry = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись расписания не найдена")

    # Собираем всех преподавателей этой пары (основной и второй, если указан)
    teachers_to_check = [update_data.teacher_id]
    if update_data.teacher2_id:
        teachers_to_check.append(update_data.teacher2_id)

    # 2. ПРОВЕРКА КОНФЛИКТОВ (учитываем неделю, день и слот)

    # Проверка А: Занят ли хоть один из преподавателей в это время на этой неделе?
    teacher_conflict = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == update_data.week_number,
        models.ScheduleEntry.day_of_week == update_data.day_of_week,
        models.ScheduleEntry.time_slot == update_data.time_slot,
        models.ScheduleEntry.id != entry_id,
        or_(
            models.ScheduleEntry.teacher_id.in_(teachers_to_check),
            models.ScheduleEntry.teacher2_id.in_(teachers_to_check)
        )
    ).first()

    if teacher_conflict:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "teacher_conflict",
                "message": f"Ошибка: Один из преподавателей уже ведет пару в это время у группы {teacher_conflict.group.number}.",
                "field": "teacher_id"
            }
        )

    # Проверка Б: Занята ли группа в это время на этой неделе?
    group_conflict = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == update_data.week_number,
        models.ScheduleEntry.day_of_week == update_data.day_of_week,
        models.ScheduleEntry.time_slot == update_data.time_slot,
        models.ScheduleEntry.group_id == update_data.group_id,
        models.ScheduleEntry.id != entry_id
    ).first()

    if group_conflict:
        group = db.query(models.Group).filter(models.Group.id == update_data.group_id).first()
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "group_conflict",
                "message": f"Ошибка: У группы {group.number} в это время уже стоит пара.",
                "field": "group_id"
            }
        )

    # Проверка В: Занят ли кабинет в это время на этой неделе?
    room_conflict = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == update_data.week_number,
        models.ScheduleEntry.day_of_week == update_data.day_of_week,
        models.ScheduleEntry.time_slot == update_data.time_slot,
        models.ScheduleEntry.room == update_data.room,
        models.ScheduleEntry.id != entry_id
    ).first()

    if room_conflict:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "room_conflict",
                "message": f"Ошибка: Кабинет {update_data.room} уже занят в это время.",
                "field": "room"
            }
        )

    # 3. Если конфликтов нет — применяем изменения
    entry.week_number = update_data.week_number
    entry.day_of_week = update_data.day_of_week
    entry.time_slot = update_data.time_slot
    entry.room = update_data.room
    entry.teacher_id = update_data.teacher_id
    entry.teacher2_id = update_data.teacher2_id
    entry.group_id = update_data.group_id

    db.commit()
    db.refresh(entry)

    return entry


@app.get("/schedule/", response_model=List[schemas.ScheduleEntryOut])
def get_schedule(group_id: int = None, week_number: int = 1, db: Session = Depends(database.get_db)):
    query = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.week_number == week_number)
    if group_id:
        query = query.filter(models.ScheduleEntry.group_id == group_id)
    return query.all()


@app.put("/groups/{group_id}", response_model=schemas.GroupOut)
def update_group(group_id: int, group_data: schemas.GroupCreate, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    group.number = group_data.number
    db.commit()
    db.refresh(group)
    return group

@app.put("/teachers/{teacher_id}", response_model=schemas.TeacherOut)
def update_teacher(teacher_id: int, teacher_data: schemas.TeacherCreate, db: Session = Depends(database.get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")
    teacher.name = teacher_data.name
    teacher.default_room = teacher_data.default_room
    teacher.max_hours_per_week = teacher_data.max_hours_per_week
    db.commit()
    db.refresh(teacher)
    return teacher

@app.put("/course_plans/{plan_id}", response_model=schemas.CoursePlanOut)
def update_course_plan(plan_id: int, plan_data: schemas.CoursePlanCreate, db: Session = Depends(database.get_db)):
    plan = db.query(models.CoursePlan).filter(models.CoursePlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Учебный план не найден")
    plan.subject_name = plan_data.subject_name
    plan.total_hours = plan_data.total_hours
    plan.group_id = plan_data.group_id
    plan.teacher_id = plan_data.teacher_id
    plan.teacher2_id = plan_data.teacher2_id
    db.commit()
    db.refresh(plan)
    return plan


@app.get("/archived-weeks/status", response_model=schemas.ArchivedWeekOut)
def get_archive_status(group_id: int, week_number: int, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.group_id == group_id,
        models.ArchivedWeek.week_number == week_number
    ).first()
    is_archived = record.is_archived if record else False
    return {"group_id": group_id, "week_number": week_number, "is_archived": is_archived}


@app.post("/archived-weeks/toggle", response_model=schemas.ArchivedWeekOut)
def toggle_archive(data: schemas.ArchivedWeekToggle, db: Session = Depends(database.get_db)):
    record = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.group_id == data.group_id,
        models.ArchivedWeek.week_number == data.week_number
    ).first()

    if record:
        record.is_archived = not record.is_archived
    else:
        record = models.ArchivedWeek(
            group_id=data.group_id,
            week_number=data.week_number,
            is_archived=True
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


@app.post("/schedule/", response_model=schemas.ScheduleEntryOut)
def create_schedule_entry(
        entry_data: schemas.ScheduleEntryCreate,
        db: Session = Depends(database.get_db)
):
    # Проверка архива
    archived_check = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.group_id == entry_data.group_id,
        models.ArchivedWeek.week_number == entry_data.week_number,
        models.ArchivedWeek.is_archived == True
    ).first()

    if archived_check:
        raise HTTPException(
            status_code=403,
            detail={"message": "Ошибка: Неделя заархивирована. Изменения запрещены."}
        )

    # Проверка конфликтов преподавателей, группы и кабинета аналогична update,
    # либо можно разрешить гибко, но давай проверим базовые накладки:
    teachers_to_check = [entry_data.teacher_id]
    if entry_data.teacher2_id:
        teachers_to_check.append(entry_data.teacher2_id)

    teacher_conflict = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == entry_data.week_number,
        models.ScheduleEntry.day_of_week == entry_data.day_of_week,
        models.ScheduleEntry.time_slot == entry_data.time_slot,
        or_(
            models.ScheduleEntry.teacher_id.in_(teachers_to_check),
            models.ScheduleEntry.teacher2_id.in_(teachers_to_check)
        )
    ).first()

    if teacher_conflict:
        raise HTTPException(status_code=409, detail={"message": "Ошибка: Преподаватель уже занят в это время."})

    group_conflict = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == entry_data.week_number,
        models.ScheduleEntry.day_of_week == entry_data.day_of_week,
        models.ScheduleEntry.time_slot == entry_data.time_slot,
        models.ScheduleEntry.group_id == entry_data.group_id
    ).first()

    if group_conflict:
        raise HTTPException(status_code=409, detail={"message": "Ошибка: У группы в это время уже есть пара."})

    new_entry = models.ScheduleEntry(**entry_data.dict())
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return new_entry

@app.delete("/schedule/{entry_id}")
def delete_schedule_entry(
        entry_id: int,
        db: Session = Depends(database.get_db)
):
    entry = db.query(models.ScheduleEntry).filter(models.ScheduleEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    archived_check = db.query(models.ArchivedWeek).filter(
        models.ArchivedWeek.group_id == entry.group_id,
        models.ArchivedWeek.week_number == entry.week_number,
        models.ArchivedWeek.is_archived == True
    ).first()

    if archived_check:
        raise HTTPException(
            status_code=403,
            detail={"message": "Ошибка: Неделя заархивирована. Удаление запрещено."}
        )

    db.delete(entry)
    db.commit()
    return {"ok": True}


from sqlalchemy import or_


@app.get("/teachers-workload/")
def get_teachers_workload(db: Session = Depends(database.get_db)):
    teachers = db.query(models.Teacher).all()
    result = []
    for t in teachers:
        # Считаем сумму часов всех учебных планов, где преподаватель основной или второй
        plans = db.query(models.CoursePlan).filter(
            or_(
                models.CoursePlan.teacher_id == t.id,
                models.CoursePlan.teacher2_id == t.id
            )
        ).all()
        total_semester_hours = sum(p.total_hours for p in plans)
        weekly_hours = total_semester_hours / 20.0  # На 20 учебных недель полугодия

        result.append({
            "id": t.id,
            "name": t.name,
            "default_room": t.default_room,
            "max_hours_per_week": t.max_hours_per_week,
            "assigned_weekly_hours": round(weekly_hours, 1),
            "total_semester_hours": total_semester_hours
        })
    return result


@app.delete("/teachers/{teacher_id}")
def delete_teacher(teacher_id: int, db: Session = Depends(database.get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")
    db.delete(teacher)
    db.commit()
    return {"ok": True}


# --- Предметы (Справочник) ---
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
    subj = db.query(models.Subject).filter(models.Subject.id == subject_id).first()
    if not subj:
        raise HTTPException(status_code=404, detail="Предмет не найден")
    db.delete(subj)
    db.commit()
    return {"ok": True}

@app.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(database.get_db)):
    group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    db.delete(group)
    db.commit()
    return {"ok": True}

@app.delete("/course_plans/{plan_id}")
def delete_course_plan(plan_id: int, db: Session = Depends(database.get_db)):
    plan = db.query(models.CoursePlan).filter(models.CoursePlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="План не найден")
    db.delete(plan)
    db.commit()
    return {"ok": True}


from typing import Optional

@app.get("/plans-progress/")
def get_plans_progress(group_id: Optional[int] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.CoursePlan)
    if group_id:
        query = query.filter(models.CoursePlan.group_id == group_id)
    plans = query.all()

    groups_dict = {g.id: g.number for g in db.query(models.Group).all()}
    teachers_dict = {t.id: t.name for t in db.query(models.Teacher).all()}

    result = []
    for p in plans:
        # Считаем, сколько пар этого предмета реально стоит в сетке на все 20 недель
        actual_count = db.query(models.ScheduleEntry).filter(
            models.ScheduleEntry.group_id == p.group_id,
            models.ScheduleEntry.subject_name == p.subject_name
        ).count()

        diff = actual_count - p.total_hours
        percentage = round((actual_count / p.total_hours * 100), 1) if p.total_hours > 0 else 0

        result.append({
            "plan_id": p.id,
            "group_id": p.group_id,
            "group_number": groups_dict.get(p.group_id, str(p.group_id)),
            "subject_name": p.subject_name,
            "teacher_id": p.teacher_id,
            "teacher_name": teachers_dict.get(p.teacher_id, "Не назначен"),
            "teacher2_id": p.teacher2_id,
            "teacher2_name": teachers_dict.get(p.teacher2_id) if p.teacher2_id else None,
            "total_hours": p.total_hours,
            "scheduled_hours": actual_count,
            "diff": diff,
            "percentage": min(percentage, 100.0),
            "raw_percentage": percentage
        })

    return result

