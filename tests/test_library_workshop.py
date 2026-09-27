import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.methods import EditMessageText
from test_telegram import RecordingSession

from fitness_bot import ui
from fitness_bot.bot import Controller
from fitness_bot.db import Database
from fitness_bot.service import Service, UserError


@pytest.fixture
def service(tmp_path):
    s = Service(Database(tmp_path / "db"), 100)
    s.register(100, "Автор", "fitness_author")
    s.register(200, "Читатель", "reader")
    return s


def test_remove_seed_persists_across_registration_and_restart(service):
    service.remove_program(200, 1)
    service.register(200, "Читатель", "reader")
    reboot = Service(service.db, 100)
    reboot.register(200, "Читатель", "reader")
    assert reboot.programs(200) == []
    assert len(reboot.programs(100)) == 1
    reboot.register(300, "Новый")
    assert len(reboot.programs(300)) == 1


def test_remove_preserves_active_history_and_publication(service):
    service.publish_program(100, 1)
    sid = service.start(100, 1, 0)
    service.log_set(100, sid, 0, 25, 10)
    service.remove_program(100, 1)
    assert service.active(100)["id"] == sid
    service.finish(100, sid, 1)
    assert service.history(100)[0]["set_count"] == 1
    assert service.workshop(200)[0]["id"] == 1
    assert service.workshop(100, own=True)[0]["id"] == 1
    service.publish_program(100, 1, False)
    assert not service.workshop(200)
    assert service.program(200, 1)["id"] == 1


def test_rename_is_personal_and_does_not_change_published_name(service):
    service.rename_program(200, 1, "Мой план")
    assert service.program(200, 1)["name"] == "Мой план"
    assert service.programs(200)[0]["name"] == "Мой план"
    assert service.program(100, 1)["name"] == "Программа тренировок"
    service.publish_program(100, 1)
    assert service.workshop(200)[0]["name"] == "Программа тренировок"
    assert service.program(200, 1)["author_username"] == "fitness_author"
    for name in ("", "  ", "a" * 81, "два\nимени"):
        with pytest.raises(UserError):
            service.rename_program(200, 1, name)


def test_private_links_are_not_public_and_workshop_access_is_checked(service):
    pid = service.create_program(100, "Тест\nДень | Жим | chest | 2 | 12 | 20 | 90")
    service.share_program(100, pid)
    assert service.workshop(200) == []
    with pytest.raises(UserError):
        service.workshop_program(200, pid)
    with pytest.raises(UserError):
        service.add_from_workshop(200, pid)
    with pytest.raises(UserError):
        service.publish_program(200, pid)
    with pytest.raises(UserError):
        service.rename_program(200, pid, "Украдено")
    service.remove_program(200, pid)
    assert service.program(100, pid)["name"] == "Тест"
    service.publish_program(100, pid)
    service.add_from_workshop(200, pid)
    service.add_from_workshop(200, pid)
    assert len(service.programs(200)) == 2
    assert service.workshop_program(200, pid)["added"]
    service.publish_program(100, pid, False)
    with pytest.raises(UserError):
        service.add_from_workshop(200, pid)
    assert service.program(200, pid)["id"] == pid


def test_author_mention_refreshes_and_escapes_fallback(service):
    assert "@fitness_author" in ui.program_card(service.program(200, 1))
    service.register(100, "Имя", "new_author")
    assert "@new_author" in ui.program_card(service.program(200, 1))
    service.register(100, "<b>Имя</b>", None)
    card = ui.program_card(service.program(200, 1))
    assert "tg://user?id=100" in card and "&lt;b&gt;" in card
    assert "@new_author" not in card
    copy = service.copy_program(200, 1)
    service.publish_program(200, copy)
    public = service.workshop_program(100, copy)
    assert public["author_id"] == 100 and public["publisher_username"] == "reader"


def test_v1_migration_keeps_data_and_is_repeatable(tmp_path):
    path = tmp_path / "v1.sqlite3"
    schema = Path(__file__).resolve().parents[1] / "fitness_bot/schema.sql"
    with sqlite3.connect(path) as c:
        c.executescript(schema.read_text("utf-8"))
        c.execute(
            "INSERT INTO users(id,name,timezone,created,checkin_due,friend_token,xp) VALUES(100,'Owner','Europe/Berlin',1,2,'token',777)"
        )
        c.execute(
            "INSERT INTO programs(id,name,author_id,author_name,owner_id,document,share_token) VALUES(5,'Old',100,'Owner',100,'{}','oldlink')"
        )
        c.execute("INSERT INTO library VALUES(100,5)")
    for _ in range(2):
        db = Database(path)
    with db.connect() as c:
        assert c.execute("SELECT xp,username FROM users WHERE id=100").fetchone()[:] == (777, None)
        assert c.execute("SELECT program_id,alias FROM library").fetchone()[:] == (5, None)
        assert c.execute("SELECT published,share_token FROM programs WHERE id=5").fetchone()[:] == (
            0,
            "oldlink",
        )
        assert c.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 2


def test_workshop_pagination_and_compact_days(service):
    for i in range(10):
        pid = service.copy_program(100, 1)
        service.publish_program(100, pid)
    assert len(service.workshop(200)) == 9  # Eight visible plus lookahead.
    assert len(service.workshop(200, 8)) == 2
    assert service.workshop(100) == []
    p = service.program(200, 1)
    overview = ui.program_card(p)
    assert "Дней: 3" in overview and len(overview) < 400
    first = ui.day_card(p, 0)
    second = ui.day_card(p, 0, 4)
    assert "1–4 из 8" in first and "5–8 из 8" in second
    assert "Жим лёжа" in first and "Сгибание на бицепс" in second


def test_new_folders_management_and_workshop_transport(service):
    async def scenario():
        transport = RecordingSession()
        bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", session=transport)
        controller = Controller(service, "fitness_test_bot")
        token = controller.screen.set(88)
        for folder in ui.FOLDERS:
            await controller.callback_action(bot, 100, "folder:" + folder)
        for section in ("summary", "records", "measurements", "achievements"):
            await controller.callback_action(bot, 100, "progress:" + section)
        for section in ("friends", "challenges", "rooms"):
            await controller.callback_action(bot, 100, "social:" + section)
        await controller.callback_action(bot, 100, "confirmpublish:1")
        assert not service.workshop(200)
        await controller.callback_action(bot, 100, "publish:1")
        await controller.callback_action(bot, 200, "workshop:0:0")
        assert "@fitness_author" in transport.sent[-1].text
        await controller.callback_action(bot, 200, "public:1")
        await controller.callback_action(bot, 200, "day:1:0:0:1")
        assert "1–4 из 8" in transport.sent[-1].text
        await controller.callback_action(bot, 200, "manage:1")
        await controller.callback_action(bot, 200, "rename:1")
        prompt = json.loads(service.profile(200)["prompt"])
        await controller.prompt_reply(bot, 200, "Моя база", prompt)
        assert service.program(200, 1)["name"] == "Моя база"
        await controller.callback_action(bot, 200, "confirmremove:1")
        assert service.programs(200)
        await controller.callback_action(bot, 200, "remove:1")
        assert not service.programs(200)
        await controller.callback_action(bot, 200, "addpublic:1")
        assert service.programs(200)
        assert any(isinstance(request, EditMessageText) for request in transport.sent)
        controller.screen.reset(token)
        await bot.session.close()

    asyncio.run(scenario())
