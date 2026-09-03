from ortools.sat.python import cp_model
from sqlalchemy.orm import Session
from . import models


def generate_and_save_schedule(db: Session, teachers, groups, course_plans, num_days=5, slots_per_day=5):
    model = cp_model.CpModel()
    schedule = {}

    teacher_rooms = {t.id: t.default_room for t in teachers}
    WEEKS_IN_SEMESTER = 20
    NUM_CYCLE_WEEKS = 2  # 0 - нечетная, 1 - четная неделя

    # 1. Переменные
    for w in range(NUM_CYCLE_WEEKS):
        for d in range(num_days):
            for s in range(slots_per_day):
                for p in course_plans:
                    schedule[(w, d, s, p.id)] = model.NewBoolVar(f'shift_w{w}_d{d}_s{s}_p{p.id}')

    # 2. ОГРАНИЧЕНИЯ

    # А) Преподаватель ведет максимум 1 пару одновременно
    for w in range(NUM_CYCLE_WEEKS):
        for d in range(num_days):
            for s in range(slots_per_day):
                for t in teachers:
                    t_plans = [p for p in course_plans if p.teacher_id == t.id or p.teacher2_id == t.id]
                    model.Add(sum(schedule[(w, d, s, p.id)] for p in t_plans) <= 1)

    # Б) Группа на одной паре одновременно
    for w in range(NUM_CYCLE_WEEKS):
        for d in range(num_days):
            for s in range(slots_per_day):
                for g in groups:
                    g_plans = [p for p in course_plans if p.group_id == g.id]
                    model.Add(sum(schedule[(w, d, s, p.id)] for p in g_plans) <= 1)

    # В) Расчет часов и баланс двухнедельного цикла
    for p in course_plans:
        pairs_in_2_weeks = round(p.total_hours / 10)
        model.Add(
            sum(schedule[(w, d, s, p.id)] for w in range(NUM_CYCLE_WEEKS) for d in range(num_days) for s in
                range(slots_per_day))
            == pairs_in_2_weeks
        )
        w0_count = sum(schedule[(0, d, s, p.id)] for d in range(num_days) for s in range(slots_per_day))
        w1_count = sum(schedule[(1, d, s, p.id)] for d in range(num_days) for s in range(slots_per_day))
        model.Add(w0_count - w1_count <= 1)
        model.Add(w1_count - w0_count <= 1)

    # Г) Лимит часов преподавателя в неделю
    for w in range(NUM_CYCLE_WEEKS):
        for t in teachers:
            t_plans = [p for p in course_plans if p.teacher_id == t.id or p.teacher2_id == t.id]
            model.Add(sum(schedule[(w, d, s, p.id)] for d in range(num_days) for s in range(slots_per_day) for p in
                          t_plans) <= t.max_hours_per_week)

    # Д) Не более 2 пар одного предмета в день у группы
    for w in range(NUM_CYCLE_WEEKS):
        for d in range(num_days):
            for p in course_plans:
                model.Add(sum(schedule[(w, d, s, p.id)] for s in range(slots_per_day)) <= 2)

    # Е) СПЛОШНОЙ БЛОК БЕЗ ОКОН И СТАРТ С 1-Й ПАРЫ + РАВНОМЕРНОСТЬ
    for w in range(NUM_CYCLE_WEEKS):
        for g in groups:
            g_plans = [p for p in course_plans if p.group_id == g.id]
            if not g_plans:
                continue

            day_loads = []
            for d in range(num_days):
                is_active = []
                for s in range(slots_per_day):
                    act = model.NewBoolVar(f'act_w{w}_g{g.id}_d{d}_s{s}')
                    model.Add(sum(schedule[(w, d, s, p.id)] for p in g_plans) == act)
                    is_active.append(act)

                # Жесткое правило заполнения сверху вниз:
                # слот s+1 может быть занят только если занят слот s
                for s in range(slots_per_day - 1):
                    model.Add(is_active[s] >= is_active[s + 1])

                day_load = model.NewIntVar(0, slots_per_day, f'load_w{w}_g{g.id}_d{d}')
                model.Add(day_load == sum(is_active))
                day_loads.append(day_load)

            # Равномерность нагрузки: разброс между днями не больше 1 пары
            max_load_g = model.NewIntVar(0, slots_per_day, f'max_load_w{w}_g{g.id}')
            min_load_g = model.NewIntVar(0, slots_per_day, f'min_load_w{w}_g{g.id}')

            for d in range(num_days):
                model.Add(day_loads[d] <= max_load_g)
                model.Add(day_loads[d] >= min_load_g)

            model.Add(max_load_g - min_load_g <= 1)

    # 3. Решение
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        db.query(models.ScheduleEntry).delete()

        for week in range(1, WEEKS_IN_SEMESTER + 1):
            w_idx = 0 if (week % 2 != 0) else 1

            for d in range(num_days):
                for s in range(slots_per_day):
                    for p in course_plans:
                        if solver.Value(schedule[(w_idx, d, s, p.id)]) == 1:
                            new_entry = models.ScheduleEntry(
                                week_number=week,
                                day_of_week=d + 1,
                                time_slot=s + 1,
                                room=teacher_rooms.get(p.teacher_id, "Неизвестно"),
                                teacher_id=p.teacher_id,
                                teacher2_id=p.teacher2_id,
                                group_id=p.group_id,
                                subject_name=p.subject_name
                            )
                            db.add(new_entry)
        db.commit()
        return True
    return False
