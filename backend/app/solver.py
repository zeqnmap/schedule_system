"""Predictable CP-SAT schedule generator.

The generator never invents academic hours. Every planned row consumes one
hour from a course plan; a pair is two consecutive rows. Curator hours,
teacher collisions, group collisions and room collisions are hard rules.
"""

from functools import lru_cache
from typing import Callable, List
from datetime import date, timedelta

from ortools.sat.python import cp_model
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from . import models


DAY_COUNT = 6
SLOTS_PER_DAY = 10
MAX_LESSONS_PER_DAY = 9
MAX_PAIRS_PER_DAY = 4
GENERATED_SLOTS_PER_DAY = 8
MIN_LESSONS_ON_STARTED_DAY = 4
# This is deliberately below the value of one scheduled lesson, but far above
# the secondary subject preferences.  It makes the first available lesson the
# deterministic choice whenever moving it earlier does not create a conflict.
EARLY_SLOT_WEIGHT = 10_000
LESSON_MODES = {"auto", "lessons", "pairs", "pair_and_lesson"}
NON_CLASSROOM_SUBJECTS = {"преддипломная", "технологическая"}


def is_pe_subject(subject_name: str) -> bool:
    name = (subject_name or "").casefold()
    return any(token in name for token in ("физ", "спорт", "здоров"))


def is_sports_room(room_name: str) -> bool:
    name = (room_name or "").casefold()
    return any(token in name for token in ("спорт", "физкульт", "зал"))


def is_non_classroom_plan(subject_name: str) -> bool:
    name = (subject_name or "").strip().casefold()
    return name in NON_CLASSROOM_SUBJECTS or name.startswith("практикум")


def get_teacher_working_days(teacher) -> set[int]:
    raw_days = getattr(teacher, "working_days", None) or "1,2,3,4,5"
    result = set()
    for value in str(raw_days).split(","):
        try:
            day = int(value.strip())
        except (TypeError, ValueError):
            continue
        if 1 <= day <= DAY_COUNT:
            result.add(day)
    return result


def get_teacher_vacation_weeks(teacher) -> set[int]:
    result = set()
    for value in str(getattr(teacher, "vacation_weeks", "") or "").split(","):
        try:
            week = int(value.strip())
        except (TypeError, ValueError):
            continue
        if week > 0:
            result.add(week)
    return result


def calendar_date_for_slot(year, week: int, day: int) -> date:
    """Return the real calendar date for a Monday-first timetable column."""
    monday = year.start_date - timedelta(days=year.start_date.isoweekday() - 1)
    return monday + timedelta(days=(week - 1) * 7 + day)


class SubjectDemand:
    def __init__(self, plan, weekly_limit: int, mode: str, remaining: int):
        self.plan = plan
        self.id = plan.id
        self.group_id = plan.group_id
        self.weekly_limit = max(1, int(weekly_limit))
        self.mode = mode if mode in LESSON_MODES else "auto"
        self.remaining = max(0, int(remaining))

    @property
    def scheduled_total(self) -> int:
        """Lessons the group attends, not the sum of parallel subgroup workload."""
        allocations = [
            int(value) for value in (self.plan.teacher_hours, self.plan.teacher2_hours)
            if value is not None and int(value) > 0
        ]
        return max(allocations) if allocations else int(self.plan.total_hours or 0)

    @property
    def teacher_ids(self) -> List[int]:
        return list({item for item in (self.plan.teacher_id, self.plan.teacher2_id) if item})


def find_rule_for_plan(plan, group, rules, term_number=None):
    matches = [
        rule for rule in rules
        if rule.is_active and rule.is_required
        and rule.subject_name.strip().casefold() == plan.subject_name.strip().casefold()
        and (rule.term_number is None or rule.term_number == term_number)
        and ((rule.group_id is not None and rule.group_id == group.id)
             or (rule.group_id is None and rule.course == group.course))
    ]
    return next((rule for rule in matches if rule.group_id == group.id), matches[0] if matches else None)


def consumed_hours(db: Session, plan, before_week=None) -> int:
    query = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.group_id == plan.group_id,
        models.ScheduleEntry.subject_name == plan.subject_name,
        models.ScheduleEntry.status != "canceled",
    )
    if getattr(plan, "term_id", None):
        query = query.filter(models.ScheduleEntry.term_id == plan.term_id)
    if before_week is not None:
        query = query.filter(models.ScheduleEntry.week_number < before_week)
    return query.count()


def planned_lessons(plan) -> int:
    allocations = [
        int(value) for value in (getattr(plan, "teacher_hours", None), getattr(plan, "teacher2_hours", None))
        if value is not None and int(value) > 0
    ]
    return max(allocations) if allocations else int(plan.total_hours or 0)


def consumed_hours_by_plan(db: Session):
    rows = db.query(
        models.ScheduleEntry.group_id,
        models.ScheduleEntry.subject_name,
        models.ScheduleEntry.term_id,
        func.count(models.ScheduleEntry.id),
    ).filter(
        models.ScheduleEntry.status != "canceled"
    ).group_by(
        models.ScheduleEntry.group_id,
        models.ScheduleEntry.subject_name,
        models.ScheduleEntry.term_id,
    ).all()
    return {(group_id, subject, term_id): count for group_id, subject, term_id, count in rows}


def build_subject_demands(db, groups, course_plans, rules, week, terms_by_group, archived=None):
    if archived is None:
        archived = {
            item.group_id for item in db.query(models.ArchivedWeek).filter_by(
                week_number=week, is_archived=True
            ).all()
        }
    consumed = consumed_hours_by_plan(db)
    groups_by_id = {group.id: group for group in groups if group.id not in archived}
    demands = []
    for plan in course_plans:
        if is_non_classroom_plan(plan.subject_name):
            continue
        group = groups_by_id.get(plan.group_id)
        term = terms_by_group.get(plan.group_id)
        if not group or not term or plan.term_id != term.id:
            continue
        rule = find_rule_for_plan(plan, group, rules, term.term_number)
        limit = rule.weekly_hours if rule else (plan.max_weekly_hours or 4)
        used = consumed.get((plan.group_id, plan.subject_name, plan.term_id), 0)
        remaining = max(0, planned_lessons(plan) - used)
        if remaining:
            demands.append(SubjectDemand(plan, limit, rule.lesson_mode if rule else "auto", remaining))
    return demands


def mode_limits(demand, remaining):
    # The plan's weekly value is a preferred pace, not a reason to leave
    # semester hours unused. If a plan has 22 hours over a 17-week semester,
    # some weeks must contain two hours even when its nominal limit is 1.
    # The group weekly norm is solved separately and remains the main load
    # target. Only the selected lesson format stays hard.
    weekly = remaining
    if demand.mode == "lessons":
        return 0, weekly
    if demand.mode == "pairs":
        return weekly // 2, 1 if remaining % 2 else 0
    if demand.mode == "pair_and_lesson":
        return min(1, weekly // 2), min(1, weekly)
    # Auto mode may use a single hour even when the semester remainder is
    # even, e.g. the only free slot after a curator hour is lesson six.
    return weekly // 2, weekly


def validate_rules(groups, terms, course_plans, rules):
    errors = []
    for rule in rules:
        if not rule.is_active or not rule.is_required:
            continue
        if rule.lesson_mode not in LESSON_MODES:
            errors.append(f"Правило '{rule.subject_name}': неизвестный режим занятий.")
            continue
        selected = [
            group for group in groups
            if (rule.group_id is not None and rule.group_id == group.id)
            or (rule.group_id is None and rule.course == group.course)
        ]
        if not selected:
            errors.append(f"Правило '{rule.subject_name}' не применимо: группы не найдены.")
        if rule.weekly_hours < 1:
            errors.append(f"Правило '{rule.subject_name}': недельная нагрузка должна быть положительной.")
        for group in selected:
            group_terms = [term for term in terms if term.group_id == group.id and (rule.term_number is None or term.term_number == rule.term_number)]
            for term in group_terms:
                plans = [
                    plan for plan in course_plans
                    if plan.group_id == group.id and plan.term_id == term.id
                    and plan.subject_name.strip().casefold() == rule.subject_name.strip().casefold()
                ]
                if plans and len(plans) != 1:
                    errors.append(f"Гр. {group.number}, {term.name}: для правила '{rule.subject_name}' нужен ровно один учебный план.")
    return errors


def make_model(db, week, groups, teachers, rooms, demands, strict_load, terms_by_group):
    group_demands = {group.id: [item for item in demands if item.group_id == group.id] for group in groups}
    days_by_group = {}
    for group in groups:
        # Saturday is an explicit group setting. Never add it automatically
        # because the requested weekly load is large.
        days_by_group[group.id] = 6 if bool(group.has_saturday) else 5
    room_by_teacher = {
        teacher.id: next((room.name for room in rooms if room.id == teacher.room_id), None)
        for teacher in teachers
    }
    year_by_group = {group.id: db.query(models.AcademicYear).filter_by(id=terms_by_group[group.id].academic_year_id).first() for group in groups}
    curator_hours = db.query(models.CuratorHour).filter(
        models.CuratorHour.is_active.is_(True),
        or_(models.CuratorHour.group_id == 0, models.CuratorHour.group_id.is_(None)),
    ).all()
    overrides = {
        (item.group_id, item.curator_hour_id, item.schedule_date): item
        for item in db.query(models.GroupCuratorHourOverride).filter(
            models.GroupCuratorHourOverride.group_id.in_([group.id for group in groups])
        ).all()
    }
    days_off = {(item.academic_year_id, item.day_date) for item in db.query(models.AcademicDayOff).all()}
    group_breaks = {(item.group_id, item.academic_year_id, item.day_date) for item in db.query(models.GroupBreakDay).all()}
    blocked, curator_teacher_slots, curator_room_slots = set(), set(), set()
    # Common settings stay unchanged; a manual group override only changes this
    # group's card and the resources reserved by that particular card.
    for group in groups:
        for item in curator_hours:
            occurrence_date = calendar_date_for_slot(year_by_group[group.id], week, item.day_of_week - 1)
            term = terms_by_group[group.id]
            if (
                not term.start_date
                or not term.end_date
                or occurrence_date < term.start_date
                or occurrence_date > term.end_date
                or (year_by_group[group.id].id, occurrence_date) in days_off
                or (group.id, year_by_group[group.id].id, occurrence_date) in group_breaks
            ):
                continue
            override = overrides.get((group.id, item.id, occurrence_date))
            if override and override.is_hidden:
                continue
            teacher_id = override.teacher_id if override and override.teacher_id is not None else (item.teacher_id or getattr(group, "curator_teacher_id", None))
            room_name = override.room_name if override and override.room_name is not None else (item.room_name or getattr(group, "curator_room_name", None))
            for offset in range(item.duration):
                slot = item.time_slot - 1 + offset
                blocked.add((group.id, item.day_of_week - 1, slot))
                if teacher_id:
                    curator_teacher_slots.add((teacher_id, item.day_of_week - 1, slot))
                if room_name:
                    curator_room_slots.add((room_name.strip(), item.day_of_week - 1, slot))

    model = cp_model.CpModel()
    lesson, pair, single, pair_starts = {}, {}, {}, {}
    objective = []
    vacations = db.query(models.TeacherVacation).all()
    fixed_entries = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == week,
        models.ScheduleEntry.academic_year_id.in_({year.id for year in year_by_group.values()}),
        models.ScheduleEntry.status != "canceled",
    ).all()
    fixed_group_slots = {
        (entry.group_id, entry.day_of_week - 1, entry.time_slot - 1)
        for entry in fixed_entries
    }
    fixed_teacher_slots = {
        (teacher_id, entry.day_of_week - 1, entry.time_slot - 1)
        for entry in fixed_entries
        for teacher_id in (entry.teacher_id, entry.teacher2_id)
        if teacher_id
    }
    fixed_room_slots = {
        (room_name.strip(), entry.day_of_week - 1, entry.time_slot - 1)
        for entry in fixed_entries
        for room_name in (entry.room_name, entry.room2_name)
        if room_name and room_name.strip()
    }
    vacation_dates = {
        (vacation.teacher_id, vacation.academic_year_id, day)
        for vacation in vacations
        for day in (vacation.start_date + timedelta(days=offset) for offset in range((vacation.end_date - vacation.start_date).days + 1))
    }

    @lru_cache(maxsize=None)
    def date_available(group, day_index, target_week=week):
        term, year = terms_by_group[group.id], year_by_group[group.id]
        target = calendar_date_for_slot(year, target_week, day_index)
        return (
            term.start_date <= target <= term.end_date
            and (year.id, target) not in days_off
            and (group.id, year.id, target) not in group_breaks
        )

    def teacher_available(teacher_id, group, day_index):
        target = calendar_date_for_slot(year_by_group[group.id], week, day_index)
        return (teacher_id, year_by_group[group.id].id, target) not in vacation_dates

    for group in groups:
        days = days_by_group[group.id]
        for demand in group_demands[group.id]:
            max_pairs, max_singles = mode_limits(demand, demand.remaining)
            for day in range(days):
                for slot in range(SLOTS_PER_DAY):
                    lesson[group.id, day, slot, demand.id] = model.NewBoolVar(
                        f"lesson_g{group.id}_d{day}_s{slot}_p{demand.id}"
                    )
                    single[group.id, day, slot, demand.id] = model.NewBoolVar(
                        f"single_g{group.id}_d{day}_s{slot}_p{demand.id}"
                    )
                # Automatic lessons use four conventional pairs: 1-2, 3-4,
                # 5-6 and 7-8. A shifted start remains available immediately
                # after a curator hour, but no generated lesson is placed
                # after the eighth lesson.
                starts = list(range(0, GENERATED_SLOTS_PER_DAY - 1, 2))
                starts.extend(
                    slot for slot in range(1, GENERATED_SLOTS_PER_DAY - 1)
                    if (group.id, day, slot - 1) in blocked and slot not in starts
                )
                pair_starts[group.id, day] = starts
                for start in starts:
                    pair[group.id, day, start, demand.id] = model.NewBoolVar(
                        f"pair_g{group.id}_d{day}_s{start}_p{demand.id}"
                    )
            pairs = [pair[group.id, day, start, demand.id] for day in range(days) for start in pair_starts[group.id, day]]
            singles = [single[group.id, day, slot, demand.id] for day in range(days) for slot in range(SLOTS_PER_DAY)]
            model.Add(sum(pairs) <= max_pairs)
            model.Add(sum(singles) <= max_singles)

            # Prefer conventional pairs 1-2, 3-4 and 5-6. If a curator hour
            # blocks one of these starts, the shifted pair remains available.
            aligned_pairs = [
                pair[group.id, day, start, demand.id]
                for day in range(days)
                for start in pair_starts[group.id, day]
                if start % 2 == 0
            ]
            objective.append(50000 * sum(pairs) + 50000 * sum(aligned_pairs))

            # "Only lessons" means separate lessons, not several consecutive
            # lessons of the same subject on one day.  Spread the weekly load
            # across available days (e.g. 3 h/week → three different days).
            if demand.mode == "lessons":
                for day in range(days):
                    model.Add(sum(single[group.id, day, slot, demand.id] for slot in range(SLOTS_PER_DAY)) <= 1)

            total = sum(lesson[group.id, day, slot, demand.id] for day in range(days) for slot in range(SLOTS_PER_DAY))
            term = terms_by_group[group.id]
            weeks_left = max(1, int(term.start_week + term.weeks - week))
            upper = demand.remaining
            lower = 0
            model.Add(total >= lower)
            model.Add(total <= upper)
            desired = min(upper, max(lower, (demand.remaining + weeks_left - 1) // weeks_left))
            # Keep each subject near its remaining-hours pace. A missed hour
            # costs more than any slot preference, while extra hours are
            # allowed when another subject cannot use the available space.
            shortfall = model.NewIntVar(0, desired, f"shortfall_g{group.id}_p{demand.id}")
            model.AddMaxEquality(shortfall, [desired - total, 0])
            planned = max(1, demand.scheduled_total)
            pace_pressure = max(1, (demand.remaining * term.weeks + planned * weeks_left - 1) // (planned * weeks_left))
            horizon_pressure = 1 + 24 // weeks_left
            objective.append(-100000 * pace_pressure * horizon_pressure * shortfall)

            # Prefer a subject that is behind over one already close to its
            # semester total when both compete for the same teacher or room.
            urgency = (1000 * demand.remaining + planned - 1) // planned
            objective.append(100 * urgency * total)

            for day in range(days):
                for slot in range(SLOTS_PER_DAY):
                    covering_pairs = [
                        pair[group.id, day, start, demand.id]
                        for start in pair_starts[group.id, day]
                        if start == slot or start + 1 == slot
                    ]
                    model.Add(lesson[group.id, day, slot, demand.id] == sum(covering_pairs) + single[group.id, day, slot, demand.id])
                    if slot >= GENERATED_SLOTS_PER_DAY:
                        model.Add(lesson[group.id, day, slot, demand.id] == 0)
                        model.Add(single[group.id, day, slot, demand.id] == 0)
                    if (group.id, day, slot) in blocked:
                        model.Add(lesson[group.id, day, slot, demand.id] == 0)
                    if not date_available(group, day):
                        model.Add(lesson[group.id, day, slot, demand.id] == 0)
                    # After the required load and resource safety, compress
                    # the day towards lesson 1.  The old 3-point difference
                    # was too small, so equivalent solutions could begin at
                    # lesson 2 or later while lesson 1 was actually free.
                    objective.append(
                        (SLOTS_PER_DAY - slot) * EARLY_SLOT_WEIGHT
                        * lesson[group.id, day, slot, demand.id]
                    )

    for group in groups:
        days = days_by_group[group.id]
        for day in range(days):
            occupied = []
            for slot in range(SLOTS_PER_DAY):
                current = [lesson[group.id, day, slot, demand.id] for demand in group_demands[group.id]]
                fixed = int((group.id, day, slot) in fixed_group_slots or (group.id, day, slot) in blocked)
                model.Add(sum(current) + fixed <= 1)
                occupied.append(sum(current) + fixed)

            # Curator hours and fixed entries participate in the same block:
            # there can be only one transition from an empty to an occupied slot.
            starts = []
            for slot in range(SLOTS_PER_DAY):
                current = occupied[slot]
                start = model.NewBoolVar(f"start_g{group.id}_d{day}_s{slot}")
                if slot == 0:
                    model.Add(start == current)
                else:
                    previous = occupied[slot - 1]
                    model.Add(start >= current - previous)
                starts.append(start)
            model.Add(sum(starts) <= 1)

            day_pairs = [
                pair[group.id, day, start, demand.id]
                for demand in group_demands[group.id]
                for start in pair_starts[group.id, day]
            ]
            model.Add(sum(day_pairs) <= MAX_PAIRS_PER_DAY)

            # A generated day is either empty or contains at least two full
            # pairs. This prevents isolated one-lesson days while still
            # allowing a day to remain empty when resources are unavailable.
            if strict_load:
                day_active = model.NewBoolVar(f"active_g{group.id}_d{day}")
                generated_daily = sum(
                    lesson[group.id, day, slot, demand.id]
                    for slot in range(GENERATED_SLOTS_PER_DAY)
                    for demand in group_demands[group.id]
                )
                model.Add(generated_daily >= MIN_LESSONS_ON_STARTED_DAY * day_active)
                model.Add(generated_daily <= GENERATED_SLOTS_PER_DAY * day_active)

    # Build resource lists once. The old version repeatedly scanned every
    # group and plan for every teacher, room, day and slot.
    teacher_demands = {
        teacher.id: [
            (group, demand)
            for group in groups
            for demand in group_demands[group.id]
            if teacher.id in demand.teacher_ids
        ]
        for teacher in teachers
    }
    plan_rooms = {
        demand.id: {
            name.strip() for name in (
                demand.plan.room_name or room_by_teacher.get(demand.plan.teacher_id),
                demand.plan.room2_name or room_by_teacher.get(demand.plan.teacher2_id),
            ) if name and name.strip()
        }
        for demand in demands
    }
    room_demands = {
        room.name: [
            (group, demand)
            for group in groups
            for demand in group_demands[group.id]
            if room.name in plan_rooms[demand.id]
        ]
        for room in rooms
    }

    for day in range(DAY_COUNT):
        for slot in range(SLOTS_PER_DAY):
            for teacher in teachers:
                variables = [
                    lesson[group.id, day, slot, demand.id]
                    for group, demand in teacher_demands[teacher.id]
                    if day < days_by_group[group.id]
                ]
                if variables:
                    model.Add(sum(variables) <= 1)
                    if (teacher.id, day, slot) in fixed_teacher_slots:
                        for variable in variables: model.Add(variable == 0)
                    if teacher.on_vacation or teacher.is_sick or week in get_teacher_vacation_weeks(teacher):
                        for variable in variables:
                            model.Add(variable == 0)
                    for group, demand in teacher_demands[teacher.id]:
                        if day < days_by_group[group.id] and not teacher_available(teacher.id, group, day):
                            model.Add(lesson[group.id, day, slot, demand.id] == 0)
                    if day + 1 not in get_teacher_working_days(teacher):
                        for variable in variables:
                            model.Add(variable == 0)
                    for group, demand in teacher_demands[teacher.id]:
                        if day < days_by_group[group.id] and (teacher.id, day, slot) in curator_teacher_slots:
                            model.Add(lesson[group.id, day, slot, demand.id] == 0)

            for room in rooms:
                regular, sports = [], []
                for group, demand in room_demands[room.name]:
                    if day >= days_by_group[group.id]:
                        continue
                    variable = lesson[group.id, day, slot, demand.id]
                    if is_pe_subject(demand.plan.subject_name) and is_sports_room(room.name):
                        sports.append(variable)
                    else:
                        regular.append(variable)
                    if (room.name.strip(), day, slot) in curator_room_slots:
                        model.Add(variable == 0)
                if regular:
                    model.Add(sum(regular + sports) <= 1)
                elif sports:
                    model.Add(sum(sports) <= 2)
                if (room.name.strip(), day, slot) in fixed_room_slots:
                    for variable in regular + sports: model.Add(variable == 0)

    # A teacher's weekly limit is also a hard constraint. Co-teaching still
    # counts as one conducted lesson for each of the two teachers.
    for teacher in teachers:
        teacher_load = [
            lesson[group.id, day, slot, demand.id]
            for group, demand in teacher_demands[teacher.id]
            for day in range(days_by_group[group.id])
            for slot in range(SLOTS_PER_DAY)
        ]
        if teacher_load:
            model.Add(sum(teacher_load) <= int(teacher.max_hours_per_week or 30))

    for group in groups:
        available = sum(item.remaining for item in group_demands[group.id])
        reserved_slots = {
            (day, slot)
            for group_id, day, slot in fixed_group_slots | blocked
            if group_id == group.id and day < days_by_group[group.id]
        }
        requested = max(0, int(group.weekly_hours or 0) - len(reserved_slots))
        capacity = days_by_group[group.id] * GENERATED_SLOTS_PER_DAY
        open_days = [day for day in range(days_by_group[group.id]) if date_available(group, day)]
        term = terms_by_group[group.id]
        # Fill as many lessons as this week's group norm and free capacity
        # allow. The previous horizon pacing deliberately left available
        # hours for later weeks, which made subjects such as PE stay at zero
        # even when their teachers and sports rooms were free.
        target = min(available, requested, max(0, capacity - len(reserved_slots)))
        total = sum(
            lesson[group.id, day, slot, demand.id]
            for day in range(days_by_group[group.id])
            for slot in range(SLOTS_PER_DAY)
            for demand in group_demands[group.id]
        )
        model.Add(total == target if strict_load else total <= target)
        daily_loads = []
        for day in range(days_by_group[group.id]):
            daily = sum(
                lesson[group.id, day, slot, demand.id]
                for slot in range(SLOTS_PER_DAY)
                for demand in group_demands[group.id]
            )
            reserved_hours = sum((day, slot) in reserved_slots for slot in range(SLOTS_PER_DAY))
            model.Add(daily <= MAX_LESSONS_PER_DAY - reserved_hours)
            daily_loads.append(daily)
        # Spread lessons across all available days when the target allows it.
        # This is a hard minimum only for the strict pass.  If a teacher or a
        # room makes the requested weekly load impossible, the fallback must
        # still build the largest safe partial schedule instead of failing the
        # whole generation because one day cannot be covered.
        uncovered_days = [day for day in open_days if not any((group.id, day, slot) in fixed_group_slots for slot in range(SLOTS_PER_DAY))]
        if strict_load and uncovered_days and target >= len(uncovered_days):
            for day in uncovered_days:
                model.Add(daily_loads[day] >= 1)
        # First consume all available subject hours; only then prefer the
        # requested group norm. This prevents a half-used subject from being
        # reported as a shortage while free subject hours still exist.
        objective.append(100000000 * total)

    model.Maximize(sum(objective))
    return model, lesson, group_demands, room_by_teacher


def solve_global_week(db: Session, week: int, groups, teachers, rooms, course_plans, rules=None, strict_load=True, terms_by_group=None, archived=None):
    rules = rules or []
    terms_by_group = terms_by_group or {}
    demands = build_subject_demands(db, groups, course_plans, rules, week, terms_by_group, archived)
    active_ids = {item.group_id for item in demands}
    active_groups = [
        group for group in groups
        if group.id in active_ids and group.id not in (archived or set())
    ]
    if not active_groups:
        return True, f"Неделя {week}: все группы заархивированы или часы закончились."

    model, lesson, group_demands, room_by_teacher = make_model(
        db, week, active_groups, teachers, rooms,
        [item for item in demands if item.group_id in {group.id for group in active_groups}],
        strict_load, terms_by_group,
    )
    cp_solver = cp_model.CpSolver()
    # Ten lesson slots and the four-pair objective produce a larger model.
    # Give CP-SAT enough time to find a feasible weekly layout before using
    # the relaxed fallback.
    cp_solver.parameters.max_time_in_seconds = 10.0
    cp_solver.parameters.num_search_workers = 8
    status = cp_solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if strict_load:
            return solve_global_week(db, week, groups, teachers, rooms, course_plans, rules, strict_load=False, terms_by_group=terms_by_group, archived=archived)
        reason = "решатель не успел найти вариант" if status == cp_model.UNKNOWN else "ограничения несовместимы"
        return False, f"Неделя {week}: {reason}; расписание не изменено. Проверьте нагрузку, доступность преподавателей и субботы групп."

    for group in active_groups:
        year = db.query(models.AcademicYear).filter_by(id=terms_by_group[group.id].academic_year_id).first()
        day_count = 6 if bool(group.has_saturday) else 5
        for day in range(day_count):
            for slot in range(SLOTS_PER_DAY):
                for demand in group_demands[group.id]:
                    if cp_solver.Value(lesson[group.id, day, slot, demand.id]):
                        db.add(models.ScheduleEntry(
                            week_number=week,
                            day_of_week=day + 1,
                            time_slot=slot + 1,
                            room_name=demand.plan.room_name or room_by_teacher.get(demand.plan.teacher_id) or "Без кабинета",
                            room2_name=demand.plan.room2_name or room_by_teacher.get(demand.plan.teacher2_id),
                            teacher_id=demand.plan.teacher_id,
                            teacher2_id=demand.plan.teacher2_id,
                            group_id=group.id,
                            subject_name=demand.plan.subject_name,
                            status="planned",
                            is_generated=True,
                            term_id=terms_by_group[group.id].id,
                            academic_year_id=terms_by_group[group.id].academic_year_id,
                            schedule_date=calendar_date_for_slot(year, week, day),
                        ))
    db.flush()
    return True, f"Неделя {week}: создано безопасное расписание."


def trigger_global_generation(
    db: Session,
    approve_adjustments: bool = False,
    term_number: int | None = None,
    progress: Callable[[int, int], None] | None = None,
):
    teachers = db.query(models.Teacher).filter_by(is_active=True).all()
    groups = db.query(models.Group).all()
    rooms = db.query(models.Room).all()
    terms = db.query(models.GroupTerm).filter_by(is_active=True).all()
    # Backfill the first period for legacy groups created before semester support.
    active_year = db.query(models.AcademicYear).filter_by(is_active=True).first()
    if not active_year:
        active_year = models.AcademicYear(name="2026–2027", start_date=date(2026, 9, 1), end_date=date(2027, 8, 31), is_active=True)
        db.add(active_year); db.flush()
    existing_groups = {term.group_id for term in terms}
    for group in db.query(models.Group).all():
        if group.id in existing_groups:
            continue
        weeks = max(1, int(group.semester_weeks or 20))
        term = models.GroupTerm(academic_year_id=active_year.id, group_id=group.id, term_number=1, name="1 семестр", start_week=1, weeks=weeks, start_date=active_year.start_date, end_date=active_year.start_date + timedelta(days=weeks * 7 - 1), is_active=True, is_locked=False)
        db.add(term); db.flush()
        db.query(models.CoursePlan).filter(models.CoursePlan.group_id == group.id, models.CoursePlan.term_id.is_(None)).update({"term_id": term.id}, synchronize_session=False)
        db.query(models.ScheduleEntry).filter(models.ScheduleEntry.group_id == group.id, models.ScheduleEntry.term_id.is_(None)).update({"term_id": term.id}, synchronize_session=False)
        terms.append(term)
    db.flush()
    unlocked_terms = [term for term in terms if not term.is_locked and (term_number is None or term.term_number == term_number)]
    if not unlocked_terms:
        selected = f"{term_number}-й семестр" if term_number else "выбранные семестры"
        return False, f"Нет открытых семестров для генерации: {selected}. Завершённые семестры доступны только для ручного редактирования."
    course_plans = db.query(models.CoursePlan).filter(models.CoursePlan.term_id.in_([term.id for term in unlocked_terms])).all()
    rules = db.query(models.AlgorithmRule).filter_by(is_active=True).all()
    if not groups:
        return False, "Нет групп для генерации."
    if not course_plans:
        return False, "В выбранных семестрах нет назначенных дисциплин. Существующее расписание не изменено."

    errors = validate_rules(groups, unlocked_terms, course_plans, rules)
    if errors:
        return False, "ОШИБКИ В НАСТРОЙКАХ АЛГОРИТМА:\n" + "\n".join(errors)

    archived = {
        (item.group_id, item.week_number, item.academic_year_id)
        for item in db.query(models.ArchivedWeek).filter(
            models.ArchivedWeek.is_archived.is_(True),
            models.ArchivedWeek.academic_year_id.in_({term.academic_year_id for term in unlocked_terms}),
        ).all()
    }
    # A group may have one archived week and many open weeks. Delete only
    # rows belonging to open weeks; filtering by group alone would destroy the
    # protected history of that group.
    for entry in db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.term_id.in_([term.id for term in unlocked_terms]),
        models.ScheduleEntry.is_generated.is_(True),
        or_(models.ScheduleEntry.status == "planned", models.ScheduleEntry.status.is_(None))
    ).all():
        if (entry.group_id, entry.week_number, entry.academic_year_id) not in archived:
            db.delete(entry)
    db.flush()

    max_weeks = max(term.start_week + term.weeks - 1 for term in unlocked_terms)
    weeks = [
        week for week in range(1, max_weeks + 1)
        if any(term.start_week <= week < term.start_week + term.weeks for term in unlocked_terms)
    ]
    if progress:
        progress(0, len(weeks))
    for completed, week in enumerate(weeks, 1):
        terms_by_group = {term.group_id: term for term in unlocked_terms if term.start_week <= week < term.start_week + term.weeks}
        active_groups = [group for group in groups if group.id in terms_by_group]
        if active_groups:
            archived_groups = {
                group_id for group_id, archived_week, academic_year_id in archived
                if archived_week == week
                and terms_by_group.get(group_id)
                and terms_by_group[group_id].academic_year_id == academic_year_id
            }
            success, message = solve_global_week(
                db, week, active_groups, teachers, rooms, course_plans, rules,
                strict_load=True, terms_by_group=terms_by_group,
                archived=archived_groups,
            )
            if not success:
                return False, message
        if progress:
            progress(completed, len(weeks))

    shortages = []
    consumed = consumed_hours_by_plan(db)
    for plan in course_plans:
        if is_non_classroom_plan(plan.subject_name):
            continue
        missing = max(0, planned_lessons(plan) - consumed.get((plan.group_id, plan.subject_name, plan.term_id), 0))
        if missing:
            group = next((item for item in groups if item.id == plan.group_id), None)
            shortages.append(f"Гр. {group.number if group else plan.group_id}, {plan.subject_name}: не доставлено {missing} ч.")

    selected = f"{term_number}-го семестра" if term_number else "всех открытых семестров"
    result = f"Расписание создано для {selected} до {max_weeks}-й календарной недели без накладок."
    if shortages:
        result += "\n\nНЕДОСТАВЛЕННЫЕ ЧАСЫ — измените планы вручную:\n" + "\n".join(shortages)
    db.commit()
    return True, result
