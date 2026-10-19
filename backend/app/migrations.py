"""Versioned, explicit migrations for the schedule database."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import database, models

CURRENT_REVISION = "0001_schedule_schema"


def _add_column(engine: Engine, table: str, name: str, definition: str) -> bool:
    columns = {column["name"] for column in inspect(engine).get_columns(table)}
    if name in columns:
        return False
    with engine.begin() as connection:
        connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'))
    return True


def _apply_0001(engine: Engine) -> None:
    # create_all only creates absent tables. The explicit column upgrades below
    # preserve databases created by earlier application versions.
    models.Base.metadata.create_all(bind=engine)

    for table, name, definition in (
        ("teachers", "working_days", "VARCHAR DEFAULT '1,2,3,4,5' NOT NULL"),
        ("teachers", "vacation_weeks", "VARCHAR DEFAULT '' NOT NULL"),
        ("curator_hours", "room_name", "VARCHAR"),
        ("curator_hours", "teacher_id", "INTEGER"),
        ("curator_hours", "hour_type", "VARCHAR DEFAULT 'curator' NOT NULL"),
        ("algorithm_rules", "term_number", "INTEGER"),
        ("group_curator_hour_overrides", "schedule_date", "DATE"),
        ("groups", "curator_teacher_id", "INTEGER"),
        ("groups", "curator_room_name", "VARCHAR"),
        ("users", "role", "VARCHAR DEFAULT 'user' NOT NULL"),
        ("course_plans", "term_id", "INTEGER"),
        ("schedule_entries", "term_id", "INTEGER"),
        ("group_terms", "is_locked", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("group_terms", "start_date", "DATE"),
        ("group_terms", "end_date", "DATE"),
        ("course_plans", "academic_year_id", "INTEGER"),
        ("schedule_entries", "academic_year_id", "INTEGER"),
        ("archived_weeks", "academic_year_id", "INTEGER"),
        ("course_plans", "teacher_hours", "INTEGER"),
        ("course_plans", "teacher2_hours", "INTEGER"),
        ("course_plans", "room_name", "VARCHAR"),
        ("course_plans", "room2_name", "VARCHAR"),
        ("schedule_entries", "room2_name", "VARCHAR"),
        ("schedule_entries", "is_generated", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("schedule_entries", "schedule_date", "DATE"),
    ):
        added = _add_column(engine, table, name, definition)
        if table == "group_curator_hour_overrides" and name == "schedule_date" and added:
            # Legacy overrides applied to every week. Restore the common hour
            # cards rather than assigning those overrides an arbitrary date.
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM group_curator_hour_overrides"))

    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE users SET role = CASE WHEN is_admin = 1 THEN 'owner' ELSE 'user' END "
            "WHERE role IS NULL OR role NOT IN ('owner', 'admin', 'user')"
        ))
        connection.execute(text(
            "UPDATE users SET is_admin = CASE WHEN role = 'owner' THEN 1 ELSE 0 END"
        ))
        connection.execute(text(
            "UPDATE group_terms SET start_date = COALESCE(start_date, '2026-09-01'), "
            "end_date = COALESCE(end_date, date('2026-09-01', '+' || "
            "(start_week + weeks - 2) || ' days'))"
        ))
        connection.execute(text(
            "UPDATE course_plans SET term_id = (SELECT id FROM group_terms "
            "WHERE group_terms.group_id = course_plans.group_id "
            "AND group_terms.term_number = 1 LIMIT 1) WHERE term_id IS NULL"
        ))
        connection.execute(text(
            "UPDATE course_plans SET academic_year_id = (SELECT academic_year_id "
            "FROM group_terms WHERE group_terms.id = course_plans.term_id) "
            "WHERE academic_year_id IS NULL"
        ))
        connection.execute(text(
            "UPDATE schedule_entries SET term_id = (SELECT id FROM group_terms "
            "WHERE group_terms.group_id = schedule_entries.group_id "
            "AND schedule_entries.week_number >= group_terms.start_week "
            "AND schedule_entries.week_number < group_terms.start_week + group_terms.weeks "
            "ORDER BY group_terms.term_number LIMIT 1) WHERE term_id IS NULL"
        ))
        connection.execute(text(
            "UPDATE schedule_entries SET academic_year_id = (SELECT academic_year_id "
            "FROM group_terms WHERE group_terms.id = schedule_entries.term_id) "
            "WHERE academic_year_id IS NULL"
        ))
        connection.execute(text("UPDATE curator_hours SET group_id = NULL WHERE group_id = 0"))
        connection.execute(text(
            "UPDATE archived_weeks SET academic_year_id = (SELECT id FROM academic_years "
            "WHERE is_active = 1 LIMIT 1) WHERE academic_year_id IS NULL"
        ))

    # Preserve the old calendar conversion exactly, but perform it only once
    # as part of the recorded migration rather than on every server start.
    with Session(engine) as session:
        years_by_id = {year.id: year for year in session.query(models.AcademicYear).all()}
        legacy_entries = session.query(models.ScheduleEntry).filter(
            models.ScheduleEntry.schedule_date.is_(None)
        ).all()
        for entry in legacy_entries:
            year = years_by_id.get(entry.academic_year_id)
            if year is None:
                continue
            monday = year.start_date - timedelta(days=year.start_date.isoweekday() - 1)
            entry.schedule_date = monday + timedelta(
                days=(entry.week_number - 1) * 7 + entry.day_of_week - 1
            )
        session.commit()


def current_revision(engine: Engine = database.engine) -> str | None:
    inspector = inspect(engine)
    if "schema_migrations" not in inspector.get_table_names():
        return None
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        )).scalar_one_or_none()


def upgrade(engine: Engine = database.engine) -> bool:
    """Apply pending migrations and return whether the database changed."""
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version VARCHAR PRIMARY KEY, applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        applied = connection.execute(text(
            "SELECT 1 FROM schema_migrations WHERE version = :version"
        ), {"version": CURRENT_REVISION}).first()
    if applied:
        return False

    _apply_0001(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schema_migrations(version) VALUES (:version)"
        ), {"version": CURRENT_REVISION})
    return True


def require_current(engine: Engine = database.engine) -> None:
    revision = current_revision(engine)
    if revision != CURRENT_REVISION:
        found = revision or "none"
        raise RuntimeError(
            f"Database schema revision is {found}; expected {CURRENT_REVISION}. "
            "Run `python -m backend.app.migrate` before starting the API."
        )
