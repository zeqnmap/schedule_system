"""Predictable CP-SAT schedule generator.

The generator never invents academic hours. Every planned row consumes one
hour from a course plan; a pair is two consecutive rows. Curator hours,
teacher collisions, group collisions and room collisions are hard rules.
"""

from typing import List
from datetime import date, timedelta

from ortools.sat.python import cp_model
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import models


DAY_COUNT = 6
SLOTS_PER_DAY = 12
LESSON_MODES = {"auto", "lessons", "pairs", "pair_and_lesson"}


def is_pe_subject(subject_name: str) -> bool:
    name = (subject_name or "").casefold()
    return any(token in name for token in ("физ", "спорт", "здоров"))


def is_sports_room(room_name: str) -> bool:
    name = (room_name or "").casefold()
    return any(token in name for token in ("спорт", "физкульт", "зал"))


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
    def teacher_ids(self) -> List[int]:
        return list({item for item in (self.plan.teacher_id, self.plan.teacher2_id) if item})


def find_rule_for_plan(plan, group, rules):
    matches = [
        rule for rule in rules
        if rule.is_active and rule.is_required
        and rule.subject_name.strip().casefold() == plan.subject_name.strip().casefold()
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


def build_subject_demands(db, groups, course_plans, rules, week, terms_by_group):
    archived = {
        item.group_id for item in db.query(models.ArchivedWeek).filter_by(
            week_number=week, is_archived=True
        ).all()
    }
    groups_by_id = {group.id: group for group in groups if group.id not in archived}
    demands = []
    for plan in course_plans:
        group = groups_by_id.get(plan.group_id)
        term = terms_by_group.get(plan.group_id)
        if not group or not term or plan.term_id != term.id:
            continue
        rule = find_rule_for_plan(plan, group, rules)
        limit = rule.weekly_hours if rule else (plan.max_weekly_hours or 4)
        remaining = max(0, int(plan.total_hours or 0) - consumed_hours(db, plan, before_week=week))
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
    return weekly // 2, weekly % 2


def validate_rules(groups, course_plans, rules):
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
            plans = [
                plan for plan in course_plans
                if plan.group_id == group.id
                and plan.subject_name.strip().casefold() == rule.subject_name.strip().casefold()
            ]
            if plans and len(plans) != 1:
                errors.append(f"Гр. {group.number}: для правила '{rule.subject_name}' нужен ровно один учебный план.")
    return errors


def make_model(db, week, groups, teachers, rooms, demands, strict_load, terms_by_group):
    group_demands = {group.id: [item for item in demands if item.group_id == group.id] for group in groups}
    room_by_teacher = {
        teacher.id: next((room.name for room in rooms if room.id == teacher.room_id), None)
        for teacher in teachers
    }
    year_by_group = {group.id: db.query(models.AcademicYear).filter_by(id=terms_by_group[group.id].academic_year_id).first() for group in groups}
    curator_hours = db.query(models.CuratorHour).filter(models.CuratorHour.is_active.is_(True), models.CuratorHour.group_id.in_([0, None])).all()
    overrides = {
        (item.group_id, item.curator_hour_id, item.schedule_date): item
        for item in db.query(models.GroupCuratorHourOverride).filter(
            models.GroupCuratorHourOverride.group_id.in_([group.id for group in groups])
        ).all()
    }
    blocked, curator_teacher_slots, curator_room_slots = set(), set(), set()
    # Common settings stay unchanged; a manual group override only changes this
    # group's card and the resources reserved by that particular card.
    for group in groups:
        for item in curator_hours:
            occurrence_date = calendar_date_for_slot(year_by_group[group.id], week, item.day_of_week - 1)
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
    days_off = {(item.academic_year_id, item.day_date) for item in db.query(models.AcademicDayOff).all()}
    group_breaks = {(item.group_id, item.academic_year_id, item.day_date) for item in db.query(models.GroupBreakDay).all()}
    vacations = db.query(models.TeacherVacation).all()

    def date_available(group, day_index):
        term, year = terms_by_group[group.id], year_by_group[group.id]
        target = calendar_date_for_slot(year, week, day_index)
        return (
            term.start_date <= target <= term.end_date
            and (year.id, target) not in days_off
            and (group.id, year.id, target) not in group_breaks
        )

    def teacher_available(teacher_id, group, day_index):
        target = calendar_date_for_slot(year_by_group[group.id], week, day_index)
        return not any(v.teacher_id == teacher_id and v.academic_year_id == year_by_group[group.id].id and v.start_date <= target <= v.end_date for v in vacations)

    for group in groups:
        days = 6 if group.has_saturday else 5
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
                # A regular pair starts on the usual even slot. If a curator
                # hour splits a day, the first free slot after it is also a
                # valid pair start, otherwise two lessons after that hour can
                # never be scheduled for pair-only subjects.
                starts = list(range(0, SLOTS_PER_DAY - 1, 2))
                starts.extend(
                    slot for slot in range(1, SLOTS_PER_DAY - 1)
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

            total = sum(lesson[group.id, day, slot, demand.id] for day in range(days) for slot in range(SLOTS_PER_DAY))
            term = terms_by_group[group.id]
            weeks_left = max(1, int(term.start_week + term.weeks - week))
            upper = demand.remaining
            lower = 0
            model.Add(total >= lower)
            model.Add(total <= upper)
            desired = min(upper, max(lower, (demand.remaining + weeks_left - 1) // weeks_left))
            deviation = model.NewIntVar(0, SLOTS_PER_DAY * days, f"deviation_g{group.id}_p{demand.id}")
            model.AddAbsEquality(deviation, total - desired)
            objective.append(-300 * deviation)

            for day in range(days):
                for slot in range(SLOTS_PER_DAY):
                    covering_pairs = [
                        pair[group.id, day, start, demand.id]
                        for start in pair_starts[group.id, day]
                        if start == slot or start + 1 == slot
                    ]
                    model.Add(lesson[group.id, day, slot, demand.id] == sum(covering_pairs) + single[group.id, day, slot, demand.id])
                    if (group.id, day, slot) in blocked:
                        model.Add(lesson[group.id, day, slot, demand.id] == 0)
                    if not date_available(group, day):
                        model.Add(lesson[group.id, day, slot, demand.id] == 0)
                    objective.append((1000 - slot * 30) * lesson[group.id, day, slot, demand.id])

    for group in groups:
        days = 6 if group.has_saturday else 5
        for day in range(days):
            for slot in range(SLOTS_PER_DAY):
                current = [lesson[group.id, day, slot, demand.id] for demand in group_demands[group.id]]
                model.Add(sum(current) <= 1)

                # Students never have an ordinary empty slot between lessons.
                # A curator hour is the only allowed break: it starts a new
                # compact segment, so classes may continue immediately after it.
                if slot + 1 < SLOTS_PER_DAY and (group.id, day, slot) not in blocked and (group.id, day, slot + 1) not in blocked:
                    following = [lesson[group.id, day, slot + 1, demand.id] for demand in group_demands[group.id]]
                    model.Add(sum(following) <= sum(current))

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
    room_demands = {
        room.name: [
            (group, demand)
            for group in groups
            for demand in group_demands[group.id]
            if room_by_teacher.get(demand.plan.teacher_id) == room.name
        ]
        for room in rooms
    }

    fixed_entries = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == week, models.ScheduleEntry.status != "canceled"
    ).all()
    for day in range(DAY_COUNT):
        for slot in range(SLOTS_PER_DAY):
            for teacher in teachers:
                variables = [
                    lesson[group.id, day, slot, demand.id]
                    for group, demand in teacher_demands[teacher.id]
                    if day < (6 if group.has_saturday else 5)
                ]
                if variables:
                    model.Add(sum(variables) <= 1)
                    if any(teacher.id in {entry.teacher_id, entry.teacher2_id} for entry in fixed_entries if entry.day_of_week == day + 1 and entry.time_slot == slot + 1):
                        for variable in variables: model.Add(variable == 0)
                    if teacher.on_vacation or teacher.is_sick or week in get_teacher_vacation_weeks(teacher):
                        for variable in variables:
                            model.Add(variable == 0)
                    for group, demand in teacher_demands[teacher.id]:
                        if day < (6 if group.has_saturday else 5) and not teacher_available(teacher.id, group, day):
                            model.Add(lesson[group.id, day, slot, demand.id] == 0)
                    if day + 1 not in get_teacher_working_days(teacher):
                        for variable in variables:
                            model.Add(variable == 0)
                    for group, demand in teacher_demands[teacher.id]:
                        if day < (6 if group.has_saturday else 5) and (teacher.id, day, slot) in curator_teacher_slots:
                            model.Add(lesson[group.id, day, slot, demand.id] == 0)

            for room in rooms:
                regular, sports = [], []
                for group, demand in room_demands[room.name]:
                    if day >= (6 if group.has_saturday else 5):
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
                if any((entry.room_name or "").strip() == room.name.strip() for entry in fixed_entries if entry.day_of_week == day + 1 and entry.time_slot == slot + 1):
                    for variable in regular + sports: model.Add(variable == 0)

    # A teacher's weekly limit is also a hard constraint. Co-teaching still
    # counts as one conducted lesson for each of the two teachers.
    for teacher in teachers:
        teacher_load = [
            lesson[group.id, day, slot, demand.id]
            for group, demand in teacher_demands[teacher.id]
            for day in range(6 if group.has_saturday else 5)
            for slot in range(SLOTS_PER_DAY)
        ]
        if teacher_load:
            model.Add(sum(teacher_load) <= int(teacher.max_hours_per_week or 30))

    for group in groups:
        available = sum(item.remaining for item in group_demands[group.id])
        target = min(int(group.weekly_hours or 30), available)
        total = sum(
            lesson[group.id, day, slot, demand.id]
            for day in range(6 if group.has_saturday else 5)
            for slot in range(SLOTS_PER_DAY)
            for demand in group_demands[group.id]
        )
        model.Add(total == target if strict_load else total <= target)
        # First consume all available subject hours; only then prefer the
        # requested group norm. This prevents a half-used subject from being
        # reported as a shortage while free subject hours still exist.
        objective.append(100000000 * total)

    model.Maximize(sum(objective))
    return model, lesson, group_demands, room_by_teacher


def solve_global_week(db: Session, week: int, groups, teachers, rooms, course_plans, rules=None, strict_load=True, terms_by_group=None):
    rules = rules or []
    terms_by_group = terms_by_group or {}
    demands = build_subject_demands(db, groups, course_plans, rules, week, terms_by_group)
    active_ids = {item.group_id for item in demands}
    active_groups = [
        group for group in groups
        if group.id in active_ids and not db.query(models.ArchivedWeek).filter_by(
            group_id=group.id, week_number=week, is_archived=True
        ).first()
    ]
    if not active_groups:
        return True, f"Неделя {week}: все группы заархивированы или часы закончились."

    model, lesson, group_demands, room_by_teacher = make_model(
        db, week, active_groups, teachers, rooms,
        [item for item in demands if item.group_id in {group.id for group in active_groups}],
        strict_load, terms_by_group,
    )
    cp_solver = cp_model.CpSolver()
    # Independent weeks can be solved quickly in parallel. A valid solution
    # is still accepted only when every hard constraint above is satisfied.
    cp_solver.parameters.max_time_in_seconds = 4.0
    cp_solver.parameters.num_search_workers = 8
    status = cp_solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if strict_load:
            return solve_global_week(db, week, groups, teachers, rooms, course_plans, rules, strict_load=False, terms_by_group=terms_by_group)
        return False, f"Неделя {week}: невозможно составить безопасное расписание без накладок."

    for group in active_groups:
        year = db.query(models.AcademicYear).filter_by(id=terms_by_group[group.id].academic_year_id).first()
        for day in range(6 if group.has_saturday else 5):
            for slot in range(SLOTS_PER_DAY):
                for demand in group_demands[group.id]:
                    if cp_solver.Value(lesson[group.id, day, slot, demand.id]):
                        db.add(models.ScheduleEntry(
                            week_number=week,
                            day_of_week=day + 1,
                            time_slot=slot + 1,
                            room_name=room_by_teacher.get(demand.plan.teacher_id) or "Без кабинета",
                            teacher_id=demand.plan.teacher_id,
                            teacher2_id=demand.plan.teacher2_id,
                            group_id=group.id,
                            subject_name=demand.plan.subject_name,
                            status="planned",
                            term_id=terms_by_group[group.id].id,
                            academic_year_id=terms_by_group[group.id].academic_year_id,
                            schedule_date=calendar_date_for_slot(year, week, day),
                        ))
    db.commit()
    return True, f"Неделя {week}: создано безопасное расписание."


def trigger_global_generation(db: Session, approve_adjustments: bool = False):
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
    db.commit()
    unlocked_terms = [term for term in terms if not term.is_locked]
    if not unlocked_terms:
        return False, "Нет открытых семестров для генерации. Завершённые семестры доступны только для ручного редактирования."
    course_plans = db.query(models.CoursePlan).filter(models.CoursePlan.term_id.in_([term.id for term in unlocked_terms])).all()
    rules = db.query(models.AlgorithmRule).filter_by(is_active=True).all()
    if not groups:
        return False, "Нет групп для генерации."

    errors = validate_rules(groups, course_plans, rules)
    if errors:
        return False, "ОШИБКИ В НАСТРОЙКАХ АЛГОРИТМА:\n" + "\n".join(errors)

    archived = {
        (item.group_id, item.week_number)
        for item in db.query(models.ArchivedWeek).filter_by(is_archived=True).all()
    }
    # A group may have one archived week and many open weeks. Delete only
    # rows belonging to open weeks; filtering by group alone would destroy the
    # protected history of that group.
    for entry in db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.term_id.in_([term.id for term in unlocked_terms]),
        or_(models.ScheduleEntry.status == "planned", models.ScheduleEntry.status.is_(None))
    ).all():
        if (entry.group_id, entry.week_number) not in archived:
            db.delete(entry)
    db.commit()

    max_weeks = max(term.start_week + term.weeks - 1 for term in unlocked_terms)
    for week in range(1, max_weeks + 1):
        terms_by_group = {term.group_id: term for term in unlocked_terms if term.start_week <= week < term.start_week + term.weeks}
        active_groups = [group for group in groups if group.id in terms_by_group]
        if not active_groups:
            continue
        success, message = solve_global_week(db, week, active_groups, teachers, rooms, course_plans, rules, strict_load=True, terms_by_group=terms_by_group)
        if not success:
            return False, message

    shortages = []
    for plan in course_plans:
        missing = max(0, int(plan.total_hours or 0) - consumed_hours(db, plan))
        if missing:
            group = next((item for item in groups if item.id == plan.group_id), None)
            shortages.append(f"Гр. {group.number if group else plan.group_id}, {plan.subject_name}: не доставлено {missing} ч.")

    result = f"Расписание создано для открытых семестров до {max_weeks}-й календарной недели без накладок."
    if shortages:
        result += "\n\nНЕДОСТАВЛЕННЫЕ ЧАСЫ — измените планы вручную:\n" + "\n".join(shortages)
    return True, result
