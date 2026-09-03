from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base


class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    default_room = Column(String)  # Закрепленный кабинет по умолчанию
    max_hours_per_week = Column(Integer, default=30)

    is_active = Column(Boolean, default=True)
    on_vacation = Column(Boolean, default=False)
    is_sick = Column(Boolean, default=False)

    # Обратные связи для SQLAlchemy (разделяем основного и второго преподавателя)
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
    number = Column(Integer, unique=True, index=True)  # Трехзначный номер, напр. 312

    # Связи
    course_plans = relationship("CoursePlan", back_populates="group")
    schedule_entries = relationship("ScheduleEntry", back_populates="group")


class CoursePlan(Base):
    """
    Учебный план: сколько пар какого предмета должен отвести конкретный преподаватель
    (или два преподавателя) у конкретной группы за полугодие.
    """
    __tablename__ = "course_plans"

    id = Column(Integer, primary_key=True, index=True)
    subject_name = Column(String)
    total_hours = Column(Integer)  # Общее количество пар на полугодие

    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    teacher2_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)  # Второй преподаватель (необязательно)
    group_id = Column(Integer, ForeignKey("groups.id"))

    teacher = relationship("Teacher", foreign_keys=[teacher_id], back_populates="course_plans_primary")
    teacher2 = relationship("Teacher", foreign_keys=[teacher2_id], back_populates="course_plans_secondary")
    group = relationship("Group", back_populates="course_plans")


class ScheduleEntry(Base):
    """
    Конкретная запись в расписании (одна пара) на конкретную неделю.
    Здесь сохраняются сгенерированные пары и ручные правки.
    """
    __tablename__ = "schedule_entries"

    id = Column(Integer, primary_key=True, index=True)
    week_number = Column(Integer)  # Номер недели (от 1 до 20)
    day_of_week = Column(Integer)  # 1 - Понедельник, ..., 5 - Пятница
    time_slot = Column(Integer)  # Номер пары: 1, 2, 3, 4 и т.д.
    room = Column(String)  # Кабинет по факту

    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    teacher2_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)  # Второй преподаватель (если есть)
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
