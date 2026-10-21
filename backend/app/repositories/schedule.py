"""Focused schedule queries for services and routers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models


class ScheduleRepository:
    """Keep read/query details out of HTTP handlers."""

    def __init__(self, db: Session):
        self.db = db

    def academic_year(self, academic_year_id: int | None = None):
        query = self.db.query(models.AcademicYear)
        if academic_year_id is not None:
            return query.filter_by(id=academic_year_id).first()
        return query.filter_by(is_active=True).first()

    def groups(self):
        return self.db.query(models.Group).order_by(models.Group.number).all()

    def entries_for_period(self, *, academic_year_id: int | None, week_number: int,
                           start_date=None, end_date=None, day: int | None = None,
                           group_id: int | None = None):
        query = self.db.query(models.ScheduleEntry)
        if start_date and end_date:
            query = query.filter(
                models.ScheduleEntry.schedule_date >= start_date,
                models.ScheduleEntry.schedule_date <= end_date,
            )
        else:
            query = query.filter(models.ScheduleEntry.week_number == week_number)
        if academic_year_id is not None:
            query = query.filter(models.ScheduleEntry.academic_year_id == academic_year_id)
        if day is not None:
            query = query.filter(models.ScheduleEntry.day_of_week == day)
        if group_id is not None:
            query = query.filter(models.ScheduleEntry.group_id == group_id)
        return query.order_by(models.ScheduleEntry.day_of_week, models.ScheduleEntry.time_slot).all()
