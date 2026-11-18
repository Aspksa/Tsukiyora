"""Standalone guarded recovery command. Never restores via HTTP.

Run from the project folder with the Python server fully closed:
  python restore_memory.py --list
  python restore_memory.py --restore memory-...sqlite3 --confirm
"""
from __future__ import annotations

import argparse
import json

import launcher
from reliability import Reliability


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tsukiyora offline SQLite recovery")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="List verified snapshot candidates")
    action.add_argument("--restore", metavar="BACKUP_FILENAME", help="Restore a named local snapshot")
    parser.add_argument("--confirm", action="store_true", help="Confirm replacement of local memory")
    args = parser.parse_args(argv)
    launcher.DATA.mkdir(parents=True, exist_ok=True)
    recovery = Reliability(launcher.DATABASE, launcher.ROOT)
    if args.list:
        print(json.dumps(recovery.backups(), ensure_ascii=False, indent=2))
        return 0
    if not args.confirm:
        parser.error("--restore requires --confirm")
    # Lock must be held until restore is over. This also prevents a new BAT
    # launcher from opening the DB during replacement.
    handle = launcher.acquire_lock()
    if handle is None:
        parser.error("Tsukiyora is running; stop the BAT window first.")
    try:
        if launcher.previous_url():
            parser.error("Local Tsukiyora server still responds; stop it first.")
        result = recovery.restore_offline(args.restore, confirm=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))
    finally:
        launcher.release_lock(handle)


if __name__ == "__main__":
    raise SystemExit(main())
