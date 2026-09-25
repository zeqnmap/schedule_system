from sqlalchemy import Column, Integer, String, Boolean, Date, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)

class Teacher(Base):
    __tablename__ = "teachers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    max_hours_per_week = Column(Integer, default=30)
    is_active = Column(Boolean, default=True)
    on_vacation = Column(Boolean, default=False)
    is_sick = Column(Boolean, default=False)
    working_days = Column(String, default="1,2,3,4,5", nullable=False)
    vacation_weeks = Column(String, default="", nullable=False)

    room = relationship("Room")
    course_plans_primary = relationship("CoursePlan", foreign_keys="[CoursePlan.teacher_id]", back_populates="teacher")
    course_plans_secondary = relationship("CoursePlan", foreign_keys="[CoursePlan.teacher2_id]", back_populates="teacher2")

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, unique=True, index=True)
    course = Column(Integer, default=1)
    has_saturday = Column(Boolean, default=False)
    weekly_hours = Column(Integer, default=30)
    semester_weeks = Column(Integer, default=20)
    curator_teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    curator_room_name = Column(String, nullable=True)

    course_plans = relationship("CoursePlan", back_populates="group")
    schedule_entries = relationship("ScheduleEntry", back_populates="group")
    curator_teacher = relationship("Teacher", foreign_keys=[curator_teacher_id])
    terms = relationship("GroupTerm", back_populates="group", cascade="all, delete-orphan")

class CoursePlan(Base):
    __tablename__ = "course_plans"
    id = Column(Integer, primary_key=True, index=True)
    subject_name = Column(String)
    total_hours = Column(Integer)
    max_weekly_hours = Column(Integer, default=4)
    group_id = Column(Integer, ForeignKey("groups.id"))
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    teacher2_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    term_id = Column(Integer, ForeignKey("group_terms.id"), nullable=True, index=True)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=True, index=True)

    group = relationship("Group", back_populates="course_plans")
    teacher = relationship("Teacher", foreign_keys=[teacher_id])
    teacher2 = relationship("Teacher", foreign_keys=[teacher2_id])
    term = relationship("GroupTerm", back_populates="course_plans")

class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"
    id = Column(Integer, primary_key=True, index=True)
    week_number = Column(Integer)
    day_of_week = Column(Integer)
    time_slot = Column(Integer)
    room_name = Column(String)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    teacher2_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    subject_name = Column(String)
    status = Column(String, default="planned") # <--- НОВАЯ ЛОГИКА (planned / canceled)
    term_id = Column(Integer, ForeignKey("group_terms.id"), nullable=True, index=True)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=True, index=True)
    schedule_date = Column(Date, nullable=True, index=True)

    group = relationship("Group", back_populates="schedule_entries")
    term = relationship("GroupTerm", back_populates="schedule_entries")


class AcademicYear(Base):
    __tablename__ = "academic_years"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    terms = relationship("GroupTerm", back_populates="academic_year")


class AcademicDayOff(Base):
    __tablename__ = "academic_days_off"
    id = Column(Integer, primary_key=True, index=True)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=False, index=True)
    day_date = Column(Date, nullable=False, index=True)
    title = Column(String, nullable=True)


class GroupBreakDay(Base):
    """A calendar break for one group (practice, field activity, etc.)."""
    __tablename__ = "group_break_days"
    id = Column(Integer, primary_key=True, index=True)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False, index=True)
    day_date = Column(Date, nullable=False, index=True)
    title = Column(String, nullable=True)


class TeacherVacation(Base):
    __tablename__ = "teacher_vacations"
    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=False, index=True)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)


class GroupTerm(Base):
    __tablename__ = "group_terms"
    id = Column(Integer, primary_key=True, index=True)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=False, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False, index=True)
    term_number = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    start_week = Column(Integer, nullable=False)
    weeks = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_locked = Column(Boolean, default=False, nullable=False)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    academic_year = relationship("AcademicYear", back_populates="terms")
    group = relationship("Group", back_populates="terms")
    course_plans = relationship("CoursePlan", back_populates="term")
    schedule_entries = relationship("ScheduleEntry", back_populates="term")

class ArchivedWeek(Base):
    __tablename__ = "archived_weeks"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    week_number = Column(Integer)
    is_archived = Column(Boolean, default=False)
    academic_year_id = Column(Integer, ForeignKey("academic_years.id"), nullable=True, index=True)

class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)


class AlgorithmRule(Base):
    __tablename__ = "algorithm_rules"
    id = Column(Integer, primary_key=True, index=True)
    subject_name = Column(String, nullable=False, index=True)
    course = Column(Integer, nullable=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True, index=True)
    weekly_hours = Column(Integer, nullable=False)
    lesson_mode = Column(String, default="auto", nullable=False)
    is_required = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    group = relationship("Group")


class CuratorHour(Base):
    __tablename__ = "curator_hours"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True, index=True)
    day_of_week = Column(Integer, nullable=False)
    time_slot = Column(Integer, nullable=False)
    duration = Column(Integer, default=1, nullable=False)
    room_name = Column(String, nullable=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    hour_type = Column(String, default="curator", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    group = relationship("Group")
    teacher = relationship("Teacher")


class GroupCuratorHourOverride(Base):
    """A group-specific override for one occurrence of a common curator hour."""
    __tablename__ = "group_curator_hour_overrides"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False, index=True)
    curator_hour_id = Column(Integer, ForeignKey("curator_hours.id"), nullable=False, index=True)
    schedule_date = Column(Date, nullable=False, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    room_name = Column(String, nullable=True)
    is_hidden = Column(Boolean, default=False, nullable=False)

    group = relationship("Group")
    curator_hour = relationship("CuratorHour")
    teacher = relationship("Teacher")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
