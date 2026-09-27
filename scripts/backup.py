"""Online SQLite backup, including committed WAL content; refuses to overwrite a file."""

import argparse
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("Source database does not exist")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open("xb"):
        pass
    src = sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(args.destination)
    try:
        src.backup(dst)
        print("Backup created:", args.destination)
    finally:
        dst.close()
        src.close()


if __name__ == "__main__":
    main()
