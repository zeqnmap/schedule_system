from __future__ import annotations

import io
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("ADMIN_LOGIN", "test-owner")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-123")
os.environ.setdefault("SESSION_SECRET", "schedule-test-session-secret")
os.environ.setdefault("SCHEDULE_DATABASE_URL", f"sqlite:///{Path(tempfile.gettempdir()) / 'schedule-system-test-import.db'}")

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from reportlab.pdfgen import canvas
from pypdf import PdfReader

from backend.app import database, models, schemas, solver, migrations
migrations.upgrade(database.engine)
from backend.app import main


class ScheduleSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.Session()
        self.original_session_factory = database.SessionLocal
        database.SessionLocal = self.Session
        self.original_solver_solve = solver.cp_model.CpSolver.Solve

        def solve_feasibility_only(instance, model, *args, **kwargs):
            instance.parameters.max_time_in_seconds = 2
            instance.parameters.num_search_workers = 2
            return self.original_solver_solve(instance, model, *args, **kwargs)

        solver.cp_model.CpSolver.Solve = solve_feasibility_only

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        self.original_main_overrides = dict(main.app.dependency_overrides)
        main.app.dependency_overrides[database.get_db] = override_db
        self.client = TestClient(main.app)
        admin = models.User(
            login="test-admin", password_hash="unused", role="admin",
            is_admin=False, is_active=True,
        )
        self.db.add(admin)
        self.db.commit()
        self.client.cookies.set("schedule_session", main.make_session(admin.id))

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(self.original_main_overrides)
        database.SessionLocal = self.original_session_factory
        solver.cp_model.CpSolver.Solve = self.original_solver_solve
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def fixture(self, with_plan=True):
        room = models.Room(name="101")
        teacher = models.Teacher(name="Преподаватель", max_hours_per_week=20)
        group = models.Group(number=345, course=1, weekly_hours=4, semester_weeks=1)
        year = models.AcademicYear(
            name="2026-2027", start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31), is_active=True,
        )
        self.db.add_all([room, teacher, group, year])
        self.db.flush()
        term = models.GroupTerm(
            academic_year_id=year.id, group_id=group.id, term_number=1,
            name="1 семестр", start_week=1, weeks=1,
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 7),
            is_active=True, is_locked=False,
        )
        self.db.add(term)
        self.db.flush()
        plan = None
        if with_plan:
            plan = models.CoursePlan(
                subject_name="Математика", total_hours=2, max_weekly_hours=2,
                group_id=group.id, teacher_id=teacher.id, room_name=room.name,
                term_id=term.id, academic_year_id=year.id,
            )
            self.db.add(plan)
        self.db.commit()
        return SimpleNamespace(room=room, teacher=teacher, group=group, year=year, term=term, plan=plan)

    def test_schema_migration_is_recorded_and_idempotent(self):
        engine = create_engine(
            f"sqlite:///{Path(self.temp.name) / 'migration.db'}",
            connect_args={"check_same_thread": False},
        )
        try:
            self.assertTrue(migrations.upgrade(engine))
            self.assertEqual(migrations.current_revision(engine), migrations.CURRENT_REVISION)
            self.assertFalse(migrations.upgrade(engine))
            self.assertEqual(migrations.current_revision(engine), migrations.CURRENT_REVISION)
        finally:
            engine.dispose()

    def test_require_current_rejects_unmigrated_engine(self):
        from sqlalchemy import create_engine
        engine = create_engine("sqlite:///:memory:")
        try:
            with self.assertRaisesRegex(RuntimeError, "Run `python -m backend.app.migrate`"):
                migrations.require_current(engine)
        finally:
            engine.dispose()

    def test_password_hash_verifies_only_original_password(self):
        encoded = main.hash_password("a-long-test-password")
        self.assertTrue(main.verify_password("a-long-test-password", encoded))
        self.assertFalse(main.verify_password("incorrect", encoded))
        self.assertFalse(main.verify_password("broken", "not-a-hash"))

    def test_session_signature_is_accepted_and_tampering_rejected(self):
        token = main.make_session(41)
        request = SimpleNamespace(cookies={"schedule_session": token})
        user = models.User(id=41, login="session-user", password_hash="unused", is_active=True)
        self.db.add(user)
        self.db.commit()
        self.assertEqual(main.current_user(request, self.db).id, 41)
        request.cookies["schedule_session"] = token[:-1] + ("0" if token[-1] != "0" else "1")
        with self.assertRaises(HTTPException) as raised:
            main.current_user(request, self.db)
        self.assertEqual(raised.exception.status_code, 401)

    def test_login_sets_http_only_session_and_wrong_password_is_rejected(self):
        self.db.add(models.User(
            login="dispatch", password_hash=main.hash_password("secure-pass"),
            role="admin", is_active=True,
        ))
        self.db.commit()
        response = self.client.post("/auth/login", json={"login": "dispatch", "password": "secure-pass"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(response.json()["role"], "admin")
        wrong = self.client.post("/auth/login", json={"login": "dispatch", "password": "wrong-pass"})
        self.assertEqual(wrong.status_code, 401)

    def test_same_origin_schedule_write_reaches_validation_instead_of_middleware(self):
        response = self.client.post(
            "/schedule/",
            headers={"Origin": "http://testserver"},
            json={},
        )
        self.assertEqual(response.status_code, 422)

    def test_editor_cannot_modify_reference_data(self):
        editor = models.User(login="read-only", password_hash="unused", role="user", is_active=True)
        self.db.add(editor)
        self.db.commit()
        self.client.cookies.set("schedule_session", main.make_session(editor.id))
        denied = self.client.post("/rooms/", json={"name": "101"})
        self.assertEqual(denied.status_code, 403)
        denied = self.client.post("/subjects/", json={"name": "Математика"})
        self.assertEqual(denied.status_code, 403)

    def test_reference_and_course_plan_crud(self):
        item = self.fixture(with_plan=False)
        room = self.client.post("/rooms/", json={"name": "102"})
        self.assertEqual(room.status_code, 200, room.text)
        teacher = self.client.post("/teachers/", json={"name": "Новый преподаватель", "room_id": room.json()["id"]})
        self.assertEqual(teacher.status_code, 200, teacher.text)
        subject = self.client.post("/subjects/", json={"name": "Новый предмет"})
        self.assertEqual(subject.status_code, 200, subject.text)
        plan_payload = {
            "subject_name": "Новый предмет", "total_hours": 12, "max_weekly_hours": 3,
            "teacher_id": teacher.json()["id"], "group_id": item.group.id,
            "room_name": "102", "term_id": item.term.id,
        }
        plan = self.client.post("/course_plans/", json=plan_payload)
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertEqual(plan.json()["academic_year_id"], item.year.id)
        plan_payload["total_hours"] = 16
        updated = self.client.put(f"/course_plans/{plan.json()['id']}", json=plan_payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["total_hours"], 16)
        self.assertEqual(self.client.delete(f"/course_plans/{plan.json()['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/subjects/{subject.json()['id']}").status_code, 200)

    def test_academic_year_create_update_and_activate(self):
        self.fixture(with_plan=False)
        created = self.client.post("/academic-years/", json={
            "name": "2027-2028", "start_date": "2027-09-01", "end_date": "2028-08-31", "is_active": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        updated = self.client.put(f"/academic-years/{created.json()['id']}", json={
            "name": "2027-2028 обновлён", "start_date": "2027-09-02", "end_date": "2028-08-31", "is_active": False,
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        activated = self.client.post(f"/academic-years/{created.json()['id']}/activate")
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertTrue(activated.json()["is_active"])
        self.assertEqual(self.db.query(models.AcademicYear).filter_by(is_active=True).count(), 1)

    def test_group_create_update_schedule_settings_and_saturday_toggle(self):
        self.fixture(with_plan=False)
        created = self.client.post("/groups/", json={
            "number": 499, "course": 4, "weekly_hours": 24, "semester_weeks": 18, "has_saturday": False,
        })
        self.assertEqual(created.status_code, 200, created.text)
        group_id = created.json()["id"]
        updated = self.client.put(f"/groups/{group_id}", json={
            "number": 499, "course": 4, "weekly_hours": 28, "semester_weeks": 18, "has_saturday": False,
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        settings = self.client.post("/groups/schedule-settings/", json=[{
            "group_id": group_id, "weekly_hours": 30, "has_saturday": True,
        }])
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertTrue(settings.json()[0]["has_saturday"])
        toggled = self.client.post(f"/groups/{group_id}/toggle-saturday")
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertFalse(toggled.json()["has_saturday"])

    def test_teacher_vacation_create_delete_and_teacher_update(self):
        item = self.fixture(with_plan=False)
        updated = self.client.put(f"/teachers/{item.teacher.id}", json={
            "name": "Преподаватель 2", "room_id": item.room.id, "working_days": "1,3,5",
            "max_hours_per_week": 18, "is_active": True, "on_vacation": False, "is_sick": False,
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["working_days"], "1,3,5")
        vacation = self.client.post("/teacher-vacations/", json={
            "teacher_id": item.teacher.id, "academic_year_id": item.year.id,
            "start_date": "2026-09-01", "end_date": "2026-09-02",
        })
        self.assertEqual(vacation.status_code, 200, vacation.text)
        listed = self.client.get("/teacher-vacations/", params={"academic_year_id": item.year.id})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(self.client.delete(f"/teacher-vacations/{vacation.json()['id']}").status_code, 200)

    def test_archive_week_toggle_for_group_and_all_groups(self):
        item = self.fixture(with_plan=False)
        first = self.client.post("/archived-weeks/toggle", json={
            "group_id": item.group.id, "week_number": 1, "academic_year_id": item.year.id,
        })
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["is_archived"])
        all_groups = self.client.post("/archived-weeks/toggle-all", json={
            "week_number": 1, "academic_year_id": item.year.id,
        })
        self.assertEqual(all_groups.status_code, 200, all_groups.text)
        status = self.client.get("/archived-weeks/status", params={
            "group_id": item.group.id, "week_number": 1, "academic_year_id": item.year.id,
        })
        self.assertEqual(status.status_code, 200, status.text)
        self.assertIn("is_archived", status.json())

    def test_owner_can_manage_users_but_not_delete_self(self):
        owner = models.User(login="owner", password_hash="unused", role="owner", is_active=True)
        self.db.add(owner)
        self.db.commit()
        self.client.cookies.set("schedule_session", main.make_session(owner.id))
        created = self.client.post("/users/", json={"login": "operator", "password": "operator-pass", "role": "user"})
        self.assertEqual(created.status_code, 200, created.text)
        changed = self.client.put(f"/users/{created.json()['id']}", json={"role": "admin", "is_active": True})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["role"], "admin")
        self.assertEqual(self.client.delete(f"/users/{created.json()['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/users/{owner.id}").status_code, 400)

    def test_schedule_entry_create_update_delete_roundtrip(self):
        item = self.fixture()
        payload = {
            "week_number": 1, "day_of_week": 2, "time_slot": 1, "room_name": "101",
            "teacher_id": item.teacher.id, "teacher2_id": None, "room2_name": None,
            "group_id": item.group.id, "subject_name": "Математика", "term_id": item.term.id,
            "academic_year_id": item.year.id,
        }
        created = self.client.post("/schedule/", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["schedule_date"], "2026-09-01")
        payload["time_slot"] = 2
        updated = self.client.put(f"/schedule/{created.json()['id']}", json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["time_slot"], 2)
        self.assertEqual(self.client.delete(f"/schedule/{created.json()['id']}").status_code, 200)
        self.assertEqual(self.db.query(models.ScheduleEntry).count(), 0)

    def test_schedule_rejects_group_teacher_and_room_conflicts(self):
        item = self.fixture()
        payload = {
            "week_number": 1, "day_of_week": 2, "time_slot": 1, "room_name": "101",
            "teacher_id": item.teacher.id, "group_id": item.group.id, "subject_name": "Математика",
            "term_id": item.term.id, "academic_year_id": item.year.id,
        }
        self.assertEqual(self.client.post("/schedule/", json=payload).status_code, 200)
        self.assertEqual(self.client.post("/schedule/", json=payload).status_code, 409)
        other_group = models.Group(number=346, course=1)
        self.db.add(other_group)
        self.db.flush()
        other_term = models.GroupTerm(
            academic_year_id=item.year.id, group_id=other_group.id, term_number=1,
            name="1 семестр", start_week=1, weeks=1,
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 7), is_active=True,
        )
        self.db.add(other_term)
        self.db.commit()
        payload.update(group_id=other_group.id, term_id=other_term.id)
        self.assertEqual(self.client.post("/schedule/", json=payload).status_code, 409)

    def test_group_term_lock_blocks_edit_and_delete(self):
        item = self.fixture(with_plan=False)
        locked = self.client.post(f"/group-terms/{item.term.id}/lock")
        self.assertEqual(locked.status_code, 200, locked.text)
        payload = {
            "group_id": item.group.id, "academic_year_id": item.year.id, "term_number": 1,
            "name": "1 семестр", "start_date": "2026-09-01", "end_date": "2026-09-07",
        }
        self.assertEqual(self.client.put(f"/group-terms/{item.term.id}", json=payload).status_code, 409)
        self.assertEqual(self.client.delete(f"/group-terms/{item.term.id}").status_code, 409)

    def test_group_break_day_create_delete_and_common_day_exclusion(self):
        item = self.fixture()
        common = self.client.post("/academic-days-off/", json={
            "academic_year_id": item.year.id, "day_date": "2026-09-02", "title": "Общий выходной",
        })
        self.assertEqual(common.status_code, 200, common.text)
        single = self.client.post("/group-break-days/", json={
            "academic_year_id": item.year.id, "group_id": item.group.id,
            "day_date": "2026-09-03", "title": "Практика",
        })
        self.assertEqual(single.status_code, 200, single.text)
        self.assertEqual(self.client.delete(f"/group-break-days/{single.json()['id']}").status_code, 200)
        ranged = self.client.post("/group-break-days/range/", json={
            "academic_year_id": item.year.id, "group_id": item.group.id,
            "start_date": "2026-09-01", "end_date": "2026-09-03", "title": "Практика",
        })
        self.assertEqual(ranged.status_code, 200, ranged.text)
        self.assertEqual({entry["day_date"] for entry in ranged.json()}, {"2026-09-01", "2026-09-03"})

    def test_algorithm_rule_and_curator_hour_crud(self):
        item = self.fixture()
        rule = self.client.post("/algorithm-rules/", json={
            "subject_name": "Математика", "group_id": item.group.id, "course": None,
            "term_number": 1, "weekly_hours": 2, "lesson_mode": "lessons",
        })
        self.assertEqual(rule.status_code, 200, rule.text)
        self.assertEqual(self.client.delete(f"/algorithm-rules/{rule.json()['id']}").status_code, 200)
        hour = self.client.post("/curator-hours/", json={
            "group_id": item.group.id, "day_of_week": 4, "time_slot": 6,
            "duration": 1, "teacher_id": item.teacher.id, "room_name": "101",
        })
        self.assertEqual(hour.status_code, 200, hour.text)
        updated = self.client.put(f"/curator-hours/{hour.json()['id']}", json={
            "group_id": item.group.id, "day_of_week": 4, "time_slot": 7,
            "duration": 1, "teacher_id": item.teacher.id, "room_name": "101",
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.client.delete(f"/curator-hours/{hour.json()['id']}").status_code, 200)

    def test_algorithm_calendar_has_one_scope_switchable_table(self):
        project = Path(__file__).resolve().parents[1]
        markup = (project / "frontend/html/algorithm_settings.html").read_text(encoding="utf-8")
        script = (project / "frontend/js/algorithm-settings.js").read_text(encoding="utf-8")
        self.assertEqual(markup.count("calendar-management-card"), 1)
        self.assertIn('<option value="all">Все группы</option>', markup)
        self.assertNotIn("breakCalendarDays", markup)
        self.assertIn("selectCalendarScope", script)
        self.assertIn("toggleCalendarDay", script)
        self.assertNotIn("breakCalendarMonth", script)

    def test_pydantic_rejects_out_of_range_schedule_coordinates(self):
        base = dict(week_number=1, day_of_week=1, time_slot=1, room_name="101", group_id=1, subject_name="Math")
        for field, value in (("week_number", 0), ("week_number", 54), ("day_of_week", 0), ("day_of_week", 7), ("time_slot", 0), ("time_slot", 13)):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                schemas.ScheduleEntryCreate(**(base | {field: value}))

    def test_calendar_week_is_monday_based_and_year_boundary_safe(self):
        self.assertEqual(main.calendar_week_for_date(date(2026, 9, 1), date(2026, 9, 1)), 1)
        self.assertEqual(main.calendar_week_for_date(date(2026, 9, 1), date(2026, 9, 7)), 2)
        self.assertEqual(main.calendar_week_for_date(date(2026, 9, 1), date(2026, 9, 8)), 2)

    def test_calendar_date_for_slot_maps_monday_first_and_week_number(self):
        year = SimpleNamespace(start_date=date(2026, 9, 1))
        self.assertEqual(solver.calendar_date_for_slot(year, 1, 0), date(2026, 8, 31))
        self.assertEqual(solver.calendar_date_for_slot(year, 1, 5), date(2026, 9, 5))
        self.assertEqual(solver.calendar_date_for_slot(year, 2, 0), date(2026, 9, 7))

    def test_teacher_day_and_vacation_parsing_ignores_invalid_values(self):
        teacher = SimpleNamespace(working_days="1, 3, 9, bad", vacation_weeks="2, 0, -1, nope")
        self.assertEqual(solver.get_teacher_working_days(teacher), {1, 3})
        self.assertEqual(solver.get_teacher_vacation_weeks(teacher), {2})
        self.assertEqual(solver.get_teacher_working_days(SimpleNamespace(working_days="")), {1, 2, 3, 4, 5})

    def test_parallel_teacher_hours_count_as_maximum_not_sum(self):
        plan = SimpleNamespace(teacher_hours=10, teacher2_hours=8, total_hours=18)
        self.assertEqual(solver.planned_lessons(plan), 10)
        self.assertEqual(solver.SubjectDemand(SimpleNamespace(id=1, group_id=1, teacher_hours=10, teacher2_hours=8, total_hours=18), 3, "auto", 10).scheduled_total, 10)

    def test_course_rule_prefers_group_override_and_term_match(self):
        group = SimpleNamespace(id=3, course=1)
        plan = SimpleNamespace(subject_name="Math")
        rules = [
            SimpleNamespace(subject_name="math", is_active=True, is_required=True, term_number=None, group_id=None, course=1, weekly_hours=2),
            SimpleNamespace(subject_name=" MATH ", is_active=True, is_required=True, term_number=1, group_id=3, course=None, weekly_hours=4),
            SimpleNamespace(subject_name="Math", is_active=True, is_required=True, term_number=2, group_id=3, course=None, weekly_hours=5),
        ]
        self.assertEqual(solver.find_rule_for_plan(plan, group, rules, 1), rules[1])
        self.assertEqual(solver.find_rule_for_plan(plan, group, rules, 2), rules[2])

    def test_subject_classification_and_mode_limits(self):
        self.assertTrue(solver.is_non_classroom_plan("Преддипломная"))
        self.assertTrue(solver.is_non_classroom_plan("Практикум сварщика"))
        self.assertFalse(solver.is_non_classroom_plan("Математика"))
        self.assertTrue(solver.is_pe_subject("Физическая культура"))
        self.assertTrue(solver.is_sports_room("Спортивный зал"))
        self.assertEqual(solver.mode_limits(SimpleNamespace(mode="pairs"), 5), (2, 1))
        self.assertEqual(solver.mode_limits(SimpleNamespace(mode="lessons"), 3), (0, 3))

    def test_validate_rules_reports_missing_scope_and_duplicate_plan(self):
        self.fixture()
        duplicate = models.CoursePlan(
            subject_name="Математика", total_hours=1, group_id=1,
            term_id=1, academic_year_id=1,
        )
        self.db.add(duplicate)
        self.db.commit()
        invalid_mode = SimpleNamespace(
            is_active=True, is_required=True, lesson_mode="bad", subject_name="Математика",
            group_id=1, course=None, weekly_hours=0, term_number=None,
        )
        errors = solver.validate_rules(self.db.query(models.Group).all(), self.db.query(models.GroupTerm).all(), self.db.query(models.CoursePlan).all(), [invalid_mode])
        self.assertTrue(any("неизвестный режим" in item for item in errors))
        invalid_mode.lesson_mode = "auto"
        errors = solver.validate_rules(self.db.query(models.Group).all(), self.db.query(models.GroupTerm).all(), self.db.query(models.CoursePlan).all(), [invalid_mode])
        self.assertTrue(any("положительной" in item for item in errors))
        self.assertTrue(any("ровно один" in item for item in errors))

    def test_cp_sat_places_hours_without_group_teacher_or_room_collisions(self):
        item = self.fixture()
        success, message = solver.solve_global_week(
            self.db, 1, [item.group], [item.teacher], [item.room],
            [item.plan], rules=[], terms_by_group={item.group.id: item.term},
        )
        self.assertTrue(success, message)
        entries = self.db.query(models.ScheduleEntry).all()
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry.is_generated for entry in entries))
        self.assertTrue(all(entry.schedule_date is not None for entry in entries))
        self.assertEqual(len({(e.group_id, e.day_of_week, e.time_slot) for e in entries}), len(entries))
        self.assertEqual(len({(e.teacher_id, e.day_of_week, e.time_slot) for e in entries}), len(entries))
        self.assertEqual(len({(e.room_name, e.day_of_week, e.time_slot) for e in entries}), len(entries))

    def test_cp_sat_skips_non_classroom_practice(self):
        item = self.fixture(with_plan=False)
        plan = models.CoursePlan(subject_name="Преддипломная", total_hours=3, group_id=item.group.id, term_id=item.term.id, academic_year_id=item.year.id)
        self.db.add(plan)
        self.db.commit()
        success, _ = solver.solve_global_week(self.db, 1, [item.group], [item.teacher], [item.room], [plan], terms_by_group={item.group.id: item.term})
        self.assertTrue(success)
        self.assertEqual(self.db.query(models.ScheduleEntry).count(), 0)

    def test_cp_sat_respects_teacher_working_days(self):
        item = self.fixture()
        item.teacher.working_days = "3"
        self.db.commit()
        success, message = solver.solve_global_week(self.db, 1, [item.group], [item.teacher], [item.room], [item.plan], terms_by_group={item.group.id: item.term})
        self.assertTrue(success, message)
        self.assertEqual({entry.day_of_week for entry in self.db.query(models.ScheduleEntry)}, {3})

    def test_cp_sat_respects_weekend_and_academic_day_off(self):
        item = self.fixture()
        item.year.start_date = date(2026, 9, 1)
        self.db.add(models.AcademicDayOff(academic_year_id=item.year.id, day_date=date(2026, 9, 2), title="Holiday"))
        self.db.commit()
        success, message = solver.solve_global_week(self.db, 1, [item.group], [item.teacher], [item.room], [item.plan], terms_by_group={item.group.id: item.term})
        self.assertTrue(success, message)
        self.assertNotIn(date(2026, 9, 2), {entry.schedule_date for entry in self.db.query(models.ScheduleEntry)})
        self.assertNotIn(6, {entry.day_of_week for entry in self.db.query(models.ScheduleEntry)})

    def test_cp_sat_respects_teacher_week_vacation_and_room_assignment(self):
        item = self.fixture()
        item.teacher.vacation_weeks = "1"
        self.db.commit()
        success, message = solver.solve_global_week(self.db, 1, [item.group], [item.teacher], [item.room], [item.plan], terms_by_group={item.group.id: item.term})
        self.assertTrue(success, message)
        self.assertEqual(self.db.query(models.ScheduleEntry).count(), 0)

    def test_build_demands_excludes_archived_groups_and_wrong_terms(self):
        item = self.fixture()
        demands = solver.build_subject_demands(self.db, [item.group], [item.plan], [], 1, {item.group.id: item.term})
        self.assertEqual(len(demands), 1)
        self.assertEqual(solver.build_subject_demands(self.db, [item.group], [item.plan], [], 1, {item.group.id: item.term}, archived={item.group.id}), [])
        other_term = SimpleNamespace(id=item.term.id + 1, term_number=2)
        self.assertEqual(solver.build_subject_demands(self.db, [item.group], [item.plan], [], 1, {item.group.id: other_term}), [])

    def test_generate_endpoint_rejects_invalid_term_and_generates_schedule(self):
        item = self.fixture()
        invalid = self.client.post("/generate_schedule/?term_number=3")
        self.assertEqual(invalid.status_code, 400)
        response = self.client.post("/generate_schedule/?term_number=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.db.query(models.ScheduleEntry).filter_by(term_id=item.term.id).count(), 2)

    def test_academic_day_off_is_idempotent_and_removes_only_date_entries(self):
        item = self.fixture()
        on_date = models.ScheduleEntry(week_number=1, day_of_week=2, time_slot=1, room_name="101", group_id=item.group.id, subject_name="Math", academic_year_id=item.year.id, term_id=item.term.id, schedule_date=date(2026, 9, 2))
        elsewhere = models.ScheduleEntry(week_number=1, day_of_week=3, time_slot=1, room_name="101", group_id=item.group.id, subject_name="Math", academic_year_id=item.year.id, term_id=item.term.id, schedule_date=date(2026, 9, 3))
        self.db.add_all([on_date, elsewhere]); self.db.commit()
        payload = {"academic_year_id": item.year.id, "day_date": "2026-09-02", "title": "Выходной"}
        created = self.client.post("/academic-days-off/", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.headers["x-removed-schedule-entries"], "1")
        repeated = self.client.post("/academic-days-off/", json=payload)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.db.query(models.AcademicDayOff).count(), 1)
        self.assertEqual(self.db.query(models.ScheduleEntry).count(), 1)

    def test_group_break_range_skips_sunday_and_non_saturday_group(self):
        item = self.fixture()
        response = self.client.post("/group-break-days/range/", json={
            "academic_year_id": item.year.id, "group_id": item.group.id,
            "start_date": "2026-09-04", "end_date": "2026-09-06", "title": "Практика",
        })
        self.assertEqual(response.status_code, 200, response.text)
        dates = {row[0].isoformat() for row in self.db.query(models.GroupBreakDay.day_date).all()}
        self.assertEqual(dates, {"2026-09-04"})
        self.assertEqual(response.headers["x-added-group-break-days"], "1")

    def test_available_options_excludes_occupied_teacher_and_room(self):
        item = self.fixture()
        other_group = models.Group(number=346, course=1)
        self.db.add(other_group); self.db.flush()
        self.db.add(models.ScheduleEntry(
            week_number=1, day_of_week=2, time_slot=1, room_name="101",
            teacher_id=item.teacher.id, group_id=other_group.id, subject_name="Other",
            academic_year_id=item.year.id, term_id=item.term.id,
            schedule_date=date(2026, 9, 1),
        )); self.db.commit()
        response = self.client.get("/schedule/available-options", params={
            "group_id": item.group.id, "term_id": item.term.id,
            "week_number": 1, "day_of_week": 2, "time_slot": 1,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["plans"], [])
        self.assertNotIn(item.teacher.id, response.json()["teachers"])
        self.assertNotIn("101", response.json()["rooms"])

    def test_pdf_shows_legacy_canceled_entries_after_data_migration(self):
        item = self.fixture()
        scheduled = models.ScheduleEntry(
            week_number=1, day_of_week=2, time_slot=1, room_name="101",
            teacher_id=item.teacher.id, group_id=item.group.id, subject_name="Математика",
            academic_year_id=item.year.id, term_id=item.term.id, schedule_date=date(2026, 9, 1),
        )
        legacy_canceled = models.ScheduleEntry(
            week_number=1, day_of_week=2, time_slot=2, room_name="101",
            teacher_id=item.teacher.id, group_id=item.group.id, subject_name="Старое отменённое занятие",
            academic_year_id=item.year.id, term_id=item.term.id, schedule_date=date(2026, 9, 1),
        )
        self.db.add_all([scheduled, legacy_canceled]); self.db.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE schedule_entries ADD COLUMN status VARCHAR DEFAULT 'planned'")
            connection.exec_driver_sql("UPDATE schedule_entries SET status='canceled' WHERE id=?", (legacy_canceled.id,))
        migrations.upgrade(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("SELECT status FROM schedule_entries WHERE id=?", (legacy_canceled.id,)).scalar_one(), "planned")
        pdf = main.build_schedule_pdf(self.db, 1, group_number=345, academic_year_id=item.year.id)
        pages = PdfReader(io.BytesIO(pdf.getvalue())).pages
        extracted = "\n".join(page.extract_text() or "" for page in pages)
        self.assertIn("Математика", extracted)
        self.assertIn("Старое отменённое занятие", extracted)

    def test_schedule_api_has_no_cancel_or_restore_routes(self):
        routes = {route.path for route in main.app.routes if hasattr(route, "path")}
        self.assertNotIn("/schedule/{entry_id}/cancel", routes)
        self.assertNotIn("/schedule/{entry_id}/restore", routes)

    def test_pdf_endpoint_requires_session_and_returns_pdf(self):
        item = self.fixture()
        self.client.cookies.clear()
        denied = self.client.get("/export/schedule.pdf", params={"week_number": 1, "academic_year_id": item.year.id})
        self.assertEqual(denied.status_code, 401)
        self.client.cookies.set("schedule_session", main.make_session(1))
        allowed = self.client.get("/export/schedule.pdf", params={"week_number": 1, "academic_year_id": item.year.id})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["content-type"], "application/pdf")


if __name__ == "__main__":
    unittest.main()
