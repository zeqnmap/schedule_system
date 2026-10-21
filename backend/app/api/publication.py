"""HTTP routes for publishing generated schedules to Telegram."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import database, models
from ..contracts import PublicationPeriod
from ..security import require_user


def create_publication_router(*, build_schedule_pdf: Callable, build_teachers_schedule_pdf: Callable,
                              publish_student_package: Callable, publish_pdf: Callable,
                              resolved_export_schedule_date: Callable, write_audit: Callable) -> APIRouter:
    router = APIRouter()

    @router.post("/telegram/broadcast-schedule")
    def broadcast_schedule_to_telegram(
        week_number: int = 1,
        day: Optional[int] = None,
        schedule_date: Optional[date] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        academic_year_id: Optional[int] = None,
        audience: str = "student",
        current: models.User = Depends(require_user),
        db: Session = Depends(database.get_db),
    ):
        if audience not in {"student", "teacher"}:
            raise HTTPException(status_code=400, detail="Выберите студентов или преподавателей")
        if not (start_date and end_date):
            schedule_date = resolved_export_schedule_date(db, week_number, day, academic_year_id, schedule_date)
        year = db.query(models.AcademicYear).filter_by(id=academic_year_id).first() if academic_year_id else db.query(models.AcademicYear).filter_by(is_active=True).first()
        week_start = None
        if year:
            first_monday = year.start_date - timedelta(days=year.start_date.isoweekday() - 1)
            week_start = first_monday + timedelta(days=(week_number - 1) * 7)
        filename = "Расписание.pdf" if audience == "student" else "Расписание преподавателей.pdf"
        if start_date and end_date:
            caption = f"Расписание на период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        elif day and schedule_date:
            caption = f"Расписание на {schedule_date.strftime('%d.%m.%Y')}"
        elif week_start:
            caption = f"Расписание на неделю: {week_start.strftime('%d.%m.%Y')} - {(week_start + timedelta(days=5)).strftime('%d.%m.%Y')}"
        else:
            caption = f"Расписание на неделю № {week_number}"
        pdf = build_schedule_pdf(db, week_number, day, schedule_date, academic_year_id, start_date, end_date) if audience == "student" else build_teachers_schedule_pdf(db, week_number, day, schedule_date, academic_year_id, start_date, end_date)
        if audience == "student":
            groups = db.query(models.Group).order_by(models.Group.number).all()
            group_pdfs = {
                group.number: build_schedule_pdf(db, week_number, day, schedule_date, academic_year_id, start_date, end_date, group.number)
                for group in groups
            }
            result = publish_student_package(
                pdf,
                group_pdfs,
                filename,
                caption,
                academic_year_id=year.id if year else academic_year_id,
                period=PublicationPeriod(start_date=start_date, end_date=end_date, week_number=week_number, day_of_week=day),
            )
            publication_id = result["publication_id"]
            write_audit(db, current.id, "publish_schedule", "POST", "/telegram/broadcast-schedule", 200, "publication", publication_id, caption)
            db.commit()
            return {**result, "publication_id": publication_id}
        result = publish_pdf(pdf, audience, filename, caption)
        write_audit(db, current.id, "publish_schedule", "POST", "/telegram/broadcast-schedule", 200, "broadcast", str(result.get("job_id", "")), caption)
        db.commit()
        return result

    return router
