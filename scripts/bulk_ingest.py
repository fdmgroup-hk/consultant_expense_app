"""Index a folder of consultant material in one go.

Faster than the web upload when you are seeding the knowledge base from a shared
drive of handover decks.

    python -m scripts.bulk_ingest "C:/handovers/HSBC 2025" --client HSBC --role developer

Metadata can also come from the folder layout. With --infer-from-path, a file at
    <root>/HSBC/production_support/2025H1/deck.pptx
is tagged client=HSBC, role=production_support, period=2025H1.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/bulk_ingest.py` as well as `python -m scripts.bulk_ingest`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.ingest.extract import SUPPORTED_EXTENSIONS, UnsupportedFileType  # noqa: E402
from app.ingest.pipeline import ROLES, DocumentMeta, DuplicateDocument, ingest_file  # noqa: E402


def infer_from_path(path: Path, root: Path) -> dict[str, str]:
    parts = path.relative_to(root).parts[:-1]
    inferred: dict[str, str] = {}
    for part in parts:
        normalised = part.strip().lower().replace(" ", "_").replace("-", "_")
        if normalised in ROLES and "role" not in inferred:
            inferred["role"] = normalised
        elif any(ch.isdigit() for ch in part) and "placement_period" not in inferred:
            inferred["placement_period"] = part
        elif "client" not in inferred:
            inferred["client"] = part
    return inferred


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk-index consultant material.")
    parser.add_argument("folder", type=Path, help="Folder to walk (recursively).")
    parser.add_argument("--client", default="", help="Client name applied to every file.")
    parser.add_argument("--department", default="",
                        help="Department or desk within the client, e.g. 'Corporate Lending'.")
    parser.add_argument("--role", default="general", choices=list(ROLES))
    parser.add_argument("--consultant", default="", help="Consultant name or initials.")
    parser.add_argument("--period", default="", help="Placement period, e.g. '2025 H1'.")
    parser.add_argument("--tags", default="", help="Comma-separated tags.")
    parser.add_argument("--infer-from-path", action="store_true",
                        help="Read client/role/period from the folder structure.")
    parser.add_argument("--replace", action="store_true", help="Re-index files already present.")
    parser.add_argument("--dry-run", action="store_true", help="List what would be indexed.")
    args = parser.parse_args()

    root: Path = args.folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a folder: {root}", file=sys.stderr)
        return 2

    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith("~$")
    )
    if not files:
        print(f"No supported files under {root}")
        print(f"Looking for: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        return 1

    print(f"Found {len(files)} file(s) under {root}\n")
    db.init_db()

    indexed = skipped = failed = 0
    for path in files:
        fields = {
            "client": args.client,
            "department": args.department,
            "role": args.role,
            "placement_period": args.period,
        }
        if args.infer_from_path:
            fields.update(infer_from_path(path, root))

        meta = DocumentMeta(
            title=path.stem.replace("_", " ").replace("-", " "),
            consultant=args.consultant or None,
            client=fields["client"] or None,
            department=fields["department"] or None,
            role=fields["role"] or "general",
            placement_period=fields["placement_period"] or None,
            tags=[t.strip() for t in args.tags.split(",") if t.strip()],
        )

        label = path.relative_to(root)
        if args.dry_run:
            print(f"  would index  {label}  (client={meta.client}, "
                  f"department={meta.department}, role={meta.role})")
            continue

        try:
            result = ingest_file(path, meta, replace_existing=args.replace)
            print(f"  indexed      {label}  -> {result['chunks']} chunks")
            indexed += 1
        except DuplicateDocument as exc:
            print(f"  skipped      {label}  ({exc})")
            skipped += 1
        except UnsupportedFileType as exc:
            print(f"  unreadable   {label}  ({exc})")
            failed += 1
        except Exception as exc:  # keep going through the rest of the folder
            print(f"  FAILED       {label}  ({exc})")
            failed += 1

    if not args.dry_run:
        print(f"\nIndexed {indexed}, skipped {skipped}, failed {failed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
