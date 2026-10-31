"""CLI del almacén SQLite: migración verificable desde database.xlsx y estado de la base."""
import argparse
import json
import sys
from pathlib import Path

from academic_config import paths
from canvas_sync.common import SyncError

from .store import DB_NAME, StoreError, info, migrate, verify
from .xlsx import XlsxError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Academic Manager Store: base SQLite canónica.",
                                     epilog=paths.DATA_HELP)
    parser.add_argument("action", choices=("migrate", "verify", "info", "cutover", "unlock"))
    parser.add_argument("--xlsx", help="Workbook de origen (por defecto <runtime>/database.xlsx)")
    parser.add_argument("--db", help=f"Base SQLite (por defecto <runtime>/{DB_NAME})")
    parser.add_argument("--apply", action="store_true",
                        help="cutover/unlock: aplicar de verdad (sin esta opción solo hay simulación)")
    args = parser.parse_args(argv)
    if args.action in ("cutover", "unlock") and (args.xlsx or args.db):
        parser.error(f"{args.action} no se admite con --xlsx/--db: siempre usa el runtime.")
    if args.apply and args.action not in ("cutover", "unlock"):
        parser.error("--apply no se admite fuera de cutover y unlock.")
    try:
        root = Path(paths.runtime(env=("ACADEMIC_MANAGER_DATA",), default=paths.DEFAULT_RUNTIME)).resolve()
    except paths.ConfigError as exc:
        print("Academic Store:", str(exc), file=sys.stderr)
        return 1
    xlsx = Path(args.xlsx) if args.xlsx else root / "database.xlsx"
    db = Path(args.db) if args.db else root / DB_NAME
    try:
        if args.action == "migrate":
            payload = migrate(xlsx, db)
        elif args.action == "verify":
            payload = verify(xlsx, db)
        elif args.action == "cutover":
            from .cutover import cutover
            payload = cutover(root, apply=args.apply)
        elif args.action == "unlock":
            from .unlock import unlock
            payload = unlock(root, apply=args.apply)
        else:
            payload = info(db)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok", True) else 1
    except (StoreError, XlsxError, SyncError, OSError, KeyError) as exc:
        print("Academic Store:", str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
