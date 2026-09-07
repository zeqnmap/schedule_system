from pydantic import BaseModel
from typing import List, Optional


class RoomBase(BaseModel):
    name: str


class RoomCreate(RoomBase): pass


class RoomOut(RoomBase):
    id: int

    class Config: from_attributes = True


class GroupBase(BaseModel):
    number: int
    course: int = 1
    has_saturday: bool = False
    weekly_hours: int = 30
    semester_weeks: int = 20


class GroupCreate(GroupBase): pass


class GroupOut(GroupBase):
    id: int

    class Config: from_attributes = True


class TeacherBase(BaseModel):
    name: str
    room_id: Optional[int] = None
    max_hours_per_week: int = 30
    is_active: bool = True
    on_vacation: bool = False
    is_sick: bool = False
    working_days: str = "1,2,3,4,5"


class TeacherCreate(TeacherBase): pass


class TeacherOut(TeacherBase):
    id: int
    room_name: Optional[str] = None

    class Config: from_attributes = True


class CoursePlanBase(BaseModel):
    subject_name: str
    total_hours: int
    max_weekly_hours: int = 4
    teacher_id: int
    group_id: int
    teacher2_id: Optional[int] = None


class CoursePlanCreate(CoursePlanBase): pass


class CoursePlanOut(CoursePlanBase):
    id: int

    class Config: from_attributes = True


class ScheduleEntryBase(BaseModel):
    week_number: int
    day_of_week: int
    time_slot: int
    room_name: str
    teacher_id: int
    teacher2_id: Optional[int] = None
    group_id: int
    subject_name: str
    status: str = "planned"  # <--- ДОБАВЛЕНО


class ScheduleEntryCreate(ScheduleEntryBase): pass


class ScheduleEntryOut(ScheduleEntryBase):
    id: int

    class Config: from_attributes = True


class ScheduleEntryUpdate(BaseModel):
    week_number: int
    day_of_week: int
    time_slot: int
    room_name: str
    teacher_id: int
    teacher2_id: Optional[int] = None
    group_id: int
    subject_name: str
    status: str = "planned"

    class Config: from_attributes = True


class ArchivedWeekToggle(BaseModel):
    group_id: int
    week_number: int


class ArchivedWeekOut(BaseModel):
    group_id: int
    week_number: int
    is_archived: bool

    class Config: from_attributes = True


class SubjectBase(BaseModel): name: str


class SubjectCreate(SubjectBase): pass


class SubjectOut(SubjectBase):
    id: int

    class Config: from_attributes = True


class GenerateResponse(BaseModel):
    status: str
    message: str


class LoginRequest(BaseModel):
    login: str
    password: str


class UserCreate(BaseModel):
    login: str
    password: str
    is_admin: bool = False


class UserOut(BaseModel):
    id: int
    login: str
    is_admin: bool
    is_active: bool

    class Config: from_attributes = True
