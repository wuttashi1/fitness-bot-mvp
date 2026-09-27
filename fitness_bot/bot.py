import asyncio
import json
import logging
from collections import defaultdict
from contextvars import ContextVar
from html import escape

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import BotCommand, CallbackQuery, Message

from . import ui
from .service import GROUPS, KINDS, UserError, local_time

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, service, username):
        self.service, self.username = service, username
        self.locks = defaultdict(asyncio.Lock)
        self.screen = ContextVar("screen", default=None)

    async def call(self, method, *args, **kwargs):
        return await asyncio.to_thread(getattr(self.service, method), *args, **kwargs)

    async def send(self, bot, uid, text, markup=None, html=False):
        parts = list(ui.chunks(text))
        if self.screen.get() and len(parts) == 1 and not isinstance(markup, ui.ReplyKeyboardMarkup):
            try:
                await bot.edit_message_text(
                    text,
                    chat_id=uid,
                    message_id=self.screen.get(),
                    reply_markup=markup,
                    parse_mode="HTML" if html else None,
                )
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    return
        for i, part in enumerate(parts):
            await bot.send_message(
                uid,
                part,
                reply_markup=markup if i == len(parts) - 1 else None,
                parse_mode="HTML" if html else None,
            )

    async def folder(self, bot, uid, folder):
        await self.call("set_prompt", uid)
        title, subtitle, rows = ui.FOLDERS[folder]
        await self.send(bot, uid, title + "\n\n" + subtitle, ui.buttons(rows))

    async def card(self, bot, uid):
        text, markup = await asyncio.to_thread(ui.workout_card, self.service, uid)
        await self.send(bot, uid, text, markup)

    def link(self, kind, token):
        return f"https://t.me/{self.username}?start={kind}_{token}"

    async def ask(self, bot, uid, action, text, **data):
        await self.call("set_prompt", uid, action, **data)
        await self.send(bot, uid, text + "\n\n/cancel — отменить ввод", ui.buttons([[("Отмена", "cancel")]]))

    async def choose(self, bot, uid, group="all", quick=0, offset=0):
        if await self.call("active", uid):
            return await self.card(bot, uid)
        rows = []
        for p in await self.call("programs", uid):
            for i, d in enumerate(json.loads(p["document"])["days"]):
                if group == "all" or any(e["group"] == group for e in d["exercises"]):
                    rows.append(
                        [(f"{p['name'][:25]} · {d['name'][:30]}", f"start:{p['id']}:{i}:{group}:{quick}")]
                    )
        text = (
            "Выберите день. Быстрая: первые 3 упражнения, до 2 подходов."
            if quick
            else "Выберите день программы."
        )
        if not rows:
            text = "В ваших программах нет упражнений этой группы. Добавьте свою программу."
        offset = max(0, offset)
        page = rows[offset : offset + 8]
        nav = []
        if offset:
            nav.append(("←", f"choose:{group}:{quick}:{max(0, offset - 8)}"))
        if len(rows) > offset + 8:
            nav.append(("→", f"choose:{group}:{quick}:{offset + 8}"))
        if nav:
            page.append(nav)
        page.append([("‹ Тренировки", "folder:training")])
        await self.send(bot, uid, text, ui.buttons(page))

    async def show_programs(self, bot, uid, offset=0):
        programs = await self.call("programs", uid)
        offset = max(0, offset)
        rows = [[(p["name"][:55], f"program:{p['id']}")] for p in programs[offset : offset + 8]]
        nav = []
        if offset:
            nav.append(("←", f"library:{max(0, offset - 8)}"))
        if len(programs) > offset + 8:
            nav.append(("→", f"library:{offset + 8}"))
        if nav:
            rows.append(nav)
        rows += [
            [("➕ Создать", "newprogram"), ("🛍 WORK SHOP", "workshop:0:0")],
            [("‹ Тренировки", "folder:training")],
        ]
        text = (
            f"📁 Мои тренировки\n\nПрограмм: {len(programs)}\nВыберите программу."
            if programs
            else "📁 Мои тренировки\n\nПока пусто. Создайте программу или добавьте из WORK SHOP."
        )
        await self.send(bot, uid, text, ui.buttons(rows))

    async def show_workshop(self, bot, uid, offset=0, own=False):
        programs = await self.call("workshop", uid, offset, own)
        lines = ["🛍 WORK SHOP" + (" · Мои публикации" if own else ""), ""]
        rows = []
        for p in programs[:8]:
            lines += [f"<b>{escape(p['name'][:40])}</b>", "Автор: " + ui.author(p)]
            if p["owner_id"] != p["author_id"]:
                publisher = (
                    "@" + p["publisher_username"] if p["publisher_username"] else "пользователь Telegram"
                )
                lines.append("Опубликовал: " + escape(publisher))
            lines.append("")
            rows.append([(("✓ " if p["added"] else "") + p["name"][:50], f"public:{p['id']}")])
        if not programs:
            lines.append("Публикаций пока нет.")
        nav = []
        if offset:
            nav.append(("←", f"workshop:{max(0, offset - 8)}:{int(own)}"))
        if len(programs) > 8:
            nav.append(("→", f"workshop:{offset + 8}:{int(own)}"))
        if nav:
            rows.append(nav)
        rows += [
            [("Все программы" if own else "Мои публикации", f"workshop:0:{int(not own)}")],
            [("📁 Мои тренировки", "library:0"), ("‹ Сообщество", "folder:community")],
        ]
        await self.send(bot, uid, "\n".join(lines), ui.buttons(rows), html=True)

    async def show_program(self, bot, uid, pid, public=False):
        p = await self.call("workshop_program" if public else "program", uid, pid)
        rows = [
            [(d["name"][:55], f"day:{pid}:{i}:0:{int(public)}")]
            for i, d in enumerate(json.loads(p["document"])["days"])
        ]
        if public:
            rows.append(
                [
                    (
                        "✓ Открыть в моих" if p["added"] else "＋ В мои тренировки",
                        f"program:{pid}" if p["added"] else f"addpublic:{pid}",
                    )
                ]
            )
            if p["owner_id"] == uid:
                rows.append([("Убрать из WORK SHOP", f"unpublish:{pid}")])
            rows.append([("‹ WORK SHOP", f"workshop:0:{int(p['owner_id'] == uid)}")])
        else:
            rows += [[("📂 Управление", f"manage:{pid}")], [("‹ Мои тренировки", "library:0")]]
        await self.send(bot, uid, ui.program_card(p), ui.buttons(rows), html=True)

    async def show_day(self, bot, uid, pid, day, offset=0, public=False):
        p = await self.call("workshop_program" if public else "program", uid, pid)
        days = json.loads(p["document"])["days"]
        if not 0 <= day < len(days) or not 0 <= offset < len(days[day]["exercises"]):
            raise UserError("День не найден. Откройте программу заново.")
        rows, nav = [], []
        if offset:
            nav.append(("← Упражнения", f"day:{pid}:{day}:{max(0, offset - 4)}:{int(public)}"))
        if len(days[day]["exercises"]) > offset + 4:
            nav.append(("Упражнения →", f"day:{pid}:{day}:{offset + 4}:{int(public)}"))
        if nav:
            rows.append(nav)
        if not public or p["added"]:
            rows.append([("▶️ Начать", f"start:{pid}:{day}:all:0"), ("⚡ Быстро", f"start:{pid}:{day}:all:1")])
        else:
            rows.append([("＋ В мои тренировки", f"addpublic:{pid}")])
        rows.append([("‹ Программа", f"{'public' if public else 'program'}:{pid}")])
        await self.send(bot, uid, ui.day_card(p, day, offset), ui.buttons(rows), html=True)

    async def show_history(self, bot, uid, offset=0):
        history = await self.call("history", uid, offset)
        tz = (await self.call("profile", uid))["timezone"]
        rows = [
            [
                (
                    f"{local_time(s['started'], tz)} · {s['name'][:25]} · {s['set_count']} подх.",
                    f"history:{s['id']}",
                )
            ]
            for s in history
        ]
        nav = []
        if offset:
            nav.append(("← Новее", f"historypage:{max(0, offset - 10)}"))
        if len(history) == 10:
            nav.append(("Старее →", f"historypage:{offset + 10}"))
        if nav:
            rows.append(nav)
        await self.send(
            bot,
            uid,
            "История · выберите дату для подробностей" if history else "Тренировок пока нет.",
            ui.buttons(rows) if rows else ui.menu(),
        )

    async def show_progress(self, bot, uid, section="summary"):
        p = await self.call("progress", uid)
        profile = await self.call("profile", uid)
        tz = profile["timezone"]
        rows = []
        if section == "summary":
            lines = [
                "📊 Обзор",
                f"\nВсего тренировок: {p['total']}",
                f"За 7 дней: {p['week']}  •  За 30 дней: {p['month']}",
                f"Средняя длительность: {p['duration'] / 60:.0f} мин",
                "\nНагрузка за 7 дней",
            ]
            lines += [
                f"{GROUPS[m['muscle']]} · {m['sets']} подх. · {m['volume']:g} кг×повт." for m in p["muscles"]
            ]
        elif section == "records":
            lines = ["🏅 Личные рекорды", "Вес / повторы / объём — независимые рекорды."]
            lines += [
                f"\n{r['exercise']}\n{r['weight']:g} кг  ·  {r['reps']} повт.  ·  {r['volume']:g} кг×повт."
                for r in p["prs"]
            ]
            if not p["prs"]:
                lines.append("\nЗавершите тренировку, чтобы появился первый рекорд.")
        elif section == "measurements":
            lines = ["⚖️ Вес и замеры", "Талия / грудь / бёдра / рука — в см."]
            for c in p["checkins"]:
                values = " / ".join(
                    str(c[k]) if c[k] is not None else "—" for k in ("waist", "chest", "hips", "arm")
                )
                lines.append(f"\n{local_time(c['created'], tz)} · {c['weight']:g} кг\n{values}")
            if len(p["checkins"]) > 1:
                delta = p["checkins"][0]["weight"] - p["checkins"][-1]["weight"]
                lines.append(f"\nИзменение между показанными записями: {delta:+.1f} кг")
            if not p["checkins"]:
                lines.append("\nНачните с первого замера.")
            rows.append([("＋ Записать вес и замеры", "checkin")])
        elif section == "achievements":
            lines = [
                "🏆 Достижения",
                f"\nУровень {1 + profile['xp'] // 500}  ·  {profile['xp']} XP",
                f"До следующего уровня: {500 - profile['xp'] % 500} XP",
                "",
            ]
            lines += ["✓ " + a for a in p["achievements"]] or ["Первая тренировка откроет первое достижение."]
        else:
            raise UserError("Раздел не найден.")
        rows.append([("‹ Мой прогресс", "folder:progress")])
        await self.send(bot, uid, "\n".join(lines), ui.buttons(rows))

    async def show_goals(self, bot, uid):
        goals = await self.call("goals", uid)
        tz = (await self.call("profile", uid))["timezone"]
        lines = ["🎯 Цели"]
        for g in goals:
            label = "Вес, кг" if g["kind"] == "weight" else "Тренировки"
            state = (
                "✅"
                if g["achieved"]
                else ("Срок истёк" if g["deadline"] < self.service.clock() else "В процессе")
            )
            lines.append(
                f"{label}: {g['value']:g} / {g['target']:g} · до {local_time(g['deadline'], tz)} · {state}"
            )
        await self.send(bot, uid, "\n".join(lines), ui.buttons([[("➕ Цель", "goal")]]))

    async def show_social(self, bot, uid, section="friends"):
        friends, challenges, rooms = await self.call("social", uid)
        rows = []
        if section == "friends":
            lines = ["👥 Друзья", ""]
            lines += [f"{f['name']} · уровень {1 + f['xp'] // 500}" for f in friends] or [
                "Пригласите первого друга."
            ]
            lines += [
                "\nСсылка-приглашение:",
                self.link("f", (await self.call("profile", uid))["friend_token"]),
                "\nДрузьям видны имя и уровень. Вес и замеры приватны.",
            ]
            rows += [[("Убрать: " + f["name"][:30], f"unfriend:{f['id']}")] for f in friends]
        elif section == "challenges":
            lines = ["🏆 Челленджи", ""]
            for c in challenges:
                lines += [
                    c["name"] + (" · завершён" if c["ends"] <= self.service.clock() else ""),
                    *[f"{m['name']}: {m['score']}/{c['target']} тренировок" for m in c["members"]],
                    self.link("c", c["token"]),
                    "",
                ]
            if not challenges:
                lines.append("Создайте челлендж и пригласите друзей.")
            rows = [[("＋ Создать челлендж", "challenge")]]
            rows += [[("Выйти: " + c["name"][:30], f"leavechallenge:{c['id']}")] for c in challenges]
        elif section == "rooms":
            lines = ["🤝 Совместные тренировки", "Комнаты действуют 24 часа."]
            status = {"active": "тренируется", "completed": "завершил", "abandoned": "отменил"}
            for r in rooms:
                lines += [
                    "\nУчастники",
                    *[f"{m['name']} · {status.get(m['status'], m['status'])}" for m in r["members"]],
                ]
            rows = [[("＋ Создать комнату", "room")], [("Обновить статусы", "social:rooms")]]
            if rooms:
                rows.append([("Выйти из комнат", "leaveroom")])
        else:
            raise UserError("Раздел не найден.")
        rows.append([("‹ Сообщество", "folder:community")])
        await self.send(bot, uid, "\n".join(lines), ui.buttons(rows))

    async def prompt_reply(self, bot, uid, text, prompt):
        action = prompt["action"]
        fields = text.split()
        if action == "set":
            if not 2 <= len(fields) <= 4:
                raise UserError(
                    "Формат: вес повторы [RIR или -] [working/warmup/drop/amrap]. Например: 25 10 2 working"
                )
            await self.call(
                "log_set",
                uid,
                prompt["sid"],
                prompt["revision"],
                fields[0],
                fields[1],
                None if len(fields) < 3 or fields[2] == "-" else fields[2],
                fields[3] if len(fields) > 3 else "working",
            )
        elif action == "note":
            await self.call("save_note", uid, prompt["exercise"], text)
        elif action == "config":
            if len(fields) != 4:
                raise UserError("Формат: отдых_сек верх_повторов шаг_кг автопрогрессия_0_или_1")
            await self.call("save_settings", uid, prompt["exercise"], *fields)
        elif action == "checkin":
            if len(fields) not in (1, 5):
                raise UserError("Введите только вес или 5 значений: вес талия грудь бёдра рука. Пропуск: -")
            await self.call("checkin", uid, *fields)
        elif action == "goal":
            if len(fields) != 3:
                raise UserError("Формат: workouts 12 30 или weight 80 60")
            await self.call("add_goal", uid, *fields)
        elif action == "timezone":
            await self.call("set_timezone", uid, text.strip())
        elif action == "newprogram":
            await self.call("create_program", uid, text)
        elif action == "editprogram":
            await self.call("edit_program", uid, prompt["pid"], text)
        elif action == "renameprogram":
            await self.call("rename_program", uid, prompt["pid"], text)
        elif action == "challenge":
            pieces = [p.strip() for p in text.split("|")]
            if len(pieces) != 3:
                raise UserError("Формат: Название | число тренировок | число дней")
            token = await self.call("create_challenge", uid, *pieces)
            await self.send(bot, uid, "Приглашение в челлендж:\n" + self.link("c", token))
        await self.call("set_prompt", uid)
        await self.send(bot, uid, "Сохранено.", ui.menu())
        if action in ("set", "note", "config"):
            await self.card(bot, uid)
        elif action in ("newprogram", "editprogram", "renameprogram"):
            await self.show_programs(bot, uid)
        elif action == "goal":
            await self.show_goals(bot, uid)

    async def message(self, message: Message):
        if message.chat.type != "private" or not message.from_user:
            return
        uid, text, bot = message.from_user.id, message.text or "", message.bot
        async with self.locks[uid]:
            await self.call("register", uid, message.from_user.full_name, message.from_user.username)
            try:
                if text.split(" ")[0].split("@")[0] == "/id":
                    return await self.send(bot, uid, f"Ваш Telegram ID: {uid}")
                if text.startswith("/start"):
                    await self.call("set_prompt", uid)
                    payload = text.partition(" ")[2]
                    if payload:
                        # Explicit acceptance, so merely opening a link does not join anything.
                        return await self.send(
                            bot,
                            uid,
                            "Принять приглашение? Для друзей станут видны имя и уровень; "
                            "для челленджа — число тренировок; для комнаты — статус тренировки.",
                            ui.buttons([[("Принять", "accept:" + payload)], [("Отмена", "cancel")]]),
                        )
                    await self.send(
                        bot,
                        uid,
                        "Добро пожаловать!\n\n🏋️ Тренировки — ваша коллекция и WORK SHOP\n"
                        "📈 Мой прогресс — результаты, история и цели\n"
                        "👥 Сообщество — друзья и совместные тренировки\n\n"
                        "При первом входе стартовая программа добавляется в «Мои тренировки».\n/help — справка",
                        ui.menu(),
                    )
                    return
                if text == "/cancel":
                    await self.call("set_prompt", uid)
                    return await self.send(bot, uid, "Ввод отменён. Тренировка сохранена.", ui.menu())
                if text in ("/help", "/privacy"):
                    return await self.send(bot, uid, HELP, ui.menu())
                if text in [v for row in ui.MENU for v in row]:
                    await self.call("set_prompt", uid)
                    return await self.menu_action(bot, uid, text)
                if text == "/checkin":
                    return await self.callback_action(bot, uid, "checkin")
                if text == "/resume":
                    return await self.card(bot, uid)
                profile = await self.call("profile", uid)
                if profile["prompt"]:
                    return await self.prompt_reply(bot, uid, text, json.loads(profile["prompt"]))
                await self.send(bot, uid, "Выберите действие в меню. /help — справка.", ui.menu())
            except UserError as error:
                await self.send(bot, uid, str(error))

    async def menu_action(self, bot, uid, text):
        folders = {"🏋️ Тренировки": "training", "📈 Мой прогресс": "progress", "👥 Сообщество": "community"}
        if text in folders:
            return await self.folder(bot, uid, folders[text])
        if text == "🏋️ Тренировка":
            if await self.call("active", uid):
                return await self.card(bot, uid)
            return await self.send(
                bot,
                uid,
                "Что тренируем?",
                ui.buttons([[(label, f"choose:{key}:0")] for key, label in GROUPS.items()]),
            )
        if text == "⚡ Быстрая":
            return await self.choose(bot, uid, quick=1)
        if text == "📚 Программы":
            return await self.show_programs(bot, uid)
        if text == "📅 История":
            return await self.show_history(bot, uid)
        if text == "📊 Прогресс":
            return await self.show_progress(bot, uid)
        if text == "🎯 Цели":
            return await self.show_goals(bot, uid)
        if text == "👥 Друзья":
            return await self.show_social(bot, uid)
        if text == "🤖 Что сегодня?":
            options = await self.call("recommend", uid)
            lines = [
                "Дни отсортированы по давности нагрузки и числу групп, тренировавшихся за последние 48 часов.",
                "Это подсказка по журналу, а не оценка восстановления. Учитывайте своё самочувствие.",
            ]
            rows = []
            for o in options:
                lines.append(
                    o["name"]
                    + (
                        " · Недавняя нагрузка: " + ", ".join(o["recent"])
                        if o["recent"]
                        else " · Нет записей нагрузки за 48 ч"
                    )
                )
                rows.append([(o["name"], f"start:{o['pid']}:{o['day']}:all:0")])
            return await self.send(bot, uid, "\n".join(lines), ui.buttons(rows))
        profile = await self.call("profile", uid)
        if text == "👤 Профиль":
            return await self.send(
                bot,
                uid,
                f"👤 {profile['name']}\nID: {uid}\nУровень: {1 + profile['xp'] // 500}"
                f" · XP: {profile['xp']}\nДо следующего уровня: {500 - profile['xp'] % 500} XP\nЧасовой пояс: {profile['timezone']}",
                ui.buttons(
                    [
                        [("⚖️ Вес и замеры", "checkin")],
                        [("🎯 Цели", "goals")],
                        [("📈 Мой прогресс", "folder:progress")],
                        [("⚙️ Настройки", "nav:settings")],
                    ]
                ),
            )
        if text == "⚙️ Настройки":
            return await self.send(
                bot,
                uid,
                f"Часовой пояс: {profile['timezone']}\nWeekly check-in: "
                f"{'включён' if profile['reminders'] else 'выключен'}\nСледующий: "
                + local_time(profile["checkin_due"], profile["timezone"]),
                ui.buttons(
                    [[("Изменить часовой пояс", "timezone")], [("Переключить weekly check-in", "reminders")]]
                ),
            )

    async def callback(self, query: CallbackQuery):
        if not isinstance(query.message, Message) or query.message.chat.type != "private":
            return
        uid = query.from_user.id
        if query.message.chat.id != uid:
            return
        await query.answer()
        async with self.locks[uid]:
            await self.call("register", uid, query.from_user.full_name, query.from_user.username)
            screen_token = self.screen.set(query.message.message_id)
            try:
                await self.callback_action(query.bot, uid, query.data or "")
            except (UserError, IndexError, ValueError) as error:
                await self.send(
                    query.bot,
                    uid,
                    str(error) if isinstance(error, UserError) else "Кнопка недействительна. Откройте меню.",
                )
            finally:
                self.screen.reset(screen_token)

    async def callback_action(self, bot, uid, data):
        a = data.split(":")
        await self.call("set_prompt", uid)
        if a[0] == "folder":
            return await self.folder(bot, uid, a[1])
        if a[0] == "nav":
            destination = {"train": "🏋️ Тренировка", "recommend": "🤖 Что сегодня?", "settings": "⚙️ Настройки"}
            return await self.menu_action(bot, uid, destination[a[1]])
        if a[0] == "library":
            return await self.show_programs(bot, uid, int(a[1]))
        if a[0] == "workshop":
            return await self.show_workshop(bot, uid, max(0, int(a[1])), bool(int(a[2])))
        if a[0] == "public":
            return await self.show_program(bot, uid, int(a[1]), public=True)
        if a[0] == "day":
            return await self.show_day(bot, uid, int(a[1]), int(a[2]), int(a[3]), bool(int(a[4])))
        if a[0] == "addpublic":
            await self.call("add_from_workshop", uid, int(a[1]))
            return await self.show_program(bot, uid, int(a[1]))
        if a[0] == "manage":
            p = await self.call("program", uid, int(a[1]))
            pid = p["id"]
            rows = [[("✏️ Переименовать у себя", f"rename:{pid}")], [("📑 Личная копия", f"copy:{pid}")]]
            if p["owner_id"] == uid:
                if not p["is_base"]:
                    rows.append([("📝 Изменить упражнения", f"editprogram:{pid}")])
                rows += [
                    [("🔗 Ссылка", f"share:{pid}"), ("Отозвать ссылку", f"revoke:{pid}")],
                    [
                        (
                            "Убрать из WORK SHOP" if p["published"] else "🛍 Опубликовать в WORK SHOP",
                            f"unpublish:{pid}" if p["published"] else f"confirmpublish:{pid}",
                        )
                    ],
                ]
            rows += [[("🗑 Удалить у себя", f"confirmremove:{pid}")], [("‹ Программа", f"program:{pid}")]]
            return await self.send(bot, uid, "📂 Управление\n\n" + p["name"], ui.buttons(rows))
        if a[0] == "rename":
            p = await self.call("program", uid, int(a[1]))
            return await self.ask(
                bot,
                uid,
                "renameprogram",
                "Новое название для «"
                + p["name"]
                + "»\n\nОт 1 до 80 символов. Название изменится только у вас.",
                pid=p["id"],
            )
        if a[0] == "confirmremove":
            p = await self.call("program", uid, int(a[1]))
            return await self.send(
                bot,
                uid,
                "Удалить «" + p["name"] + "» из моих тренировок?\n\n"
                "История и текущая сессия сохранятся. Публикация в WORK SHOP и коллекции других пользователей останутся.",
                ui.buttons(
                    [[("🗑 Удалить у себя", f"remove:{p['id']}")], [("Оставить", f"program:{p['id']}")]]
                ),
            )
        if a[0] == "remove":
            await self.call("remove_program", uid, int(a[1]))
            return await self.show_programs(bot, uid)
        if a[0] == "confirmpublish":
            p = await self.call("program", uid, int(a[1]))
            if p["owner_id"] != uid:
                raise UserError("Сначала создайте личную копию.")
            return await self.send(
                bot,
                uid,
                "Опубликовать в WORK SHOP?\n\nНазвание публикации: "
                + p["original_name"]
                + "\n\nПользователям будут видны упражнения, веса и повторы шаблона, "
                "а также автор. Ваше личное название, история и замеры не публикуются.",
                ui.buttons([[("Опубликовать", f"publish:{p['id']}")], [("‹ Назад", f"manage:{p['id']}")]]),
            )
        if a[0] in ("publish", "unpublish"):
            await self.call("publish_program", uid, int(a[1]), a[0] == "publish")
            return await self.show_workshop(bot, uid, own=True)
        if a[0] == "cancel":
            await self.call("set_prompt", uid)
            return await self.send(bot, uid, "Ввод отменён.", ui.menu())
        if a[0] == "accept":
            return await self.send(bot, uid, await self.call("redeem", uid, a[1]), ui.menu())
        if a[0] == "choose":
            return await self.choose(bot, uid, a[1], int(a[2]), int(a[3]) if len(a) > 3 else 0)
        if a[0] == "start":
            await self.call("set_prompt", uid)
            await self.call("start", uid, int(a[1]), int(a[2]), a[3], bool(int(a[4])))
            return await self.card(bot, uid)
        if a[0] == "program":
            return await self.show_program(bot, uid, int(a[1]))
        if a[0] == "copy":
            await self.call("copy_program", uid, int(a[1]))
            return await self.show_programs(bot, uid)
        if a[0] in ("share", "revoke"):
            token = await self.call("share_program", uid, int(a[1]), a[0] == "revoke")
            return await self.send(
                bot,
                uid,
                "Ссылка: " + self.link("p", token)
                if token
                else "Ссылка отозвана. Уже добавленные программы остаются у получателей.",
            )
        if a[0] == "newprogram":
            return await self.ask(bot, uid, "newprogram", CONSTRUCTOR)
        if a[0] == "editprogram":
            p = await self.call("program", uid, int(a[1]))
            if p["owner_id"] != uid:
                raise UserError("Редактировать можно только свою программу.")
            if p["is_base"]:
                raise UserError("Для редактирования исходной Excel-программы создайте личную копию.")
            return await self.ask(
                bot,
                uid,
                "editprogram",
                "Пришлите новую версию всей программы. История тренировок сохранится.\n" + CONSTRUCTOR,
                pid=p["id"],
            )
        if a[0] == "historypage":
            return await self.show_history(bot, uid, int(a[1]))
        if a[0] == "history":
            s, sets = await self.call("session_detail", uid, int(a[1]))
            tz = (await self.call("profile", uid))["timezone"]
            text = f"{s['name']} · {s['status']}\n{local_time(s['started'], tz)}"
            if s["ended"]:
                text += f" — {local_time(s['ended'], tz)} · {(s['ended'] - s['started']) / 60:.0f} мин"
            text += "\n\n" + "\n".join(
                f"{t['exercise']}: {t['weight']:g} × {t['reps']} · {KINDS[t['kind']]} · RIR {t['rir'] if t['rir'] is not None else '—'}"
                for t in sets
            )
            return await self.send(bot, uid, text)
        if a[0] == "checkin":
            return await self.ask(
                bot,
                uid,
                "checkin",
                "Вес в кг; замеры в см: вес талия грудь бёдра рука\nПример: 80.5 85 100 98 35\nМожно только вес: 80.5. Пропуск замера: -",
            )
        if a[0] == "goal":
            return await self.ask(
                bot,
                uid,
                "goal",
                "Цель тренировок: workouts 12 30 (12 за следующие 30 дней)\nЦель веса: weight 80 60 (80 кг за 60 дней)",
            )
        if a[0] == "goals":
            return await self.show_goals(bot, uid)
        if a[0] == "progress":
            return await self.show_progress(bot, uid, a[1] if len(a) > 1 else "summary")
        if a[0] == "timezone":
            return await self.ask(
                bot, uid, "timezone", "Введите часовой пояс, например Europe/Berlin или Europe/Moscow"
            )
        if a[0] == "reminders":
            await self.call("toggle_reminders", uid)
            return await self.menu_action(bot, uid, "⚙️ Настройки")
        if a[0] == "social":
            return await self.show_social(bot, uid, a[1] if len(a) > 1 else "friends")
        if a[0] == "challenge":
            return await self.ask(
                bot, uid, "challenge", "Название | количество тренировок | дней\nПример: Сентябрь | 12 | 30"
            )
        if a[0] == "room":
            token = await self.call("create_room", uid)
            return await self.send(
                bot,
                uid,
                "Комната действует 24 часа. Участники запускают свои тренировки и видят статусы через «Друзья → Обновить».\n"
                + self.link("r", token),
            )
        if a[0] == "leaveroom":
            await self.call("leave_room", uid)
            return await self.show_social(bot, uid, "rooms")
        if a[0] == "leavechallenge":
            await self.call("leave_challenge", uid, int(a[1]))
            return await self.show_social(bot, uid, "challenges")
        if a[0] == "unfriend":
            await self.call("remove_friend", uid, int(a[1]))
            return await self.show_social(bot, uid)
        if a[0] == "s":
            sid, revision, action = int(a[1]), int(a[2]), a[3]
            s = await self.call("active", uid)
            if not s or s["id"] != sid or s["revision"] != revision:
                raise UserError("Кнопка устарела. Нажмите «Тренировка» для актуальных кнопок.")
            prefix = f"s:{sid}:{revision}:"
            if action == "tools":
                rows = []
                if s["cursor"] < len(s["plan"]):
                    rows += [
                        [("📝 Заметка", prefix + "note"), ("🔁 Замена", prefix + "replace")],
                        [("⚙️ Отдых и прогрессия", prefix + "config")],
                        [("⏭ Пропустить упражнение", prefix + "skip")],
                    ]
                rows += [
                    [("↩️ Отменить последний подход", prefix + "undo")],
                    [("Отменить тренировку", prefix + "confirmabandon")],
                    [("‹ К подходу", prefix + "resume")],
                ]
                return await self.send(bot, uid, "📂 Действия тренировки", ui.buttons(rows))
            if action in ("confirmfinish", "confirmabandon"):
                finish = action == "confirmfinish"
                return await self.send(
                    bot,
                    uid,
                    "Сохранить выполненные подходы и завершить?"
                    if finish
                    else "Отменить тренировку? Она останется в истории как отменённая, без XP и PR.",
                    ui.buttons(
                        [
                            [("Подтвердить", prefix + ("finish" if finish else "abandon"))],
                            [("Продолжить", prefix + "resume")],
                        ]
                    ),
                )
            if action in ("finish", "abandon"):
                xp = await self.call("finish", uid, sid, revision, action == "abandon")
                await self.call("set_prompt", uid)
                return await self.send(
                    bot,
                    uid,
                    f"Тренировка {'сохранена' if action == 'finish' else 'отменена'}. +{xp} XP",
                    ui.menu(),
                )
            if action == "log":
                await self.call("log_set", uid, sid, revision, a[4], a[5])
            elif action == "input":
                return await self.ask(
                    bot,
                    uid,
                    "set",
                    "Введите вес и повторы: 25 10\nОпционально RIR (0–10) и тип: 25 10 2 working\nТипы: working / warmup / drop / amrap. RIR — сколько повторений осталось в запасе.\n0 кг — без внешнего отягощения.",
                    sid=sid,
                    revision=revision,
                )
            elif action in ("note", "config", "replace", "swap"):
                if s["cursor"] >= len(s["plan"]):
                    raise UserError("Нет текущего упражнения.")
                e = s["plan"][s["cursor"]]
                if action == "note":
                    return await self.ask(
                        bot, uid, "note", "Введите заметку к «" + e["name"] + "»", exercise=e["name"]
                    )
                if action == "config":
                    return await self.ask(
                        bot,
                        uid,
                        "config",
                        "Отдых_сек верх_повторов шаг_кг автопрогрессия_0_или_1\nПример: 90 12 2.5 1\nУкажите подходящий шаг для выбранной системы веса. Предложение увеличения появляется после двух успешных тренировок.",
                        exercise=e["name"],
                    )
                alternatives = await self.call("alternatives", uid, e["group"], e["name"])
                if action == "replace":
                    return await self.send(
                        bot,
                        uid,
                        "Выберите замену из этой группы:"
                        if alternatives
                        else "В этой группе нет других упражнений.",
                        ui.buttons(
                            [
                                [(other["name"][:60], prefix + f"swap:{i}")]
                                for i, other in enumerate(alternatives)
                            ]
                        )
                        if alternatives
                        else None,
                    )
                await self.call(
                    "mutate_session", uid, sid, revision, "replace", alternatives[int(a[4])]["name"]
                )
            elif action != "resume":
                await self.call("mutate_session", uid, sid, revision, action)
            return await self.card(bot, uid)


CONSTRUCTOR = """Название программы
День | Упражнение | группа | подходы | повторы | вес или - | отдых в сек

Пример (скопируйте и измените):
Домашняя программа
День 1 | Скручивания | core | 3 | 12 | 0 | 60
День 1 | Отжимания | chest | 3 | 10 | 0 | 90

Группы: chest, back, shoulders, arms, legs, core. До 60 упражнений.
Редактирование заменяет шаблон целиком; завершённые и активные сессии сохраняют свою версию."""

HELP = """🏋️ Тренировки
Мои тренировки → программа → день → начать.
Управление → переименовать или удалить у себя, создать копию, редактировать.
WORK SHOP → опубликованные программы → добавить в мои.
Публикация доступна владельцу через «Управление». Старые ссылки не публикуются автоматически.

📈 Мой прогресс
Обзор, история, рекорды, вес и замеры, цели, достижения.

👥 Сообщество
Друзья, челленджи и комнаты совместных тренировок.

👤 Профиль
Уровень и XP. Настройки → часовой пояс и weekly check-in.

В тренировке: ✅ записать подход; ✏️ изменить вес/повторы/RIR.
Ещё → заметки, замена, отдых и прогрессия, отмена подхода.
Разминка записывается вручную и не занимает рабочий подход.
Таймер отправляет сообщение; звук зависит от настроек Telegram.

Удаление из коллекции сохраняет историю, текущую сессию и публикацию.
Переименование меняет название только у вас. Авторство сохраняется.
@username автора обновляется при его обращении к боту; без username показывается ссылка на профиль.

/resume — продолжить · /checkin — замеры · /cancel — отменить ввод · /id — Telegram ID

Данные хранятся у владельца бота. Друзья видят имя и уровень;
участники челленджа — число тренировок; комнаты — статус.
WORK SHOP и ссылки содержат шаблон программы и авторство, без личных результатов и замеров.
Экспорт и удаление персональных данных — через владельца бота.
Рекомендации используют историю, не измеряют восстановление организма."""


async def notification_loop(bot, service, interval):
    while True:
        try:
            rest, weekly = await asyncio.to_thread(service.due_notifications)
            for kind, rows in (("rest", rest), ("weekly", weekly)):
                for row in rows:
                    uid = row["user_id"] if kind == "rest" else row["id"]
                    try:
                        # Recheck after async work: a rest may have been skipped/extended.
                        if kind == "rest":
                            active = await asyncio.to_thread(service.active, uid)
                            if not active or active["rest_due"] != row["rest_due"]:
                                continue
                        text = (
                            "⏱ Отдых закончился. /resume — продолжить."
                            if kind == "rest"
                            else "Еженедельный check-in: запишем вес и замеры?"
                        )
                        await bot.send_message(
                            uid,
                            text,
                            reply_markup=ui.buttons([[("Записать", "checkin")]])
                            if kind == "weekly"
                            else None,
                        )
                        await asyncio.to_thread(service.acknowledge_notification, kind, row)
                    except TelegramForbiddenError:
                        await asyncio.to_thread(service.disable_notifications, uid)
                    except TelegramRetryAfter as error:
                        await asyncio.sleep(min(error.retry_after, 60))
                    except (TelegramNetworkError, TelegramBadRequest):
                        logger.warning("Notification delivery failed; retrying later (kind=%s)", kind)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification worker failed; retrying")
        await asyncio.sleep(interval)


async def run(bot: Bot, service, interval=5):
    identity = await bot.get_me()
    controller = Controller(service, identity.username)
    router = Router()
    router.message.register(controller.message)
    router.callback_query.register(controller.callback)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            BotCommand(command=c, description=d)
            for c, d in [
                ("start", "Главное меню"),
                ("resume", "Продолжить тренировку"),
                ("checkin", "Вес и замеры"),
                ("cancel", "Отменить ввод"),
                ("help", "Справка"),
                ("id", "Мой Telegram ID"),
            ]
        ]
    )
    worker = asyncio.create_task(notification_loop(bot, service, interval))
    try:
        await dispatcher.start_polling(bot, handle_as_tasks=False, close_bot_session=False)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
