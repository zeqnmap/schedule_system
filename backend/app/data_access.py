"""Shared database queries used by API routes and schedule rendering."""

from datetime import date
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import models


def common_curator_hour_filter():
    """Support legacy zero and the nullable representation of a common hour."""
    return or_(models.CuratorHour.group_id == 0, models.CuratorHour.group_id.is_(None))


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


def group_has_classes_on_date(db: Session, group_id: int, academic_year_id: Optional[int], target_date: Optional[date]) -> bool:
    """Whether an implicit common hour may exist for this group on this date."""
    if target_date is None:
        return True
    if academic_year_id is None:
        year = db.query(models.AcademicYear).filter_by(is_active=True).first()
        academic_year_id = year.id if year else None
    if academic_year_id is None:
        return False
    in_term = db.query(models.GroupTerm.id).filter(
        models.GroupTerm.group_id == group_id,
        models.GroupTerm.academic_year_id == academic_year_id,
        models.GroupTerm.is_active.is_(True),
        models.GroupTerm.start_date <= target_date,
        models.GroupTerm.end_date >= target_date,
    ).first()
    if not in_term:
        return False
    if db.query(models.AcademicDayOff.id).filter_by(
        academic_year_id=academic_year_id, day_date=target_date
    ).first():
        return False
    return not db.query(models.GroupBreakDay.id).filter_by(
        academic_year_id=academic_year_id, group_id=group_id, day_date=target_date
    ).first()
