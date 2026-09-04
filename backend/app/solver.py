import math
from typing import Optional, List
from ortools.sat.python import cp_model
from sqlalchemy.orm import Session
from . import models


class ScheduleUnit:
    def __init__(self, unit_id: str, plan_a, plan_b=None, target_runs: int = 0, max_w_runs: int = 1):
        self.id = unit_id
        self.plan_a = plan_a
        self.plan_b = plan_b
        self.is_combo = (plan_b is not None)
        self.target_runs = target_runs
        self.max_w_runs = max_w_runs

    @property
    def teacher_ids(self) -> List[int]:
        ids = [self.plan_a.teacher_id]
        if self.plan_a.teacher2_id: ids.append(self.plan_a.teacher2_id)
        if self.plan_b:
            ids.append(self.plan_b.teacher_id)
            if self.plan_b.teacher2_id: ids.append(self.plan_b.teacher2_id)
        return list(set(filter(None, ids)))


def build_schedule_units(target_plans) -> List[ScheduleUnit]:
    units = []

    single_plans = [p for p in target_plans if getattr(p, 'max_weekly_hours', 4) == 1 or p.total_hours <= 22]
    regular_plans = [p for p in target_plans if p not in single_plans]

    for p in regular_plans:
        runs = p.total_hours // 2
        max_w = max(1, getattr(p, 'max_weekly_hours', 4) // 2)
        if runs > 0:
            units.append(ScheduleUnit(f"reg_{p.id}", p, target_runs=runs, max_w_runs=max_w))

    by_teacher = {}
    for p in single_plans:
        by_teacher.setdefault(p.teacher_id, []).append(p)

    remaining = []
    c_idx = 1
    for t_id, t_plans in by_teacher.items():
        while len(t_plans) >= 2:
            p1, p2 = t_plans.pop(0), t_plans.pop(0)
            common_runs = min(p1.total_hours, p2.total_hours)
            units.append(ScheduleUnit(f"combo_{c_idx}", p1, p2, target_runs=common_runs, max_w_runs=1))
            c_idx += 1
            larger = p1 if p1.total_hours > p2.total_hours else p2
            diff = abs(p1.total_hours - p2.total_hours)
            if diff // 2 > 0:
                units.append(ScheduleUnit(f"rem_{larger.id}_{c_idx}", larger, target_runs=diff // 2, max_w_runs=1))
        if t_plans: remaining.extend(t_plans)

    while len(remaining) >= 2:
        p1, p2 = remaining.pop(0), remaining.pop(0)
        common_runs = min(p1.total_hours, p2.total_hours)
        units.append(ScheduleUnit(f"combo_{c_idx}", p1, p2, target_runs=common_runs, max_w_runs=1))
        c_idx += 1
        larger = p1 if p1.total_hours > p2.total_hours else p2
        diff = abs(p1.total_hours - p2.total_hours)
        if diff // 2 > 0:
            units.append(ScheduleUnit(f"rem_{larger.id}_{c_idx}", larger, target_runs=diff // 2, max_w_runs=1))

    if remaining:
        p = remaining.pop(0)
        if p.total_hours // 2 > 0:
            units.append(ScheduleUnit(f"single_{p.id}", p, target_runs=p.total_hours // 2, max_w_runs=1))

    return units


def solve_for_single_group(db: Session, target_group, teachers, course_plans, all_groups, max_days: int = 6,
                           slots_per_day: int = 6) -> bool:
    pairs_per_day = slots_per_day // 2
    teacher_rooms = {t.id: t.default_room for t in teachers}
    available_weeks = list(range(1, 21))

    target_plans = [p for p in course_plans if p.group_id == target_group.id]
    units = build_schedule_units(target_plans)
    if not units: return True

    archived = db.query(models.ArchivedWeek).filter_by(group_id=target_group.id, is_archived=True).all()
    archived_w = {a.week_number for a in archived}
    available_weeks = [w for w in available_weeks if w not in archived_w]
    if not available_weeks: return True

    active_days = 6 if getattr(target_group, 'has_saturday', False) else 5
    configured_limit = math.ceil((getattr(target_group, 'weekly_hours', 30) or 30) / 2)

    model = cp_model.CpModel()
    schedule = {}

    for w in available_weeks:
        for d in range(active_days):
            for ps in range(pairs_per_day):
                for u in units:
                    schedule[(w, d, ps, u.id)] = model.NewBoolVar(f's_{w}_{d}_{ps}_{u.id}')

    # 1. Максимум 1 пара в слоте
    for w in available_weeks:
        for d in range(active_days):
            for ps in range(pairs_per_day):
                model.Add(sum(schedule[(w, d, ps, u.id)] for u in units) <= 1)

    # 2. Не превышать план
    unit_sums = []
    for u in units:
        total = sum(schedule[(w, d, ps, u.id)] for w in available_weeks for d in range(active_days) for ps in
                    range(pairs_per_day))
        model.Add(total <= u.target_runs)
        unit_sums.append(total)

        # 3. Лимит пар в неделю (по настройкам предмета)
        for w in available_weeks:
            model.Add(sum(
                schedule[(w, d, ps, u.id)] for d in range(active_days) for ps in range(pairs_per_day)) <= u.max_w_runs)

    # 4. СПЛОШНОЙ ГРАФИК БЕЗ ОКОН (СТАРТ С 1 ПАРЫ)
    for w in available_weeks:
        for d in range(active_days):
            is_active = []
            for ps in range(pairs_per_day):
                act = model.NewBoolVar(f'act_{w}_{d}_{ps}')
                model.Add(sum(schedule[(w, d, ps, u.id)] for u in units) == act)
                is_active.append(act)
            # Если активна следующая пара, обязана быть активной предыдущая (1 <- 2 <- 3)
            for ps in range(pairs_per_day - 1):
                model.AddImplication(is_active[ps + 1], is_active[ps])

    # 5. Общий лимит группы
    for w in available_weeks:
        w_load = sum(
            schedule[(w, d, ps, u.id)] for d in range(active_days) for ps in range(pairs_per_day) for u in units)
        model.Add(w_load <= min(configured_limit, active_days * pairs_per_day))

    # 6. Ограничение нагрузки учителей
    for w in available_weeks:
        for t in teachers:
            t_units = [u for u in units if t.id in u.teacher_ids]
            if t_units:
                model.Add(
                    2 * sum(
                        schedule[(w, d, ps, u.id)] for d in range(active_days) for ps in range(pairs_per_day) for u in
                        t_units)
                    <= t.max_hours_per_week
                )

    # 7. СИСТЕМА ЖЕСТКОЙ БАЛАНСИРОВКИ И ЗАПРЕТ ПУСТЫХ ДНЕЙ
    penalties = []
    for w in available_weeks:
        day_loads = []
        for d in range(active_days):
            d_load = sum(schedule[(w, d, ps, u.id)] for ps in range(pairs_per_day) for u in units)
            day_loads.append(d_load)

            day_empty = model.NewBoolVar(f'empty_{w}_{d}')
            model.Add(d_load == 0).OnlyEnforceIf(day_empty)
            model.Add(d_load > 0).OnlyEnforceIf(day_empty.Not())

            day_is_one = model.NewBoolVar(f'one_{w}_{d}')
            model.Add(d_load == 1).OnlyEnforceIf(day_is_one)
            model.Add(d_load != 1).OnlyEnforceIf(day_is_one.Not())

            # ШТРАФ 10,000: Пустых дней быть не должно ни в коем случае!
            penalties.append(10000 * day_empty)

            # ШТРАФ 500: Избегать 1 пары в день, чтобы студенты не ездили зря
            penalties.append(500 * day_is_one)

        # РАВНОМЕРНОСТЬ: разница нагрузки между днями минимальна
        max_wd = model.NewIntVar(0, pairs_per_day, f'max_wd_w{w}')
        min_wd = model.NewIntVar(0, pairs_per_day, f'min_wd_w{w}')
        model.AddMaxEquality(max_wd, day_loads)
        model.AddMinEquality(min_wd, day_loads)

        diff_wd = model.NewIntVar(0, pairs_per_day, f'diff_wd_w{w}')
        model.Add(diff_wd == max_wd - min_wd)

        # Штраф 1,000 за перекосы (чтобы избежать 3 пар в Пн и 1 пары во Вт)
        penalties.append(1000 * diff_wd)

    # МАКСИМИЗАЦИЯ: Расставляем уроки, уворачиваясь от штрафов
    model.Maximize(10000 * sum(unit_sums) - sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        db.query(models.ScheduleEntry).filter_by(group_id=target_group.id).filter(
            models.ScheduleEntry.week_number.in_(available_weeks)).delete()

        for w in available_weeks:
            for d in range(active_days):
                for ps in range(pairs_per_day):
                    for u in units:
                        if solver.Value(schedule[(w, d, ps, u.id)]) == 1:
                            if u.is_combo:
                                db.add(models.ScheduleEntry(week_number=w, day_of_week=d + 1, time_slot=ps * 2 + 1,
                                                            room=teacher_rooms.get(u.plan_a.teacher_id, ""),
                                                            teacher_id=u.plan_a.teacher_id,
                                                            teacher2_id=u.plan_a.teacher2_id, group_id=target_group.id,
                                                            subject_name=u.plan_a.subject_name))
                                db.add(models.ScheduleEntry(week_number=w, day_of_week=d + 1, time_slot=ps * 2 + 2,
                                                            room=teacher_rooms.get(u.plan_b.teacher_id, ""),
                                                            teacher_id=u.plan_b.teacher_id,
                                                            teacher2_id=u.plan_b.teacher2_id, group_id=target_group.id,
                                                            subject_name=u.plan_b.subject_name))
                            else:
                                db.add(models.ScheduleEntry(week_number=w, day_of_week=d + 1, time_slot=ps * 2 + 1,
                                                            room=teacher_rooms.get(u.plan_a.teacher_id, ""),
                                                            teacher_id=u.plan_a.teacher_id,
                                                            teacher2_id=u.plan_a.teacher2_id, group_id=target_group.id,
                                                            subject_name=u.plan_a.subject_name))
                                db.add(models.ScheduleEntry(week_number=w, day_of_week=d + 1, time_slot=ps * 2 + 2,
                                                            room=teacher_rooms.get(u.plan_a.teacher_id, ""),
                                                            teacher_id=u.plan_a.teacher_id,
                                                            teacher2_id=u.plan_a.teacher2_id, group_id=target_group.id,
                                                            subject_name=u.plan_a.subject_name))
        db.commit()
        return True
    return False


def generate_and_save_schedule(db, teachers, groups, course_plans, target_group_id=None):
    if target_group_id:
        tg = next((g for g in groups if g.id == target_group_id), None)
        return solve_for_single_group(db, tg, teachers, course_plans, groups)
    for g in groups:
        if not solve_for_single_group(db, g, teachers, course_plans, groups): return False
    return True
