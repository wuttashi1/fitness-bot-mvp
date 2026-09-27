"""Transactional application service. No Telegram objects or in-memory workout state."""

import json
import math
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

GROUPS = {
    "all": "Вся программа",
    "chest": "Грудь",
    "back": "Спина",
    "shoulders": "Плечи",
    "arms": "Руки",
    "legs": "Ноги",
    "core": "Корпус",
}
KINDS = {"working": "рабочий", "warmup": "разминка", "drop": "дроп-сет", "amrap": "AMRAP"}


class UserError(ValueError):
    pass


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def numeric(value, low, high):
    try:
        result = float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        raise UserError("Введите число.") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise UserError(f"Число должно быть от {low} до {high}.")
    return result


def integer(value, low, high):
    result = numeric(value, low, high)
    if not result.is_integer():
        raise UserError("Нужно целое число.")
    return int(result)


def next_checkin(now, timezone, hour=10):
    local = datetime.fromtimestamp(now, ZoneInfo(timezone))
    target = (local + timedelta(days=(7 - local.weekday()) % 7)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    if target.timestamp() <= now:
        target += timedelta(days=7)
    return target.timestamp()


def local_time(timestamp, timezone):
    return datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).strftime("%d.%m.%Y %H:%M")


class Service:
    def __init__(self, db, owner_id, owner_name="", timezone="Europe/Berlin", hour=10, clock=time.time):
        self.db, self.owner_id, self.owner_name = db, owner_id, owner_name
        self.timezone, self.hour, self.clock = timezone, hour, clock
        ZoneInfo(timezone)
        seed = json.loads(Path(__file__).with_name("data").joinpath("starter.json").read_text("utf-8"))
        with db.transaction() as c:
            base = c.execute("SELECT * FROM programs WHERE is_base=1").fetchone()
            if base and base["author_id"] != owner_id:
                raise ValueError(
                    "OWNER_TELEGRAM_ID differs from existing seed owner; use an explicit migration."
                )
            if not base:
                c.execute(
                    "INSERT INTO programs(name,author_id,author_name,owner_id,is_base,document) "
                    "VALUES(?,?,?,?,1,?)",
                    (
                        seed["name"],
                        owner_id,
                        owner_name or f"Автор · Telegram ID {owner_id}",
                        owner_id,
                        dumps(seed),
                    ),
                )

    def register(self, uid, name, username=None):
        now = self.clock()
        with self.db.transaction() as c:
            new_user = not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
            c.execute(
                "INSERT INTO users(id,name,timezone,created,checkin_due,friend_token) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (
                    uid,
                    name[:100],
                    self.timezone,
                    now,
                    next_checkin(now, self.timezone, self.hour),
                    secrets.token_urlsafe(16),
                ),
            )
            c.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
            if new_user:
                c.execute(
                    "INSERT OR IGNORE INTO library(user_id,program_id) SELECT ?,id FROM programs WHERE is_base=1",
                    (uid,),
                )
            if uid == self.owner_id:
                c.execute(
                    "UPDATE programs SET author_name=? WHERE author_id=?",
                    (self.owner_name or name[:100], uid),
                )

    def profile(self, uid):
        with self.db.connect() as c:
            return dict(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

    def programs(self, uid):
        with self.db.connect() as c:
            return [
                {**dict(r), "name": r["alias"] or r["name"]}
                for r in c.execute(
                    "SELECT p.*,u.username AS author_username, "
                    "l.alias FROM programs p JOIN library l ON p.id=l.program_id "
                    "LEFT JOIN users u ON u.id=p.author_id "
                    "WHERE l.user_id=? ORDER BY p.is_base DESC,p.id DESC",
                    (uid,),
                )
            ]

    def _program(self, c, uid, pid):
        p = c.execute(
            "SELECT p.*,p.name AS original_name,l.alias,u.username AS author_username FROM programs p "
            "JOIN library l ON l.program_id=p.id LEFT JOIN users u ON u.id=p.author_id "
            "WHERE p.id=? AND l.user_id=?",
            (pid, uid),
        ).fetchone()
        if not p:
            raise UserError("Программа недоступна.")
        return p

    def program(self, uid, pid):
        with self.db.connect() as c:
            p = dict(self._program(c, uid, pid))
            p["name"] = p["alias"] or p["name"]
            return p

    def rename_program(self, uid, pid, name):
        name = name.strip()
        if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            raise UserError("Название: от 1 до 80 символов, в одну строку.")
        with self.db.transaction() as c:
            self._program(c, uid, pid)
            c.execute("UPDATE library SET alias=? WHERE user_id=? AND program_id=?", (name, uid, pid))

    def remove_program(self, uid, pid):
        with self.db.transaction() as c:
            # Only the library entry is removed; published templates and session snapshots survive.
            c.execute("DELETE FROM library WHERE user_id=? AND program_id=?", (uid, pid))
            c.execute("UPDATE users SET prompt=NULL WHERE id=?", (uid,))

    def publish_program(self, uid, pid, published=True):
        with self.db.transaction() as c:
            p = c.execute("SELECT * FROM programs WHERE id=? AND owner_id=?", (pid, uid)).fetchone()
            if not p:
                raise UserError("Управлять публикацией может только владелец программы.")
            if published:
                self._program(c, uid, pid)
            c.execute("UPDATE programs SET published=? WHERE id=?", (int(published), pid))

    def workshop(self, uid, offset=0, own=False):
        with self.db.connect() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT p.*,u.username AS author_username,v.username AS publisher_username, "
                    "EXISTS(SELECT 1 FROM library l WHERE l.user_id=? AND l.program_id=p.id) AS added "
                    "FROM programs p LEFT JOIN users u ON u.id=p.author_id LEFT JOIN users v ON v.id=p.owner_id "
                    "WHERE p.published=1 AND p.owner_id "
                    + ("=" if own else "!=")
                    + " ? ORDER BY p.id DESC LIMIT 9 OFFSET ?",
                    (uid, uid, max(0, offset)),
                )
            ]

    def workshop_program(self, uid, pid):
        with self.db.connect() as c:
            p = c.execute(
                "SELECT p.*,u.username AS author_username,v.username AS publisher_username, "
                "EXISTS(SELECT 1 FROM library l WHERE l.user_id=? AND l.program_id=p.id) AS added "
                "FROM programs p LEFT JOIN users u ON u.id=p.author_id LEFT JOIN users v ON v.id=p.owner_id "
                "WHERE p.id=? AND p.published=1",
                (uid, pid),
            ).fetchone()
            if not p:
                raise UserError("Публикация больше недоступна.")
            return dict(p)

    def add_from_workshop(self, uid, pid):
        with self.db.transaction() as c:
            if not c.execute("SELECT 1 FROM programs WHERE id=? AND published=1", (pid,)).fetchone():
                raise UserError("Публикация больше недоступна.")
            c.execute("INSERT OR IGNORE INTO library(user_id,program_id) VALUES(?,?)", (uid, pid))

    def copy_program(self, uid, pid):
        with self.db.transaction() as c:
            p = self._program(c, uid, pid)
            name = (p["alias"] or p["name"])[:70] + " · копия"
            document = json.loads(p["document"])
            document["name"] = name
            result = c.execute(
                "INSERT INTO programs(name,author_id,author_name,owner_id,source_id,document) "
                "VALUES(?,?,?,?,?,?)",
                (name, p["author_id"], p["author_name"], uid, pid, dumps(document)),
            )
            c.execute("INSERT INTO library(user_id,program_id) VALUES(?,?)", (uid, result.lastrowid))
            return result.lastrowid

    def create_program(self, uid, text, edit_pid=None):
        # Compact text constructor: title, followed by day | exercise | group | sets | reps | weight | rest.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not 2 <= len(lines) <= 61 or len(lines[0]) > 80:
            raise UserError("Название до 80 символов и 1–60 строк упражнений.")
        days = {}
        for line in lines[1:]:
            fields = [v.strip() for v in line.split("|")]
            if len(fields) != 7:
                raise UserError("Строка: День | Упражнение | группа | подходы | повторы | вес или - | отдых")
            day, name, group, count, reps, weight, rest = fields
            if (
                not day
                or not name
                or len(day) > 80
                or len(name) > 100
                or group not in GROUPS
                or group == "all"
            ):
                raise UserError("Проверьте названия и группу: chest/back/shoulders/arms/legs/core.")
            e = {
                "name": name,
                "group": group,
                "rest_seconds": integer(rest, 0, 900),
                "rep_min": 8,
                "rep_max": integer(reps, 1, 1000),
                "increment": 2.5,
                "progression_enabled": False,
                "sets": [
                    {
                        "weight": None if weight == "-" else numeric(weight, 0, 2000),
                        "reps": integer(reps, 1, 1000),
                    }
                    for _ in range(integer(count, 1, 10))
                ],
            }
            days.setdefault(day, []).append(e)
        if len(days) > 14:
            raise UserError("Максимум 14 дней в программе.")
        document = {
            "version": 1,
            "name": lines[0],
            "days": [{"name": k, "exercises": v} for k, v in days.items()],
        }
        with self.db.transaction() as c:
            name = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()[0]
            if edit_pid is not None:
                p = self._program(c, uid, edit_pid)
                if p["owner_id"] != uid:
                    raise UserError("Редактировать можно только свою программу.")
                if p["is_base"]:
                    raise UserError(
                        "Исходная Excel-программа сохранена неизменной. Создайте личную копию для редактирования."
                    )
                c.execute(
                    "UPDATE programs SET name=?,document=? WHERE id=?", (lines[0], dumps(document), edit_pid)
                )
                return edit_pid
            pid = c.execute(
                "INSERT INTO programs(name,author_id,author_name,owner_id,document) VALUES(?,?,?,?,?)",
                (lines[0], uid, name, uid, dumps(document)),
            ).lastrowid
            c.execute("INSERT INTO library(user_id,program_id) VALUES(?,?)", (uid, pid))
            return pid

    def edit_program(self, uid, pid, text):
        return self.create_program(uid, text, edit_pid=pid)

    def share_program(self, uid, pid, revoke=False):
        with self.db.transaction() as c:
            p = self._program(c, uid, pid)
            if p["owner_id"] != uid:
                raise UserError("Сначала создайте свою копию программы.")
            token = None if revoke else (p["share_token"] or secrets.token_urlsafe(16))
            c.execute("UPDATE programs SET share_token=? WHERE id=?", (token, pid))
            return token

    def redeem(self, uid, payload):
        prefix, _, token = payload.partition("_")
        now = self.clock()
        with self.db.transaction() as c:
            if prefix == "p":
                p = c.execute("SELECT * FROM programs WHERE share_token=?", (token,)).fetchone()
                if not p:
                    raise UserError("Ссылка программы недействительна.")
                c.execute("INSERT OR IGNORE INTO library(user_id,program_id) VALUES(?,?)", (uid, p["id"]))
                return "Программа добавлена. Автор сохранён: " + p["author_name"]
            if prefix == "f":
                friend = c.execute("SELECT * FROM users WHERE friend_token=?", (token,)).fetchone()
                if not friend or friend["id"] == uid:
                    raise UserError("Приглашение недействительно или принадлежит вам.")
                a, b = sorted((uid, friend["id"]))
                c.execute("INSERT OR IGNORE INTO friendships VALUES(?,?,?)", (a, b, now))
                return "Друг добавлен: " + friend["name"]
            if prefix == "c":
                challenge = c.execute(
                    "SELECT * FROM challenges WHERE token=? AND ends>?", (token, now)
                ).fetchone()
                if not challenge:
                    raise UserError("Челлендж завершён или не найден.")
                c.execute(
                    "INSERT OR IGNORE INTO challenge_members VALUES(?,?,?)", (challenge["id"], uid, now)
                )
                return "Вы участвуете: " + challenge["name"]
            if prefix == "r":
                room = c.execute("SELECT * FROM rooms WHERE token=? AND expires>?", (token, now)).fetchone()
                if not room:
                    raise UserError("Комната закрыта (срок действия — 24 часа).")
                c.execute("INSERT OR IGNORE INTO room_members VALUES(?,?)", (room["id"], uid))
                return "Вы в комнате. Следующая тренировка будет связана с ней. Результаты подходов приватны."
            raise UserError("Неизвестная ссылка.")

    def _active(self, c, uid, sid=None, revision=None):
        row = c.execute("SELECT * FROM sessions WHERE user_id=? AND status='active'", (uid,)).fetchone()
        if not row or (sid is not None and row["id"] != sid):
            raise UserError("Эта тренировка уже закрыта. Откройте «Тренировка».")
        if revision is not None and row["revision"] != revision:
            raise UserError("Кнопка устарела. Откройте текущую тренировку.")
        return row

    def start(self, uid, pid, day_index, group="all", quick=False):
        now = self.clock()
        with self.db.transaction() as c:
            active = c.execute(
                "SELECT id FROM sessions WHERE user_id=? AND status='active'", (uid,)
            ).fetchone()
            if active:
                return active[0]
            p = self._program(c, uid, pid)
            days = json.loads(p["document"])["days"]
            if not 0 <= day_index < len(days) or group not in GROUPS:
                raise UserError("Тренировка не найдена.")
            day = days[day_index]
            plan = [e for e in day["exercises"] if group == "all" or e["group"] == group]
            if not plan:
                raise UserError("В этом дне нет упражнений выбранной группы.")
            if quick:
                plan = plan[:3]
                for e in plan:
                    e["sets"] = e["sets"][:2]
            room = c.execute(
                "SELECT r.id FROM rooms r JOIN room_members m ON m.room_id=r.id "
                "WHERE m.user_id=? AND r.expires>? ORDER BY r.created DESC LIMIT 1",
                (uid, now),
            ).fetchone()
            return c.execute(
                "INSERT INTO sessions(user_id,program_id,day_index,name,started,plan,room_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    uid,
                    pid,
                    day_index,
                    day["name"]
                    + (" · быстрая" if quick else "")
                    + (" · " + GROUPS[group] if group != "all" else ""),
                    now,
                    dumps(plan),
                    room[0] if room else None,
                ),
            ).lastrowid

    def active(self, uid):
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM sessions WHERE user_id=? AND status='active'", (uid,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["plan"] = json.loads(row["plan"])
            result["sets"] = [
                dict(r) for r in c.execute("SELECT * FROM sets WHERE session_id=? ORDER BY id", (row["id"],))
            ]
            return result

    def previous(self, uid, exercise, limit=2):
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT DISTINCT s.id,s.ended FROM sessions s JOIN sets t ON t.session_id=s.id "
                "WHERE s.user_id=? AND s.status='completed' AND t.exercise=? "
                "ORDER BY s.ended DESC,s.id DESC LIMIT ?",
                (uid, exercise, limit),
            ).fetchall()
            return [
                {
                    "session_id": r["id"],
                    "ended": r["ended"],
                    "sets": [
                        dict(t)
                        for t in c.execute(
                            "SELECT * FROM sets WHERE session_id=? AND exercise=? ORDER BY id",
                            (r["id"], exercise),
                        )
                    ],
                }
                for r in rows
            ]

    def settings(self, uid, exercise):
        with self.db.connect() as c:
            row = c.execute(
                "SELECT * FROM exercise_settings WHERE user_id=? AND exercise=?", (uid, exercise)
            ).fetchone()
            return dict(row) if row else None

    def suggestion(self, uid, exercise, count, ordinal):
        history = self.previous(uid, exercise["name"])
        config = self.settings(uid, exercise["name"])
        seed = exercise["sets"][min(ordinal, len(exercise["sets"]) - 1)]
        weight, reps, reason = seed["weight"], seed["reps"], "Из программы; пустые значения нужно ввести."
        if history:
            work = [s for s in history[0]["sets"] if s["kind"] == "working"]
            if work:
                last = work[min(ordinal, len(work) - 1)]
                weight, reps, reason = last["weight"], last["reps"], "Подставлен прошлый результат."
        if config and config["progression"] and len(history) == 2 and weight and weight > 0:
            qualifies = []
            for h in history:
                work = [s for s in h["sets"] if s["kind"] == "working"]
                qualifies.append(
                    len(work) >= count
                    and all(s["reps"] >= config["target"] for s in work)
                    and all(s["weight"] == weight for s in work)
                )
            if all(qualifies):
                weight = round(weight + config["increment"], 2)
                reason = (
                    "Предложено увеличение: две тренировки на верхней границе повторений. Можно изменить."
                )
        return weight, reps, reason

    def log_set(self, uid, sid, revision, weight, reps, rir=None, kind="working"):
        weight, reps = numeric(weight, 0, 2000), integer(reps, 1, 1000)
        rir = None if rir is None else integer(rir, 0, 10)
        if kind not in KINDS:
            raise UserError("Тип: working, warmup, drop или amrap.")
        with self.db.transaction() as c:
            s = self._active(c, uid, sid, revision)
            plan = json.loads(s["plan"])
            if s["cursor"] >= len(plan):
                raise UserError("Все упражнения завершены. Сохраните тренировку.")
            e = plan[s["cursor"]]
            rows = c.execute(
                "SELECT * FROM sets WHERE session_id=? AND position=?", (sid, s["cursor"])
            ).fetchall()
            ordinal = len(rows)
            c.execute(
                "INSERT INTO sets(session_id,position,ordinal,exercise,muscle,weight,reps,rir,kind,created) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sid, s["cursor"], ordinal, e["name"], e["group"], weight, reps, rir, kind, self.clock()),
            )
            working = sum(r["kind"] != "warmup" for r in rows) + (kind != "warmup")
            cursor = s["cursor"] + (working >= len(e["sets"]))
            setting = c.execute(
                "SELECT rest FROM exercise_settings WHERE user_id=? AND exercise=?", (uid, e["name"])
            ).fetchone()
            rest = setting[0] if setting else e["rest_seconds"]
            c.execute(
                "UPDATE sessions SET cursor=?,revision=revision+1,rest_due=? WHERE id=?",
                (cursor, self.clock() + rest if rest and cursor < len(plan) else None, sid),
            )

    def mutate_session(self, uid, sid, revision, action, value=None):
        with self.db.transaction() as c:
            s = self._active(c, uid, sid, revision)
            plan, cursor = json.loads(s["plan"]), s["cursor"]
            rest = s["rest_due"]
            if action == "skiprest":
                rest = None
            elif action == "rest30":
                rest = max(rest or self.clock(), self.clock()) + 30
            elif action == "undo":
                last = c.execute(
                    "SELECT * FROM sets WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)
                ).fetchone()
                if not last:
                    raise UserError("Нет подхода для отмены.")
                c.execute("DELETE FROM sets WHERE id=?", (last["id"],))
                cursor, rest = last["position"], None
            elif action == "skip":
                cursor, rest = min(cursor + 1, len(plan)), None
            elif action == "replace":
                if cursor >= len(plan):
                    raise UserError("Нет текущего упражнения.")
                existing = c.execute(
                    "SELECT 1 FROM sets WHERE session_id=? AND position=?", (sid, cursor)
                ).fetchone()
                if existing:
                    raise UserError(
                        "Заменять можно до первого подхода. Отмените подходы или пропустите упражнение."
                    )
                catalog = self._catalog(c, uid)
                replacement = next(
                    (e for e in catalog if e["name"] == value and e["group"] == plan[cursor]["group"]), None
                )
                if not replacement:
                    raise UserError("Замена не найдена в этой группе.")
                # Preserve the number of slots in quick/custom sessions on replacement.
                count = len(plan[cursor]["sets"])
                replacement["sets"] = [
                    dict(replacement["sets"][min(i, len(replacement["sets"]) - 1)]) for i in range(count)
                ]
                plan[cursor] = replacement
                rest = None
            else:
                raise UserError("Неизвестное действие.")
            c.execute(
                "UPDATE sessions SET plan=?,cursor=?,rest_due=?,revision=revision+1 WHERE id=?",
                (dumps(plan), cursor, rest, sid),
            )

    def finish(self, uid, sid, revision, abandon=False):
        with self.db.transaction() as c:
            self._active(c, uid, sid, revision)
            count = c.execute(
                "SELECT count(*) FROM sets WHERE session_id=? AND kind!='warmup'", (sid,)
            ).fetchone()[0]
            if not abandon and not count:
                raise UserError("Нет рабочих подходов. Можно отменить тренировку.")
            now = self.clock()
            c.execute(
                "UPDATE sessions SET status=?,ended=?,rest_due=NULL,revision=revision+1 WHERE id=?",
                ("abandoned" if abandon else "completed", now, sid),
            )
            if not abandon:
                xp = 50 + min(count, 100) * 5
                c.execute("UPDATE users SET xp=xp+?,prompt=NULL WHERE id=?", (xp, uid))
                total = c.execute(
                    "SELECT count(*) FROM sessions WHERE user_id=? AND status='completed'", (uid,)
                ).fetchone()[0]
                for threshold, code in [
                    (1, "Первая тренировка"),
                    (10, "10 тренировок"),
                    (50, "50 тренировок"),
                ]:
                    if total >= threshold:
                        c.execute("INSERT OR IGNORE INTO achievements VALUES(?,?,?)", (uid, code, now))
                return xp
            return 0

    def _catalog(self, c, uid):
        catalog = {}
        for row in c.execute(
            "SELECT document FROM programs p JOIN library l ON l.program_id=p.id WHERE l.user_id=?", (uid,)
        ):
            for day in json.loads(row[0])["days"]:
                for e in day["exercises"]:
                    catalog.setdefault(e["name"], e)
        return list(catalog.values())

    def alternatives(self, uid, group, name):
        with self.db.connect() as c:
            return [e for e in self._catalog(c, uid) if e["group"] == group and e["name"] != name]

    def note(self, uid, exercise):
        with self.db.connect() as c:
            row = c.execute(
                "SELECT note FROM exercise_notes WHERE user_id=? AND exercise=?", (uid, exercise)
            ).fetchone()
            return row[0] if row else ""

    def save_note(self, uid, exercise, text):
        if len(text) > 1000:
            raise UserError("Заметка: до 1000 символов.")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO exercise_notes VALUES(?,?,?) ON CONFLICT(user_id,exercise) DO UPDATE SET note=excluded.note",
                (uid, exercise, text),
            )

    def save_settings(self, uid, exercise, rest, target, increment, enabled):
        values = (
            uid,
            exercise,
            integer(rest, 0, 900),
            integer(target, 1, 1000),
            numeric(increment, 0.1, 100),
            integer(enabled, 0, 1),
        )
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO exercise_settings VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,exercise) DO UPDATE SET "
                "rest=excluded.rest,target=excluded.target,increment=excluded.increment,progression=excluded.progression",
                values,
            )

    def history(self, uid, offset=0):
        with self.db.connect() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT s.*,count(t.id) AS set_count FROM sessions s LEFT JOIN sets t "
                    "ON t.session_id=s.id WHERE s.user_id=? AND s.status!='active' GROUP BY s.id "
                    "ORDER BY s.started DESC,s.id DESC LIMIT 10 OFFSET ?",
                    (uid, max(0, offset)),
                )
            ]

    def session_detail(self, uid, sid):
        with self.db.connect() as c:
            s = c.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (sid, uid)).fetchone()
            if not s:
                raise UserError("Тренировка недоступна.")
            return dict(s), [
                dict(r) for r in c.execute("SELECT * FROM sets WHERE session_id=? ORDER BY id", (sid,))
            ]

    def checkin(self, uid, weight, waist=None, chest=None, hips=None, arm=None):
        values = [numeric(weight, 1, 500)] + [
            None if v is None or v == "-" else numeric(v, 1, 300) for v in (waist, chest, hips, arm)
        ]
        with self.db.transaction() as c:
            now = self.clock()
            c.execute(
                "INSERT INTO checkins(user_id,created,weight,waist,chest,hips,arm) VALUES(?,?,?,?,?,?,?)",
                (uid, now, *values),
            )
            tz = c.execute("SELECT timezone FROM users WHERE id=?", (uid,)).fetchone()[0]
            c.execute("UPDATE users SET checkin_due=? WHERE id=?", (next_checkin(now, tz, self.hour), uid))
            count = c.execute("SELECT count(*) FROM checkins WHERE user_id=?", (uid,)).fetchone()[0]
            if count >= 4:
                c.execute("INSERT OR IGNORE INTO achievements VALUES(?,?,?)", (uid, "4 check-in", now))

    def add_goal(self, uid, kind, target, days):
        if kind not in ("weight", "workouts"):
            raise UserError("Тип цели: weight или workouts.")
        target = numeric(target, 1, 500) if kind == "weight" else integer(target, 1, 1000)
        days = integer(days, 1, 730)
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT weight FROM checkins WHERE user_id=? ORDER BY created DESC,id DESC LIMIT 1", (uid,)
            ).fetchone()
            if kind == "weight" and not row:
                raise UserError("Сначала запишите текущий вес через check-in.")
            c.execute(
                "INSERT INTO goals(user_id,kind,target,deadline,created,start_value) VALUES(?,?,?,?,?,?)",
                (uid, kind, target, self.clock() + days * 86400, self.clock(), row[0] if row else None),
            )

    def goals(self, uid):
        with self.db.connect() as c:
            result = []
            for g in c.execute("SELECT * FROM goals WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)):
                if g["kind"] == "workouts":
                    value = c.execute(
                        "SELECT count(*) FROM sessions WHERE user_id=? AND status='completed' "
                        "AND ended>=? AND ended<=?",
                        (uid, g["created"], g["deadline"]),
                    ).fetchone()[0]
                    achieved = value >= g["target"]
                else:
                    row = c.execute(
                        "SELECT weight FROM checkins WHERE user_id=? AND created>=? AND created<=? "
                        "ORDER BY created DESC,id DESC LIMIT 1",
                        (uid, g["created"], g["deadline"]),
                    ).fetchone()
                    value = row[0] if row else g["start_value"]
                    achieved = (
                        value <= g["target"] if g["target"] <= g["start_value"] else value >= g["target"]
                    )
                result.append({**dict(g), "value": value, "achieved": achieved})
            return result

    def progress(self, uid):
        with self.db.connect() as c:
            now = self.clock()
            summary = dict(
                c.execute(
                    "SELECT count(*) AS total, coalesce(avg(ended-started),0) AS duration, "
                    "coalesce(sum(ended>=?),0) AS week, coalesce(sum(ended>=?),0) AS month "
                    "FROM sessions WHERE user_id=? AND status='completed'",
                    (now - 7 * 86400, now - 30 * 86400, uid),
                ).fetchone()
            )
            summary["muscles"] = [
                dict(r)
                for r in c.execute(
                    "SELECT muscle,count(*) AS sets,sum(weight*reps) AS volume "
                    "FROM sets t JOIN sessions s ON s.id=t.session_id WHERE s.user_id=? AND s.status='completed' "
                    "AND s.ended>=? AND t.kind!='warmup' GROUP BY muscle",
                    (uid, now - 7 * 86400),
                )
            ]
            summary["prs"] = [
                dict(r)
                for r in c.execute(
                    "SELECT exercise,max(weight) AS weight,max(reps) AS reps, "
                    "max(weight*reps) AS volume FROM sets t JOIN sessions s ON s.id=t.session_id "
                    "WHERE s.user_id=? AND s.status='completed' AND t.kind!='warmup' GROUP BY exercise",
                    (uid,),
                )
            ]
            summary["checkins"] = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM checkins WHERE user_id=? ORDER BY created DESC,id DESC LIMIT 12", (uid,)
                )
            ]
            summary["achievements"] = [
                r[0]
                for r in c.execute("SELECT code FROM achievements WHERE user_id=? ORDER BY earned", (uid,))
            ]
            return summary

    def recommend(self, uid):
        with self.db.connect() as c:
            last = {
                r[0]: r[1]
                for r in c.execute(
                    "SELECT t.muscle,max(s.ended) FROM sets t JOIN sessions s ON s.id=t.session_id "
                    "WHERE s.user_id=? AND s.status='completed' AND t.kind!='warmup' GROUP BY t.muscle",
                    (uid,),
                )
            }
            day_last = {
                (r[0], r[1]): r[2]
                for r in c.execute(
                    "SELECT program_id,day_index,max(ended) FROM sessions "
                    "WHERE user_id=? AND status='completed' GROUP BY program_id,day_index",
                    (uid,),
                )
            }
        now = self.clock()
        candidates = []
        for p in self.programs(uid):
            for i, day in enumerate(json.loads(p["document"])["days"]):
                groups = {e["group"] for e in day["exercises"]}
                recent = [GROUPS[g] for g in groups if now - last.get(g, 0) < 48 * 3600]
                candidates.append(
                    {
                        "pid": p["id"],
                        "day": i,
                        "name": day["name"],
                        "recent": recent,
                        "last": day_last.get((p["id"], i), 0),
                    }
                )
        candidates.sort(key=lambda x: (len(x["recent"]), x["last"], x["pid"], x["day"]))
        return candidates[:3]

    def set_prompt(self, uid, action=None, **data):
        with self.db.transaction() as c:
            c.execute(
                "UPDATE users SET prompt=? WHERE id=?",
                (dumps({"action": action, **data}) if action else None, uid),
            )

    def set_timezone(self, uid, timezone):
        try:
            ZoneInfo(timezone)
        except (ValueError, KeyError):
            raise UserError("Нужен часовой пояс IANA, например Europe/Berlin.") from None
        with self.db.transaction() as c:
            c.execute(
                "UPDATE users SET timezone=?,checkin_due=? WHERE id=?",
                (timezone, next_checkin(self.clock(), timezone, self.hour), uid),
            )

    def toggle_reminders(self, uid):
        with self.db.transaction() as c:
            c.execute("UPDATE users SET reminders=1-reminders WHERE id=?", (uid,))

    def create_challenge(self, uid, name, target, days):
        if not name or len(name) > 80:
            raise UserError("Название: 1–80 символов.")
        target, days = integer(target, 1, 100), integer(days, 1, 90)
        now, token = self.clock(), secrets.token_urlsafe(16)
        with self.db.transaction() as c:
            cid = c.execute(
                "INSERT INTO challenges(owner_id,name,target,started,ends,token) VALUES(?,?,?,?,?,?)",
                (uid, name, target, now, now + days * 86400, token),
            ).lastrowid
            c.execute("INSERT INTO challenge_members VALUES(?,?,?)", (cid, uid, now))
        return token

    def social(self, uid):
        with self.db.connect() as c:
            friends = [
                dict(r)
                for r in c.execute(
                    "SELECT u.id,u.name,u.xp FROM friendships f JOIN users u "
                    "ON u.id=CASE WHEN f.a=? THEN f.b ELSE f.a END WHERE f.a=? OR f.b=?",
                    (uid, uid, uid),
                )
            ]
            challenges = []
            for r in c.execute(
                "SELECT ch.* FROM challenges ch JOIN challenge_members m ON m.challenge_id=ch.id "
                "WHERE m.user_id=? ORDER BY ch.started DESC LIMIT 10",
                (uid,),
            ):
                members = [
                    dict(m)
                    for m in c.execute(
                        "SELECT u.name,count(s.id) AS score FROM challenge_members m "
                        "JOIN users u ON u.id=m.user_id LEFT JOIN sessions s ON s.user_id=m.user_id "
                        "AND s.status='completed' AND s.ended>=max(?,m.joined) AND s.ended<=? "
                        "WHERE m.challenge_id=? GROUP BY u.id ORDER BY score DESC",
                        (r["started"], r["ends"], r["id"]),
                    )
                ]
                challenges.append({**dict(r), "members": members})
            rooms = [
                dict(r)
                for r in c.execute(
                    "SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id "
                    "WHERE m.user_id=? AND r.expires>?",
                    (uid, self.clock()),
                )
            ]
            for r in rooms:
                r["members"] = [
                    dict(m)
                    for m in c.execute(
                        "SELECT u.name,coalesce((SELECT status FROM sessions "
                        "WHERE user_id=u.id AND room_id=? ORDER BY id DESC LIMIT 1),'ожидает') AS status "
                        "FROM room_members m JOIN users u ON u.id=m.user_id WHERE m.room_id=?",
                        (r["id"], r["id"]),
                    )
                ]
            return friends, challenges, rooms

    def remove_friend(self, uid, other):
        with self.db.transaction() as c:
            c.execute("DELETE FROM friendships WHERE a=? AND b=?", tuple(sorted((uid, other))))

    def create_room(self, uid):
        now = self.clock()
        with self.db.transaction() as c:
            row = c.execute("SELECT token FROM rooms WHERE owner_id=? AND expires>?", (uid, now)).fetchone()
            if row:
                return row[0]
            token = secrets.token_urlsafe(16)
            rid = c.execute(
                "INSERT INTO rooms(owner_id,token,created,expires) VALUES(?,?,?,?)",
                (uid, token, now, now + 86400),
            ).lastrowid
            c.execute("INSERT INTO room_members VALUES(?,?)", (rid, uid))
            return token

    def leave_room(self, uid):
        with self.db.transaction() as c:
            c.execute("DELETE FROM room_members WHERE user_id=?", (uid,))
            c.execute("UPDATE sessions SET room_id=NULL WHERE user_id=? AND status='active'", (uid,))

    def leave_challenge(self, uid, cid):
        with self.db.transaction() as c:
            c.execute("DELETE FROM challenge_members WHERE user_id=? AND challenge_id=?", (uid, cid))

    def due_notifications(self):
        # Deadlines are durable. One process; DB is never locked while calling Telegram.
        now = self.clock()
        with self.db.connect() as c:
            rest = [
                dict(r)
                for r in c.execute(
                    "SELECT id,user_id,rest_due FROM sessions WHERE status='active' AND rest_due<=?", (now,)
                )
            ]
            weekly = [
                dict(r)
                for r in c.execute(
                    "SELECT id,timezone,checkin_due FROM users WHERE reminders=1 AND checkin_due<=?", (now,)
                )
            ]
            return rest, weekly

    def acknowledge_notification(self, kind, row):
        with self.db.transaction() as c:
            if kind == "rest":
                c.execute(
                    "UPDATE sessions SET rest_due=NULL WHERE id=? AND rest_due=?",
                    (row["id"], row["rest_due"]),
                )
            else:
                c.execute(
                    "UPDATE users SET checkin_due=? WHERE id=? AND checkin_due=?",
                    (next_checkin(self.clock(), row["timezone"], self.hour), row["id"], row["checkin_due"]),
                )

    def disable_notifications(self, uid):
        with self.db.transaction() as c:
            c.execute("UPDATE users SET reminders=0 WHERE id=?", (uid,))
            c.execute("UPDATE sessions SET rest_due=NULL WHERE user_id=?", (uid,))
