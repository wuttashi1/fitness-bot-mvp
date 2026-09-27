import os
import sqlite3
import subprocess
import sys


def test_installed_entry_reads_env_from_working_directory(tmp_path):
    (tmp_path / ".env").write_text(
        "OWNER_TELEGRAM_ID=777\nDATABASE_PATH=./data/test.sqlite3\nDEFAULT_TIMEZONE=Europe/Berlin\n",
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "OWNER_TELEGRAM_ID",
            "DATABASE_PATH",
            "BOT_TOKEN",
            "DEFAULT_TIMEZONE",
            "CHECKIN_HOUR",
            "POLL_INTERVAL_SECONDS",
            "OWNER_DISPLAY_NAME",
        }
    }
    result = subprocess.run(
        [sys.executable, "-m", "fitness_bot", "--init-db"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(tmp_path / "data/test.sqlite3") as connection:
        assert connection.execute("SELECT author_id FROM programs WHERE is_base=1").fetchone()[0] == 777
