from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    default_room = Column(String)
    max_hours_per_week = Column(Integer, default=30)

    is_active = Column(Boolean, default=True)
    on_vacation = Column(Boolean, default=False)
    is_sick = Column(Boolean, default=False)

    course_plans_primary = relationship("CoursePlan", foreign_keys="[CoursePlan.teacher_id]", back_populates="teacher")
    course_plans_secondary = relationship("CoursePlan", foreign_keys="[CoursePlan.teacher2_id]",
                                          back_populates="teacher2")
    schedule_entries_primary = relationship("ScheduleEntry", foreign_keys="[ScheduleEntry.teacher_id]",
                                            back_populates="teacher")
    schedule_entries_secondary = relationship("ScheduleEntry", foreign_keys="[ScheduleEntry.teacher2_id]",
                                              back_populates="teacher2")


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, unique=True, index=True)
    course = Column(Integer, default=1)  # Курс группы (1, 2, 3 или 4)
    has_saturday = Column(Boolean, default=False)
    weekly_hours = Column(Integer, default=30)

    course_plans = relationship("CoursePlan", back_populates="group")
    schedule_entries = relationship("ScheduleEntry", back_populates="group")


class CoursePlan(Base):
    __tablename__ = "course_plans"

    id = Column(Integer, primary_key=True, index=True)
    subject_name = Column(String)
    total_hours = Column(Integer)

    # ВОТ ЭТО ПОЛЕ БЫЛО ПРОПУЩЕНО:
    max_weekly_hours = Column(Integer, default=4)  # Лимит часов в неделю

    group_id = Column(Integer, ForeignKey("groups.id"))
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    teacher2_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)

    group = relationship("Group", back_populates="course_plans")
    teacher = relationship("Teacher", foreign_keys=[teacher_id])
    teacher2 = relationship("Teacher", foreign_keys=[teacher2_id])


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id = Column(Integer, primary_key=True, index=True)
    week_number = Column(Integer)
    day_of_week = Column(Integer)
    time_slot = Column(Integer)
    room = Column(String)

    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    teacher2_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    subject_name = Column(String)

    teacher = relationship("Teacher", foreign_keys=[teacher_id], back_populates="schedule_entries_primary")
    teacher2 = relationship("Teacher", foreign_keys=[teacher2_id], back_populates="schedule_entries_secondary")
    group = relationship("Group", back_populates="schedule_entries")


class ArchivedWeek(Base):
    __tablename__ = "archived_weeks"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    week_number = Column(Integer)
    is_archived = Column(Boolean, default=False)

    group = relationship("Group")


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
