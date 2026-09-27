import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fitness_bot.db import Database
from fitness_bot.service import Service, UserError, next_checkin


@pytest.fixture
def service(tmp_path):
    now = [1788771600.0]
    service = Service(Database(tmp_path / "fitness.sqlite3"), 100, clock=lambda: now[0])
    service.now = now
    service.register(100, "Автор")
    service.register(200, "Друг")
    return service


def tiny(s, uid=100, count=2):
    return s.create_program(uid, f"Тест\nДень | Жим | chest | {count} | 12 | 20 | 90")


def complete(s, pid, uid=100, weight=20, reps=12):
    sid = s.start(uid, pid, 0)
    while (a := s.active(uid))["cursor"] < len(a["plan"]):
        s.log_set(uid, sid, a["revision"], weight, reps)
    s.finish(uid, sid, s.active(uid)["revision"])
    s.now[0] += 86400
    return sid


def test_seed_and_author_are_not_first_visitor(tmp_path):
    s = Service(Database(tmp_path / "db"), 999)
    s.register(123, "Не автор")
    p = s.programs(123)[0]
    assert p["author_id"] == 999
    s.register(999, "Настоящий автор")
    assert s.programs(123)[0]["author_name"] == "Настоящий автор"
    doc = json.loads(p["document"])
    assert [len(d["exercises"]) for d in doc["days"]] == [8, 7, 8]
    assert doc["days"][0]["exercises"][0]["sets"][0]["weight"] == 25
    assert doc["days"][0]["exercises"][1]["sets"][0]["weight"] is None
    assert doc["days"][1]["exercises"][0]["sets"][0]["reps"] is None
    Service(s.db, 999)
    assert len(s.programs(123)) == 1


def test_seed_exactly_matches_excel():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts/import_excel.py"
    spec = importlib.util.spec_from_file_location("import_excel", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.extract() == json.loads(
        (path.parents[1] / "fitness_bot/data/starter.json").read_text("utf-8")
    )


def test_restart_duplicate_buttons_and_single_active(service):
    pid = tiny(service)
    sid = service.start(100, pid, 0)
    assert service.start(100, pid, 0) == sid

    def press():
        try:
            service.log_set(100, sid, 0, 20, 12)
            return True
        except UserError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: press(), range(2))) == [False, True]
    reboot = Service(service.db, 100, clock=service.clock)
    assert len(reboot.active(100)["sets"]) == 1
    assert reboot.active(100)["rest_due"] == service.clock() + 90
    reboot.log_set(100, sid, 1, 20, 12)
    assert reboot.active(100)["cursor"] == 1
    assert reboot.finish(100, sid, 2) == 60
    with pytest.raises(UserError):
        reboot.finish(100, sid, 2)
    assert reboot.profile(100)["xp"] == 60


def test_ownership_and_sharing(service):
    pid = tiny(service)
    sid = service.start(100, pid, 0)
    for fn in [
        lambda: service.program(200, pid),
        lambda: service.start(200, pid, 0),
        lambda: service.session_detail(200, sid),
        lambda: service.log_set(200, sid, 0, 20, 10),
    ]:
        with pytest.raises(UserError):
            fn()
    token = service.share_program(100, pid)
    service.redeem(200, "p_" + token)
    assert service.program(200, pid)["author_id"] == 100
    with pytest.raises(UserError):
        service.edit_program(200, pid, "X\nD | E | core | 1 | 10 | 0 | 0")
    copied = service.copy_program(200, pid)
    assert service.program(200, copied)["author_id"] == 100
    service.edit_program(200, copied, "X\nD | E | core | 1 | 10 | 0 | 0")
    service.share_program(100, pid, revoke=True)
    with pytest.raises(UserError):
        service.redeem(200, "p_" + token)


def test_snapshot_undo_warmup_abandon(service):
    pid = tiny(service)
    sid = service.start(100, pid, 0)
    service.edit_program(100, pid, "Изменено\nДень | Пресс | core | 1 | 10 | 0 | 0")
    assert service.active(100)["plan"][0]["name"] == "Жим"
    service.log_set(100, sid, 0, 10, 15, kind="warmup")
    assert service.active(100)["cursor"] == 0
    service.log_set(100, sid, 1, 20, 12)
    service.log_set(100, sid, 2, 20, 12)
    assert service.active(100)["cursor"] == 1
    service.mutate_session(100, sid, 3, "undo")
    assert service.active(100)["cursor"] == 0
    assert len(service.active(100)["sets"]) == 2
    service.finish(100, sid, 4, abandon=True)
    assert service.progress(100)["prs"] == []
    assert service.profile(100)["xp"] == 0


@pytest.mark.parametrize("weight,reps", [("NaN", 10), ("inf", 10), (-1, 10), (10, 0), (10, 1.5), (10, 1001)])
def test_invalid_sets(service, weight, reps):
    sid = service.start(100, tiny(service), 0)
    with pytest.raises(UserError):
        service.log_set(100, sid, 0, weight, reps)
    assert not service.active(100)["sets"]


def test_autoprogression_requires_two_complete_results_and_opt_in(service):
    pid = tiny(service)
    complete(service, pid)
    e = json.loads(service.program(100, pid)["document"])["days"][0]["exercises"][0]
    assert service.suggestion(100, e, 2, 0)[0] == 20
    service.save_settings(100, "Жим", 90, 12, 2.5, 1)
    assert service.suggestion(100, e, 2, 0)[0] == 20
    complete(service, pid)
    assert service.suggestion(100, e, 2, 0)[0] == 22.5
    complete(service, pid, reps=10)
    assert service.suggestion(100, e, 2, 0)[0] == 20


def test_pr_is_per_user_and_completed_only(service):
    pid = tiny(service)
    complete(service, pid)
    prs = service.progress(100)["prs"]
    assert prs[0]["weight"] == 20 and prs[0]["volume"] == 240
    assert not service.progress(200)["prs"]
    sid = service.start(100, pid, 0)
    service.log_set(100, sid, 0, 100, 20)
    assert service.progress(100)["prs"][0]["weight"] == 20


def test_goals_and_measurements(service):
    service.checkin(100, 85, 90, None, 100, 35)
    service.add_goal(100, "weight", 80, 30)
    service.add_goal(100, "workouts", 1, 30)
    assert not service.goals(100)[0]["achieved"]
    complete(service, tiny(service))
    service.checkin(100, 80)
    assert all(g["achieved"] for g in service.goals(100))
    assert service.progress(100)["checkins"][1]["chest"] is None


def test_dst_and_weekly_notification_survives_restart(service):
    now = datetime(2026, 10, 23, 12, tzinfo=UTC).timestamp()
    due = next_checkin(now, "Europe/Berlin")
    local = datetime.fromtimestamp(due, ZoneInfo("Europe/Berlin"))
    assert local.weekday() == 0 and local.hour == 10 and local.day == 26
    service.now[0] = service.profile(100)["checkin_due"] + 1
    _, weekly = Service(service.db, 100, clock=service.clock).due_notifications()
    row = next(r for r in weekly if r["id"] == 100)
    service.acknowledge_notification("weekly", row)
    assert 100 not in [r["id"] for r in service.due_notifications()[1]]


def test_rest_extension_and_old_ack_do_not_erase_new_deadline(service):
    sid = service.start(100, tiny(service), 0)
    service.log_set(100, sid, 0, 20, 12)
    service.now[0] += 91
    row = service.due_notifications()[0][0]
    service.mutate_session(100, sid, 1, "rest30")
    service.acknowledge_notification("rest", row)
    assert service.active(100)["rest_due"] > service.clock()


def test_quick_filter_replacement_and_empty_core(service):
    base = service.programs(100)[0]["id"]
    with pytest.raises(UserError):
        service.start(100, base, 0, "core")
    sid = service.start(100, base, 0, "all", True)
    a = service.active(100)
    assert len(a["plan"]) == 3 and len(a["plan"][0]["sets"]) == 2
    e = a["plan"][0]
    alt = service.alternatives(100, e["group"], e["name"])[0]
    service.mutate_session(100, sid, 0, "replace", alt["name"])
    assert service.active(100)["plan"][0]["name"] == alt["name"]


def test_social_consent_challenge_counts_and_room(service):
    friend_token = service.profile(100)["friend_token"]
    assert service.social(200)[0] == []
    service.redeem(200, "f_" + friend_token)
    assert service.social(200)[0][0]["id"] == 100
    token = service.create_challenge(100, "Месяц", 5, 30)
    service.redeem(200, "c_" + token)
    complete(service, tiny(service))
    c = service.social(200)[1][0]
    assert c["members"][0]["score"] == 1
    room = service.create_room(100)
    service.redeem(200, "r_" + room)
    service.start(200, tiny(service, 200), 0)
    assert service.active(200)["room_id"] is not None
    service.leave_room(200)
    assert service.active(200)["room_id"] is None
    service.remove_friend(100, 200)
    assert not service.social(200)[0]
