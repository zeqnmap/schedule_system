import math
from typing import Optional, List
from ortools.sat.python import cp_model
from sqlalchemy.orm import Session
from sqlalchemy import func
from . import models


class SubjectDemand:
    def __init__(self, plan, pairs: int, singles: int, max_w_pairs: int, max_w_singles: int):
        self.plan = plan
        self.id = plan.id
        self.group_id = plan.group_id
        self.pairs = pairs
        self.singles = singles
        self.max_w_pairs = max_w_pairs
        self.max_w_singles = max_w_singles

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
        t_hours = p.total_hours

        if w_hours == 3 and course in [1, 2]:
            pairs = 0;
            singles = t_hours;
            mw_p = 0;
            mw_s = 3
        elif w_hours == 3 and course in [3, 4]:
            pairs = t_hours // 3;
            singles = t_hours - (pairs * 2);
            mw_p = 1;
            mw_s = 1
        elif w_hours == 1:
            pairs = 0;
            singles = t_hours;
            mw_p = 0;
            mw_s = 1
        else:
            pairs = t_hours // 2;
            singles = t_hours % 2;
            mw_p = w_hours // 2;
            mw_s = w_hours % 2

        if pairs > 0 or singles > 0:
            demands.append(SubjectDemand(p, pairs, singles, mw_p, mw_s))
    return demands


# --- ШАГ 0: АВТО-ДОБАВЛЕНИЕ ЧАСОВ (AUTO-PADDING) ---
def auto_pad_course_plans(db: Session, week_number: int, groups, course_plans, archived_weeks):
    added_log = []

    for g in groups:
        target = getattr(g, 'weekly_hours', 30) or 30
        course = getattr(g, 'course', 1)
        g_plans = [p for p in course_plans if p.group_id == g.id]
        if not g_plans: continue

        archived_w = {a.week_number for a in archived_weeks if a.group_id == g.id and a.is_archived}
        if week_number in archived_w: continue

        # Узнаем, сколько уроков уже проведено
        archived_counts = {}
        if archived_w:
            counts = db.query(models.ScheduleEntry.subject_name, func.count(models.ScheduleEntry.id)) \
                .filter(models.ScheduleEntry.group_id == g.id) \
                .filter(models.ScheduleEntry.week_number.in_(archived_w)) \
                .group_by(models.ScheduleEntry.subject_name).all()
            count_dict = {name: cnt for name, cnt in counts}
            for p in g_plans: archived_counts[p.id] = count_dict.get(p.subject_name, 0)
        else:
            archived_counts = {p.id: 0 for p in g_plans}

        def get_current_possible():
            total_possible = 0
            for p in g_plans:
                w_hours = p.max_weekly_hours or 4
                t_hours = p.total_hours

                if w_hours == 3 and course in [1, 2]:
                    mw_p = 0; mw_s = 3
                elif w_hours == 3 and course in [3, 4]:
                    mw_p = 1; mw_s = 1
                elif w_hours == 1:
                    mw_p = 0; mw_s = 1
                else:
                    mw_p = w_hours // 2; mw_s = w_hours % 2

                arch_cnt = archived_counts.get(p.id, 0)
                rem_slots = max(0, t_hours - arch_cnt)

                if w_hours == 3 and course in [1, 2]:
                    rem_p = 0; rem_s = rem_slots
                elif w_hours == 1:
                    rem_p = 0; rem_s = rem_slots
                else:
                    rem_p = rem_slots // 2; rem_s = rem_slots % 2

                total_possible += (min(mw_p, rem_p) * 2) + min(mw_s, rem_s)
            return total_possible

        group_added = {}
        while True:
            current_possible = get_current_possible()
            if current_possible >= target: break

            # Сортируем: сначала предметы с наименьшим числом часов в неделю!
            g_plans.sort(key=lambda x: (x.max_weekly_hours, x.total_hours))
            lowest_plan = g_plans[0]

            # Накидываем часы в базу
            lowest_plan.max_weekly_hours += 1
            lowest_plan.total_hours += 1

            group_added[lowest_plan.subject_name] = group_added.get(lowest_plan.subject_name, 0) + 1

        if group_added:
            details = ", ".join([f"{subj} (+{cnt}ч)" for subj, cnt in group_added.items()])
            added_log.append(f"Гр. {g.number}: {details}")

    if added_log:
        db.commit()  # Сохраняем изменения учебных планов навсегда

    return added_log


# --- ШАГ 1: ПРЕД-ПРОВЕРКА (АУДИТ ОШИБОК) ---
def preflight_check(db: Session, week_number: int, groups, teachers, rooms, course_plans, archived_weeks):
    errors = []

    # 1. Проверка нагрузки учителей
    for t in teachers:
        t_plans = [p for p in course_plans if p.teacher_id == t.id or p.teacher2_id == t.id]
        requested_weekly_hours = sum(p.max_weekly_hours for p in t_plans)
        if requested_weekly_hours > t.max_hours_per_week:
            errors.append(
                f"Преподаватель '{t.name}' ПЕРЕГРУЖЕН: требуется {requested_weekly_hours} ч/нед (сумма по всем его группам), а лимит всего {t.max_hours_per_week} ч/нед. Добавьте лимит в карточке.")

    # 2. Проверка конфликта кабинетов
    room_occupants = {}
    for t in teachers:
        if t.room_id: room_occupants.setdefault(t.room_id, []).append(t.name)
    for r_id, occupants in room_occupants.items():
        if len(occupants) > 1:
            r_name = next((r.name for r in rooms if r.id == r_id), str(r_id))
            errors.append(
                f"КОНФЛИКТ КАБИНЕТА: За кабинетом '{r_name}' закреплено сразу несколько преподавателей ({', '.join(occupants)}). Кабинет должен быть строго 1 на 1.")

    return errors


# --- ШАГ 2: ГЛОБАЛЬНЫЙ РЕШАТЕЛЬ ДЛЯ ВСЕХ ГРУПП ОДНОВРЕМЕННО ---
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

    db.query(models.ScheduleEntry).filter(
        models.ScheduleEntry.week_number == week,
        models.ScheduleEntry.group_id.in_(groups_dict.keys())
    ).delete()

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

    # ГЛОБАЛЬНЫЕ БЛОКИРОВКИ: Преподаватели и Кабинеты
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

    for g in active_groups:
        active_days = 6 if g.has_saturday else 5
        g_demands = [u for u in demands if u.group_id == g.id]

        for d in range(active_days):
            for h in range(slots_per_day):
                ps = h // 2
                for u in g_demands:
                    model.Add(schedule[(g.id, d, h, u.id)] == P[(g.id, d, ps, u.id)] + S[(g.id, d, h, u.id)])
                model.Add(sum(schedule[(g.id, d, h, u.id)] for u in g_demands) <= 1)

            for u in g_demands:
                has_p = sum(P[(g.id, d, ps, u.id)] for ps in range(slots_per_day // 2))
                has_s = sum(S[(g.id, d, h, u.id)] for h in range(slots_per_day))
                model.Add(has_p + has_s <= 1)

                # ИДЕАЛЬНЫЙ МОНОЛИТ БЕЗ ОКОН
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

            # Старт либо с 1 урока (0), либо с 3 пары (4)
            model.AddAllowedAssignments([start_var], [(0,), (4,)])
            model.Add(end_var - start_var + 1 == duration).OnlyEnforceIf(day_present)

            for h in range(slots_per_day):
                after_start = model.NewBoolVar('')
                before_end = model.NewBoolVar('')
                model.Add(start_var <= h).OnlyEnforceIf(after_start)
                model.Add(start_var > h).OnlyEnforceIf(after_start.Not())
                model.Add(end_var >= h).OnlyEnforceIf(before_end)
                model.Add(end_var < h).OnlyEnforceIf(before_end.Not())

                model.AddBoolAnd([after_start, before_end]).OnlyEnforceIf(is_active[h])
                model.AddBoolOr([after_start.Not(), before_end.Not()]).OnlyEnforceIf(is_active[h].Not())

            model.Add(duration <= 10)  # Максимум 5 пар в день
            model.Add(end_var <= 9).OnlyEnforceIf(day_present)  # При старте с 3 пары закончат макс через 3 пары
            penalties.append(10000 * day_present.Not())

            is_5_pairs = model.NewBoolVar(f'is_5p_g{g.id}_{d}')
            model.Add(duration > 8).OnlyEnforceIf(is_5_pairs)
            model.Add(duration <= 8).OnlyEnforceIf(is_5_pairs.Not())
            penalties.append(500 * is_5_pairs)

        for u in g_demands:
            sum_p = sum(P[(g.id, d, ps, u.id)] for d in range(active_days) for ps in range(slots_per_day // 2))
            sum_s = sum(S[(g.id, d, h, u.id)] for d in range(active_days) for h in range(slots_per_day))
            model.Add(sum_p <= u.max_w_pairs)
            model.Add(sum_s <= u.max_w_singles)
            unit_sums.append(sum_p * 2 + sum_s)

    model.Maximize(100000 * sum(unit_sums) - sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
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
                                subject_name=u.plan.subject_name
                            ))
        db.commit()
        return True, "Расписание успешно сгенерировано!"
    return False, "КРИТИЧЕСКАЯ ОШИБКА: Алгоритм зашел в тупик. Слишком мало преподавателей/кабинетов, чтобы уместить все группы без 'окон'."


def trigger_global_generation(db: Session, week_number: int):
    teachers = db.query(models.Teacher).filter_by(is_active=True).all()
    groups = db.query(models.Group).all()
    rooms = db.query(models.Room).all()
    course_plans = db.query(models.CoursePlan).all()
    archived_weeks = db.query(models.ArchivedWeek).all()

    # ШАГ 0: АВТО-БАЛАНСИРОВКА ЧАСОВ (Auto-Padding)
    added_log = auto_pad_course_plans(db, week_number, groups, course_plans, archived_weeks)

    # ШАГ 1: АУДИТ ОШИБОК
    errors = preflight_check(db, week_number, groups, teachers, rooms, course_plans, archived_weeks)
    if errors:
        err_msg = "ОШИБКИ ДО ГЕНЕРАЦИИ:\n" + "\n".join(errors)
        if added_log: err_msg += "\n\n(Алгоритм добавил новые часы группам, из-за чего мог возникнуть перегруз преподавателей)."
        return False, err_msg

    # ШАГ 2: ГЛОБАЛЬНАЯ ГЕНЕРАЦИЯ
    success, msg = solve_global_week(db, week_number, groups, teachers, rooms, course_plans)

    # ЕСЛИ ВСЕ УСПЕШНО, ВЫВОДИМ ОТЧЕТ
    if success and added_log:
        msg += "\n\nАВТОМАТИЧЕСКИ ДОБАВЛЕНО ДО НОРМЫ (Часы сохранены в базу):\n" + "\n".join(added_log)

    return success, msg
