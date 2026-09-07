import math
from typing import Optional, List
from ortools.sat.python import cp_model
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from . import models


# --- ИДЕНТИФИКАТОР ФИЗКУЛЬТУРЫ ---
def is_pe_subject(subject_name):
    if not subject_name: return False
    name = subject_name.lower()
    return 'физ' in name or 'спорт' in name or 'здоров' in name


class SubjectDemand:
    def __init__(self, plan, max_w_pairs: int, max_w_singles: int):
        self.plan = plan
        self.id = plan.id
        self.group_id = plan.group_id
        self.max_w_pairs = max_w_pairs
        self.max_w_singles = max_w_singles
        self.rem_slots = 0

    @property
    def teacher_ids(self) -> List[int]:
        ids = [self.plan.teacher_id]
        if self.plan.teacher2_id: ids.append(self.plan.teacher2_id)
        return list(set(filter(None, ids)))


def build_subject_demands(target_plans, groups_dict) -> List[SubjectDemand]:
    demands = []
    for p in target_plans:
        group = groups_dict.get(p.group_id)
        if not group: continue
        course = group.course
        w_hours = p.max_weekly_hours or 4

        if is_pe_subject(p.subject_name):
            if course in [1, 2]:
                mw_p = 0; mw_s = 3
            else:
                mw_p = 1; mw_s = 1
        else:
            if w_hours == 1:
                mw_p = 0; mw_s = 1
            else:
                mw_p = w_hours // 2; mw_s = w_hours % 2

        if mw_p > 0 or mw_s > 0:
            demands.append(SubjectDemand(p, mw_p, mw_s))
    return demands


def auto_pad_course_plans_for_all_weeks(db: Session, groups, course_plans):
    added_log = []
    for g in groups:
        target_weekly = getattr(g, 'weekly_hours', 30) or 30
        weeks_count = getattr(g, 'semester_weeks', 20) or 20
        target_total_semester = target_weekly * weeks_count

        all_g_plans = [p for p in course_plans if p.group_id == g.id]
        # Физру не балансируем, она должна быть стабильной
        g_plans = [p for p in all_g_plans if not is_pe_subject(p.subject_name)]
        if not g_plans: continue

        sum_weekly = 0
        for p in all_g_plans:
            if is_pe_subject(p.subject_name):
                sum_weekly += 3
            else:
                sum_weekly += (p.max_weekly_hours or 4)

        weekly_add = {}
        while sum_weekly < target_weekly:
            g_plans.sort(key=lambda x: (x.max_weekly_hours or 4, x.total_hours))
            p = g_plans[0]
            # Добавляем парами, чтобы не плодить одиночные огрызки
            inc = 2 if (target_weekly - sum_weekly) >= 2 else 1
            p.max_weekly_hours = (p.max_weekly_hours or 4) + inc
            weekly_add[p.subject_name] = weekly_add.get(p.subject_name, 0) + inc
            sum_weekly += inc

        sum_total = sum(p.total_hours for p in all_g_plans)
        total_add = {}
        while sum_total < target_total_semester:
            g_plans.sort(key=lambda x: (x.total_hours, x.max_weekly_hours or 4))
            p = g_plans[0]
            inc = 2 if (target_total_semester - sum_total) >= 2 else 1
            p.total_hours += inc
            total_add[p.subject_name] = total_add.get(p.subject_name, 0) + inc
            sum_total += inc

            if p.total_hours > (p.max_weekly_hours or 4) * weeks_count:
                p.max_weekly_hours = (p.max_weekly_hours or 4) + 1
                weekly_add[p.subject_name] = weekly_add.get(p.subject_name, 0) + 1

        if weekly_add or total_add:
            log_parts = []
            for p in g_plans:
                w_add = weekly_add.get(p.subject_name, 0)
                t_add = total_add.get(p.subject_name, 0)
                if w_add > 0 or t_add > 0:
                    log_parts.append(f"{p.subject_name} (+{t_add}ч сем / +{w_add}ч нед)")
            added_log.append(f"Гр. {g.number}: {', '.join(log_parts)}")

    if added_log: db.commit()
    return added_log


def preflight_check(db: Session, groups, teachers, rooms, course_plans):
    errors = []
    for t in teachers:
        t_plans = [p for p in course_plans if p.teacher_id == t.id or p.teacher2_id == t.id]
        requested_weekly_hours = sum((p.max_weekly_hours or 4) for p in t_plans)
        if requested_weekly_hours > t.max_hours_per_week:
            errors.append(
                f"Преподаватель '{t.name}' ПЕРЕГРУЖЕН: требуется {requested_weekly_hours} ч/нед (сумма по всем его группам), а лимит всего {t.max_hours_per_week} ч/нед.")

    room_occupants = {}
    for t in teachers:
        if t.room_id: room_occupants.setdefault(t.room_id, []).append(t.name)
    for r_id, occupants in room_occupants.items():
        if len(occupants) > 1:
            r_name = next((r.name for r in rooms if r.id == r_id), str(r_id))
            errors.append(
                f"КОНФЛИКТ КАБИНЕТА: За кабинетом '{r_name}' закреплено несколько преподавателей ({', '.join(occupants)}). Кабинет строго 1 на 1.")

    return errors


def solve_global_week(db: Session, week: int, groups, teachers, rooms, course_plans, slots_per_day: int = 12) -> tuple[
    bool, str]:
    archived_w = {a.group_id for a in db.query(models.ArchivedWeek).filter_by(week_number=week, is_archived=True).all()}
    active_groups = [g for g in groups if g.id not in archived_w]
    if not active_groups: return True, "Все группы в архиве."

    groups_dict = {g.id: g for g in active_groups}
    active_plans = [p for p in course_plans if p.group_id in groups_dict]
    demands = build_subject_demands(active_plans, groups_dict)
    if not demands: return True, "Нет планов для генерации."

    teacher_rooms = {t.id: next((r.name for r in rooms if r.id == t.room_id), "Без каб.") for t in teachers}

    # ФИКС 36 ЧАСОВ: Удаляем и `planned`, и старые багнутые записи без статуса (`None`)
    db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == week,
        models.ScheduleEntry.group_id.in_(groups_dict.keys()),
        or_(models.ScheduleEntry.status == 'planned', models.ScheduleEntry.status == None)
    ).delete()
    db.flush()

    for u in demands:
        consumed = db.query(models.ScheduleEntry).filter(
            models.ScheduleEntry.group_id == u.group_id,
            models.ScheduleEntry.subject_name == u.plan.subject_name,
            models.ScheduleEntry.status != 'canceled',
            models.ScheduleEntry.week_number < week
        ).count()
        u.rem_slots = max(0, u.plan.total_hours - consumed)

    if all(u.rem_slots <= 0 for u in demands):
        return True, "Часы завершены."

    fixed_entries = db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.status == 'planned',
        models.ScheduleEntry.week_number == week
    ).all()

    fixed_t = set()
    fixed_r = set()
    for e in fixed_entries:
        d_idx = e.day_of_week - 1
        h_idx = e.time_slot - 1
        if e.teacher_id: fixed_t.add((d_idx, h_idx, e.teacher_id))
        if e.teacher2_id: fixed_t.add((d_idx, h_idx, e.teacher2_id))
        if e.room_name: fixed_r.add((d_idx, h_idx, str(e.room_name).strip()))

    model = cp_model.CpModel()
    P = {};
    S = {};
    schedule = {}

    for g in active_groups:
        active_days = 6 if g.has_saturday else 5
        g_demands = [u for u in demands if u.group_id == g.id]
        for d in range(active_days):
            for h in range(slots_per_day):
                for u in g_demands:
                    schedule[(g.id, d, h, u.id)] = model.NewBoolVar(f'sch_g{g.id}_d{d}_h{h}_u{u.id}')
                    S[(g.id, d, h, u.id)] = model.NewBoolVar(f'S_g{g.id}_d{d}_h{h}_u{u.id}')
            for ps in range(slots_per_day // 2):
                for u in g_demands:
                    P[(g.id, d, ps, u.id)] = model.NewBoolVar(f'P_g{g.id}_d{d}_ps{ps}_u{u.id}')

    for d in range(6):
        for h in range(slots_per_day):
            for t in teachers:
                t_vars = []
                for g in active_groups:
                    if d >= (6 if g.has_saturday else 5): continue
                    for u in [ud for ud in demands if ud.group_id == g.id and t.id in ud.teacher_ids]:
                        t_vars.append(schedule[(g.id, d, h, u.id)])
                if t_vars: model.Add(sum(t_vars) <= 1)

            for r in rooms:
                r_vars = []
                for g in active_groups:
                    if d >= (6 if g.has_saturday else 5): continue
                    for u in [ud for ud in demands if
                              ud.group_id == g.id and teacher_rooms.get(ud.plan.teacher_id) == r.name]:
                        r_vars.append(schedule[(g.id, d, h, u.id)])
                if r_vars: model.Add(sum(r_vars) <= 1)

    unit_sums = []
    penalties = []
    early_bonus = []

    for g in active_groups:
        active_days = 6 if g.has_saturday else 5
        g_demands = [u for u in demands if u.group_id == g.id]

        target_weekly = getattr(g, 'weekly_hours', 30) or 30
        w_load = sum(
            schedule[(g.id, d, h, u.id)] for d in range(active_days) for h in range(slots_per_day) for u in g_demands)
        model.Add(w_load <= target_weekly)

        for d in range(active_days):
            for h in range(slots_per_day):
                ps = h // 2
                for u in g_demands:
                    model.Add(schedule[(g.id, d, h, u.id)] == P[(g.id, d, ps, u.id)] + S[(g.id, d, h, u.id)])
                    early_bonus.append(schedule[(g.id, d, h, u.id)] * (20 - h) * 10)
                model.Add(sum(schedule[(g.id, d, h, u.id)] for u in g_demands) <= 1)

            for u in g_demands:
                has_p = sum(P[(g.id, d, ps, u.id)] for ps in range(slots_per_day // 2))
                has_s = sum(S[(g.id, d, h, u.id)] for h in range(slots_per_day))
                model.Add(has_p <= 1);
                model.Add(has_s <= 1)

            is_active = []
            for h in range(slots_per_day):
                act = model.NewBoolVar(f'act_g{g.id}_d{d}_h{h}')
                model.Add(sum(schedule[(g.id, d, h, u.id)] for u in g_demands) == act)
                is_active.append(act)

            duration = model.NewIntVar(0, slots_per_day, f'dur_g{g.id}_{d}')
            model.Add(duration == sum(is_active))
            day_present = model.NewBoolVar(f'pres_g{g.id}_{d}')
            model.Add(duration > 0).OnlyEnforceIf(day_present)
            model.Add(duration == 0).OnlyEnforceIf(day_present.Not())

            start_var = model.NewIntVar(0, slots_per_day - 1, f'start_g{g.id}_{d}')
            end_var = model.NewIntVar(0, slots_per_day - 1, f'end_g{g.id}_{d}')

            model.AddAllowedAssignments([start_var], [(0,), (4,)])
            model.Add(end_var - start_var + 1 == duration).OnlyEnforceIf(day_present)

            is_second_shift = model.NewBoolVar(f'is_2nd_shift_g{g.id}_{d}')
            model.Add(start_var == 4).OnlyEnforceIf(is_second_shift)
            model.Add(start_var != 4).OnlyEnforceIf(is_second_shift.Not())
            penalties.append(5000 * is_second_shift)

            for h in range(slots_per_day):
                after_start = model.NewBoolVar('');
                before_end = model.NewBoolVar('')
                model.Add(start_var <= h).OnlyEnforceIf(after_start)
                model.Add(start_var > h).OnlyEnforceIf(after_start.Not())
                model.Add(end_var >= h).OnlyEnforceIf(before_end)
                model.Add(end_var < h).OnlyEnforceIf(before_end.Not())
                model.AddBoolAnd([after_start, before_end]).OnlyEnforceIf(is_active[h])
                model.AddBoolOr([after_start.Not(), before_end.Not()]).OnlyEnforceIf(is_active[h].Not())

            model.Add(duration <= 10)
            model.Add(end_var <= 9).OnlyEnforceIf(day_present)
            penalties.append(10000 * day_present.Not())

            is_5_pairs = model.NewBoolVar(f'is_5p_g{g.id}_{d}')
            model.Add(duration > 8).OnlyEnforceIf(is_5_pairs)
            model.Add(duration <= 8).OnlyEnforceIf(is_5_pairs.Not())
            penalties.append(500 * is_5_pairs)

        for u in g_demands:
            sum_p = sum(P[(g.id, d, ps, u.id)] for d in range(active_days) for ps in range(slots_per_day // 2))
            sum_s = sum(S[(g.id, d, h, u.id)] for d in range(active_days) for h in range(slots_per_day))

            # ДИНАМИЧЕСКИЙ ФИКС НЕЧЕТНЫХ ЧАСОВ (Проблема 30/31 и 32/33)
            # Если остался ровно 1 час, разрешаем поставить одиночный урок, даже если по правилу нужны пары!
            eff_max_s = u.max_w_singles
            if u.rem_slots % 2 != 0:
                eff_max_s = max(1, u.max_w_singles)

            model.Add(sum_p <= u.max_w_pairs)
            model.Add(sum_s <= eff_max_s)
            model.Add(sum_p * 2 + sum_s <= u.rem_slots)

            # АНТИ-ГОЛОДАНИЕ (Решает проблему Биологии 14/31)
            # Умножаем вес предмета на его остаток. Чем больше осталось часов, тем отчаяннее алгоритм будет их ставить!
            weight = u.rem_slots if u.rem_slots > 0 else 1
            unit_sums.append((sum_p * 2 + sum_s) * weight)

    model.Maximize(100000 * sum(unit_sums) - sum(penalties) + sum(early_bonus))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for g in active_groups:
            active_days = 6 if g.has_saturday else 5
            g_demands = [u for u in demands if u.group_id == g.id]
            for d in range(active_days):
                for h in range(slots_per_day):
                    for u in g_demands:
                        if solver.Value(schedule[(g.id, d, h, u.id)]) == 1:
                            db.add(models.ScheduleEntry(
                                week_number=week,
                                day_of_week=d + 1,
                                time_slot=h + 1,
                                room_name=teacher_rooms.get(u.plan.teacher_id, "Неизвестно"),
                                teacher_id=u.plan.teacher_id,
                                teacher2_id=u.plan.teacher2_id,
                                group_id=g.id,
                                subject_name=u.plan.subject_name,
                                status='planned'
                            ))
        db.commit()
        return True, "Успех"
    return False, f"Математический тупик на неделе {week}."


def trigger_global_generation(db: Session):
    teachers = db.query(models.Teacher).filter_by(is_active=True).all()
    groups = db.query(models.Group).all()
    rooms = db.query(models.Room).all()
    course_plans = db.query(models.CoursePlan).all()
    archived_weeks = db.query(models.ArchivedWeek).all()

    if not groups: return False, "Нет групп для генерации."

    max_weeks = max((g.semester_weeks or 20) for g in groups)
    added_log = auto_pad_course_plans_for_all_weeks(db, groups, course_plans)

    errors = preflight_check(db, groups, teachers, rooms, course_plans)
    if errors:
        err_msg = "ОШИБКИ ДО ГЕНЕРАЦИИ:\n" + "\n".join(errors)
        if added_log: err_msg += "\n\n(Были добавлены часы для балансировки)."
        return False, err_msg

    archived_set = {(a.group_id, a.week_number) for a in archived_weeks if a.is_archived}
    entries = db.query(models.ScheduleEntry).filter(
        or_(models.ScheduleEntry.status == 'planned', models.ScheduleEntry.status == None)).all()
    for e in entries:
        if (e.group_id, e.week_number) not in archived_set:
            db.delete(e)
    db.commit()

    success_weeks = 0
    for w in range(1, max_weeks + 1):
        success, msg = solve_global_week(db, w, groups, teachers, rooms, course_plans)
        if not success:
            return False, f"Ошибка на {w} неделе: {msg}"
        success_weeks += 1

    final_msg = f"Успешно сгенерировано расписание на {success_weeks} недель!"
    if added_log:
        final_msg += "\n\nАВТОМАТИЧЕСКИ ДОБАВЛЕНО ЧАСОВ:\n" + "\n".join(added_log)

    return True, final_msg
