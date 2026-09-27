"""Text and button rendering. HTML is deliberately disabled for user-provided content."""

import json
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .service import GROUPS, local_time

MENU = [
    ["🏋️ Тренировки", "📈 Мой прогресс"],
    ["👥 Сообщество", "👤 Профиль"],
]

FOLDERS = {
    "training": (
        "🏋️ Тренировки",
        "Выберите, чем займёмся сегодня.",
        [
            [("▶️ Начать / продолжить", "nav:train")],
            [("📁 Мои тренировки", "library:0"), ("🛍 WORK SHOP", "workshop:0:0")],
            [("⚡ Быстрая", "choose:all:1"), ("🤖 Что сегодня?", "nav:recommend")],
        ],
    ),
    "progress": (
        "📈 Мой прогресс",
        "Ваши результаты — по разделам.",
        [
            [("📊 Обзор", "progress:summary"), ("📅 История", "historypage:0")],
            [("🏅 Рекорды", "progress:records"), ("⚖️ Вес и замеры", "progress:measurements")],
            [("🎯 Цели", "goals"), ("🏆 Достижения", "progress:achievements")],
        ],
    ),
    "community": (
        "👥 Сообщество",
        "Тренируйтесь вместе и находите новые программы.",
        [
            [("🛍 WORK SHOP", "workshop:0:0")],
            [("👥 Друзья", "social:friends"), ("🏆 Челленджи", "social:challenges")],
            [("🤝 Совместные тренировки", "social:rooms")],
        ],
    ),
}


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in MENU],
        resize_keyboard=True,
        is_persistent=True,
    )


def buttons(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def chunks(text, limit=3500):
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    if text:
        yield text


def workout_card(service, uid):
    s = service.active(uid)
    if not s:
        return "Активной тренировки нет.", buttons([[("Выбрать тренировку", "choose:all:0")]])
    prefix = f"s:{s['id']}:{s['revision']}:"
    rows = []
    tz = service.profile(uid)["timezone"]
    lines = [f"🏋️ {s['name']}", f"Начало: {local_time(s['started'], tz)}"]
    if s["cursor"] < len(s["plan"]):
        e = s["plan"][s["cursor"]]
        logged = [t for t in s["sets"] if t["position"] == s["cursor"]]
        ordinal = sum(t["kind"] != "warmup" for t in logged)
        weight, reps, reason = service.suggestion(uid, e, len(e["sets"]), ordinal)
        # Subsequent sets of this session reuse the user's actual current-session input.
        work = [t for t in logged if t["kind"] != "warmup"]
        if work:
            weight, reps, reason = (
                work[-1]["weight"],
                work[-1]["reps"],
                "Повтор предыдущего подхода этой тренировки.",
            )
        lines += [
            f"\n{s['cursor'] + 1}/{len(s['plan'])} — {e['name']}",
            f"Подход {ordinal + 1}/{len(e['sets'])}",
            f"Вес: {weight if weight is not None else 'не указан'} кг · Повторы: {reps if reps is not None else 'не указаны'}",
            reason if reason.startswith("Предложено") else "",
        ]
        if weight is not None and reps is not None:
            rows.append([(f"✅ {weight:g} кг × {reps}", prefix + f"log:{weight}:{reps}")])
        rows.append([("✏️ Вес / повторы / RIR", prefix + "input")])
        previous = service.previous(uid, e["name"], 1)
        if previous:
            lines += [
                "\nВ прошлый раз · " + local_time(previous[0]["ended"], tz),
                " · ".join(f"{t['weight']:g}×{t['reps']}" for t in previous[0]["sets"])[:300],
            ]
        else:
            lines.append("Прошлых результатов пока нет.")
        if logged:
            lines += ["Сегодня: " + " / ".join(f"{t['weight']:g}×{t['reps']}" for t in logged)[-500:]]
        note = service.note(uid, e["name"])
        if note:
            lines.append("📝 " + note[:200])
    else:
        lines.append("\nВсе упражнения пройдены. Нажмите «Завершить», чтобы сохранить тренировку.")
    if s["rest_due"]:
        remaining = max(0, int(s["rest_due"] - service.clock()))
        lines.append(
            f"\n⏱ Отдых: осталось {remaining // 60}:{remaining % 60:02d}. По окончании придёт сообщение."
        )
        rows.append([("+30 сек", prefix + "rest30"), ("Пропустить отдых", prefix + "skiprest")])
    rows += [
        [("📂 Ещё", prefix + "tools"), ("🏁 Завершить", prefix + "confirmfinish")],
    ]
    return "\n".join(lines), buttons(rows)


def program_card(p):
    doc = json.loads(p["document"])
    count = sum(len(day["exercises"]) for day in doc["days"])
    lines = [
        f"📋 <b>{escape(p['name'])}</b>",
        "Автор: " + author(p),
        f"\nДней: {len(doc['days'])}  •  Упражнений: {count}",
    ]
    if p["source_id"]:
        lines.append("На основе программы автора")
    lines.append("\nВыберите день ниже, чтобы посмотреть упражнения.")
    return "\n".join(lines)


def author(p):
    username = p.get("author_username")
    if username:
        return "@" + escape(username[:32])
    return f'<a href="tg://user?id={int(p["author_id"])}">{escape(p["author_name"][:40])}</a>'


def day_card(p, day_index, offset=0):
    day = json.loads(p["document"])["days"][day_index]
    exercises = day["exercises"]
    lines = [f"<b>{escape(day['name'])}</b>", escape(p["name"]), "Автор: " + author(p)]
    for i, e in enumerate(exercises[offset : offset + 4], offset + 1):
        reps = sorted({v["reps"] for v in e["sets"] if v["reps"] is not None})
        rep_text = (str(reps[0]) if len(reps) == 1 else f"{reps[0]}–{reps[-1]}") if reps else "на выбор"
        lines += [
            f"\n<b>{i}. {escape(e['name'])}</b>",
            f"{GROUPS[e['group']]}  ·  {len(e['sets'])} подх. × {rep_text} повт.",
        ]
        weights = [v["weight"] for v in e["sets"] if v["weight"] is not None]
        if weights:
            lines.append("Вес в программе: " + " / ".join(f"{v:g}" for v in dict.fromkeys(weights)) + " кг")
    lines.append(f"\n{offset + 1}–{min(offset + 4, len(exercises))} из {len(exercises)} упражнений")
    return "\n".join(lines)
