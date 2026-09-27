"""Rebuild seed deterministically, preserving missing values and source coordinates."""

import hashlib
import json
import re
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fitness_bot/data/source.xlsx"
GROUPS = [
    "chest",
    "back",
    "back",
    "shoulders",
    "arms",
    "arms",
    "legs",
    "legs",
    "chest",
    "back",
    "shoulders",
    "shoulders",
    "arms",
    "arms",
    "legs",
    "chest",
    "back",
    "shoulders",
    "shoulders",
    "arms",
    "arms",
    "legs",
    "legs",
]


def number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:кг)?\s*", value)
    if not match:
        raise ValueError(f"Unrecognized numeric value: {value!r}")
    return float(match[1].replace(",", "."))


def extract():
    workbook = openpyxl.load_workbook(SOURCE, data_only=False)
    sheet = workbook["Аркуш1"]
    days = []
    index = 0
    for row in sheet.iter_rows():
        title = str(row[0].value or "").strip()
        if re.fullmatch(r"Тренировка \d+", title):
            days.append({"name": title, "exercises": []})
        elif title and days and not title.startswith("Упражнения"):
            sets = []
            for c in range(1, 9, 2):
                sets.append(
                    {
                        "weight": number(row[c].value),
                        "reps": number(row[c + 1].value),
                        "weight_cell": row[c].coordinate,
                        "reps_cell": row[c + 1].coordinate,
                    }
                )
            days[-1]["exercises"].append(
                {
                    "name": title,
                    "group": GROUPS[index],
                    "name_cell": row[0].coordinate,
                    "sets": sets,
                    "rest_seconds": 90,
                    "rep_min": 8,
                    "rep_max": 12,
                    "increment": 2.5,
                    "load_basis": "external",
                    "progression_enabled": False,
                }
            )
            index += 1
    assert [len(d["exercises"]) for d in days] == [8, 7, 8]
    return {
        "version": 1,
        "name": "Программа тренировок",
        "author": "configured_owner",
        "source": {
            "file": "Программа тренировок.xlsx",
            "sheet": sheet.title,
            "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        },
        "assumptions": [
            "Four set slots inferred from B:I; empty cells remain null.",
            "Muscle groups, 90-second rest, 8–12 range and 2.5 kg step are app defaults, not Excel facts.",
            "Weight basis (per dumbbell/total/additional) is unspecified. Preserve the recorded number.",
            "Progression is opt-in per exercise; no automatic change to recorded weights.",
        ],
        "days": days,
    }


if __name__ == "__main__":
    output = ROOT / "fitness_bot/data/starter.json"
    output.write_text(json.dumps(extract(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
