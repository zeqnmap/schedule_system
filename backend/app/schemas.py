from pydantic import BaseModel
from typing import List, Optional

class GroupBase(BaseModel):
    number: int
    course: int = 1
    has_saturday: bool = False
    weekly_hours: int = 30

class GroupCreate(GroupBase):
    pass

class GroupOut(GroupBase):
    id: int
    class Config:
        from_attributes = True

class TeacherBase(BaseModel):
    name: str
    default_room: str
    max_hours_per_week: int = 30
    is_active: bool = True
    on_vacation: bool = False
    is_sick: bool = False

class TeacherCreate(TeacherBase):
    pass

class TeacherOut(TeacherBase):
    id: int
    class Config:
        from_attributes = True

class CoursePlanBase(BaseModel):
    subject_name: str
    total_hours: int
    max_weekly_hours: int = 4  # <-- ВЕРНУЛИ ЛИМИТ ЧАСОВ
    teacher_id: int
    group_id: int
    teacher2_id: Optional[int] = None

class CoursePlanCreate(CoursePlanBase):
    pass

class CoursePlanOut(CoursePlanBase):
    id: int
    max_weekly_hours: int = 4  # <-- ВЕРНУЛИ ЛИМИТ ЧАСОВ
    class Config:
        from_attributes = True

class ScheduleEntryBase(BaseModel):
    week_number: int
    day_of_week: int
    time_slot: int
    room: str
    teacher_id: int
    teacher2_id: Optional[int] = None
    group_id: int
    subject_name: str

class ScheduleEntryCreate(ScheduleEntryBase):
    pass

class ScheduleEntryOut(ScheduleEntryBase):
    id: int
    class Config:
        from_attributes = True

class ScheduleEntryUpdate(BaseModel):
    week_number: int
    day_of_week: int
    time_slot: int
    room: str
    teacher_id: int
    teacher2_id: Optional[int] = None
    group_id: int
    subject_name: str
    class Config:
        from_attributes = True

class ArchivedWeekToggle(BaseModel):
    group_id: int
    week_number: int

class ArchivedWeekOut(BaseModel):
    group_id: int
    week_number: int
    is_archived: bool
    class Config:
        from_attributes = True

class SubjectBase(BaseModel):
    name: str

class SubjectCreate(SubjectBase):
    pass

class SubjectOut(SubjectBase):
    id: int
    class Config:
        from_attributes = True
