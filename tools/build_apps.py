from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from xe_lang.app_catalog import (  # noqa: E402
	AppCatalogError,
	check_app_artifacts,
	load_app_catalog,
	write_app_artifacts,
)


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		description="Build or verify every app artifact declared by apps/manifest.json.",
	)
	parser.add_argument(
		"--check",
		action="store_true",
		help="report missing or stale artifacts without writing files",
	)
	parser.add_argument(
		"--root",
		type=Path,
		default=ROOT,
		help=argparse.SUPPRESS,
	)
	args = parser.parse_args(argv)
	try:
		catalog = load_app_catalog(args.root)
		if args.check:
			checks = check_app_artifacts(catalog, args.root)
			for check in checks:
				print(f"{check.status:>7}  {check.path}")
			return 1 if any(check.status != "current" for check in checks) else 0
		changed = write_app_artifacts(catalog, args.root)
		if changed:
			for path in changed:
				print(f"updated  {path}")
		else:
			print("All app artifacts are current.")
		return 0
	except AppCatalogError as error:
		print(f"App build failed: {error}", file=sys.stderr)
		return 2


if __name__ == "__main__":
	raise SystemExit(main())
