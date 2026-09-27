PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL,
 created REAL NOT NULL, checkin_due REAL NOT NULL, reminders INTEGER NOT NULL DEFAULT 1,
 xp INTEGER NOT NULL DEFAULT 0, prompt TEXT, friend_token TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS programs(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, author_id INTEGER NOT NULL,
 author_name TEXT NOT NULL, owner_id INTEGER, source_id INTEGER REFERENCES programs(id),
 is_base INTEGER NOT NULL DEFAULT 0, share_token TEXT UNIQUE, document TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_base ON programs(is_base) WHERE is_base=1;
CREATE TABLE IF NOT EXISTS library(
 user_id INTEGER REFERENCES users(id), program_id INTEGER REFERENCES programs(id),
 PRIMARY KEY(user_id,program_id)
);
CREATE TABLE IF NOT EXISTS rooms(
 id INTEGER PRIMARY KEY, owner_id INTEGER REFERENCES users(id), token TEXT NOT NULL UNIQUE,
 created REAL NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS room_members(
 room_id INTEGER REFERENCES rooms(id), user_id INTEGER REFERENCES users(id),
 PRIMARY KEY(room_id,user_id)
);
CREATE TABLE IF NOT EXISTS sessions(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 program_id INTEGER REFERENCES programs(id), day_index INTEGER, name TEXT NOT NULL,
 started REAL NOT NULL, ended REAL, status TEXT NOT NULL DEFAULT 'active'
 CHECK(status IN ('active','completed','abandoned')),
 plan TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
 rest_due REAL, room_id INTEGER REFERENCES rooms(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active ON sessions(user_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS user_history ON sessions(user_id,started);
CREATE TABLE IF NOT EXISTS sets(
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
 position INTEGER NOT NULL, ordinal INTEGER NOT NULL, exercise TEXT NOT NULL, muscle TEXT NOT NULL,
 weight REAL NOT NULL CHECK(weight>=0 AND weight<=2000), reps INTEGER NOT NULL CHECK(reps>0 AND reps<=1000),
 rir INTEGER CHECK(rir BETWEEN 0 AND 10), kind TEXT NOT NULL DEFAULT 'working'
 CHECK(kind IN ('working','warmup','drop','amrap')),
 created REAL NOT NULL, UNIQUE(session_id,position,ordinal)
);
CREATE TABLE IF NOT EXISTS exercise_notes(
 user_id INTEGER REFERENCES users(id), exercise TEXT, note TEXT NOT NULL,
 PRIMARY KEY(user_id,exercise)
);
CREATE TABLE IF NOT EXISTS exercise_settings(
 user_id INTEGER REFERENCES users(id), exercise TEXT, rest INTEGER NOT NULL,
 target INTEGER NOT NULL, increment REAL NOT NULL, progression INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(user_id,exercise)
);
CREATE TABLE IF NOT EXISTS checkins(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created REAL NOT NULL,
 weight REAL NOT NULL CHECK(weight>0 AND weight<=500), waist REAL, chest REAL, hips REAL, arm REAL
);
CREATE TABLE IF NOT EXISTS goals(
 id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), kind TEXT NOT NULL,
 target REAL NOT NULL, deadline REAL NOT NULL, created REAL NOT NULL, start_value REAL
);
CREATE TABLE IF NOT EXISTS achievements(
 user_id INTEGER REFERENCES users(id), code TEXT NOT NULL, earned REAL NOT NULL,
 PRIMARY KEY(user_id,code)
);
CREATE TABLE IF NOT EXISTS friendships(
 a INTEGER REFERENCES users(id), b INTEGER REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(a,b), CHECK(a<b)
);
CREATE TABLE IF NOT EXISTS challenges(
 id INTEGER PRIMARY KEY, owner_id INTEGER REFERENCES users(id), name TEXT NOT NULL,
 target INTEGER NOT NULL, started REAL NOT NULL, ends REAL NOT NULL, token TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS challenge_members(
 challenge_id INTEGER REFERENCES challenges(id), user_id INTEGER REFERENCES users(id),
 joined REAL NOT NULL, PRIMARY KEY(challenge_id,user_id)
);
