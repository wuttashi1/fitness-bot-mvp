import asyncio
import json
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from fitness_bot import ui
from fitness_bot.bot import Controller, notification_loop
from fitness_bot.db import Database
from fitness_bot.service import Service


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, (SendMessage, EditMessageText)):
            self.sent.append(method)
            assert len(method.text) <= 4096
            if isinstance(method.reply_markup, ui.InlineKeyboardMarkup):
                for row in method.reply_markup.inline_keyboard:
                    for button in row:
                        assert 1 <= len(button.callback_data.encode()) <= 64
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(type(method))

    async def stream_content(self, *args, **kwargs):
        yield b""


def test_full_telegram_flow_without_network(tmp_path):
    async def scenario():
        service = Service(Database(tmp_path / "db"), 100)
        session = RecordingSession()
        bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", session=session)
        controller = Controller(service, "fitness_test_bot")
        router = Router()
        router.message.register(controller.message)
        router.callback_query.register(controller.callback)
        dp = Dispatcher()
        dp.include_router(router)
        sequence = 0

        async def event(text=None, data=None):
            nonlocal sequence
            sequence += 1
            user = User(id=100, first_name="Автор", is_bot=False)
            message = Message(
                message_id=sequence,
                date=datetime.now(UTC),
                chat=Chat(id=100, type="private"),
                from_user=user,
                text=text or "card",
            )
            update = (
                Update(update_id=sequence, message=message)
                if data is None
                else Update(
                    update_id=sequence,
                    callback_query=CallbackQuery(
                        id=str(sequence), from_user=user, chat_instance="test", message=message, data=data
                    ),
                )
            )
            await dp.feed_update(bot, update)

        await event("/start")
        assert "стартовая" in session.sent[-1].text
        for title in [v for row in ui.MENU for v in row]:
            await event(title)
        await event(data="program:1")
        await event(data="newprogram")
        await event("Моя\nДень | Жим | chest | 2 | 12 | 20 | 90")
        pid = service.programs(100)[1]["id"]
        await event(data=f"start:{pid}:0:all:0")
        a = service.active(100)
        sid = a["id"]
        await event(data=f"s:{sid}:0:input")
        await event("20 12 2 working")
        assert len(service.active(100)["sets"]) == 1
        await event(data=f"s:{sid}:0:log:20:12")
        assert "устарела" in session.sent[-1].text
        await event(data=f"s:{sid}:1:note")
        await event("Скамья 3")
        assert service.note(100, "Жим") == "Скамья 3"
        await event(data=f"s:{sid}:1:log:20:12")
        await event(data=f"s:{sid}:2:confirmfinish")
        await event(data=f"s:{sid}:2:finish")
        assert service.active(100) is None
        await event(data="checkin")
        await event("80.5 85 100 98 35")
        await event(data="goal")
        await event("workouts 12 30")
        await event(data="challenge")
        await event("Месяц | 12 | 30")
        await event(data="room")
        await event(data="social")
        await event(data="progress")
        await event(data="goals")
        await event(data=f"history:{sid}")
        assert "20 × 12" in session.sent[-1].text
        await event(data=f"copy:{pid}")
        copy_id = next(p["id"] for p in service.programs(100) if p["source_id"] == pid)
        await event(data=f"editprogram:{copy_id}")
        await event("Новая\nДень | Пресс | core | 2 | 15 | 0 | 60")
        assert service.program(100, copy_id)["name"] == "Новая"
        await bot.session.close()

    asyncio.run(scenario())


def test_all_seed_exercises_render_and_finish(tmp_path):
    s = Service(Database(tmp_path / "db"), 100)
    s.register(100, "Автор")
    for day in range(3):
        sid = s.start(100, 1, day)
        while (a := s.active(100))["cursor"] < len(a["plan"]):
            text, markup = ui.workout_card(s, 100)
            assert len(text) <= 4096
            assert markup.inline_keyboard
            s.log_set(100, sid, a["revision"], 10, 10)
        s.finish(100, sid, s.active(100)["revision"])
    assert s.progress(100)["total"] == 3
    assert sum(m["sets"] for m in s.progress(100)["muscles"]) == 92


def test_worker_delivers_and_acknowledges_after_restart(tmp_path):
    async def scenario():
        now = [1788771600.0]
        db = Database(tmp_path / "db")
        s = Service(db, 100, clock=lambda: now[0])
        s.register(100, "Автор")
        s.start(100, 1, 0)
        s.log_set(100, 1, 0, 25, 10)
        now[0] = s.profile(100)["checkin_due"] + 1
        reboot = Service(db, 100, clock=lambda: now[0])
        transport = RecordingSession()
        bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", session=transport)
        task = asyncio.create_task(notification_loop(bot, reboot, 0.01))
        for _ in range(100):
            if len(transport.sent) >= 2 and reboot.due_notifications() == ([], []):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert len(transport.sent) == 2
        assert reboot.due_notifications() == ([], [])
        await bot.session.close()

    asyncio.run(scenario())


def test_program_edit_keeps_original_author_and_history(tmp_path):
    s = Service(Database(tmp_path / "db"), 100)
    s.register(200, "Копировщик")
    pid = s.copy_program(200, 1)
    s.start(200, pid, 0)
    before = s.active(200)["plan"]
    s.edit_program(200, pid, "Своя версия\nДень | Пресс | core | 1 | 20 | 0 | 60")
    assert s.active(200)["plan"] == before
    assert s.program(200, pid)["author_id"] == 100
    assert json.loads(s.program(200, pid)["document"])["days"][0]["exercises"][0]["name"] == "Пресс"
