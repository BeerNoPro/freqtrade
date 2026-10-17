"""
Checks a new checkout before a deploy switches the server's runner to it. ``deploy/deploy.sh``
runs it from a temporary worktree of the commit, against the server's private research folder:

- every rule module of ``run_forward.SPECS`` exists and its fingerprint, computed with the NEW
  code, equals the registered one (otherwise the runner would stop recording setups);
- the private files the runner reads exist (``product.json``, ``thresholds.json``,
  ``fingerprints.jsonl``, the point-in-time store);
- the Telegram token and chat id are set in the env file (their values are never printed).

    python deploy_preflight.py --research ~/signal-bot/user_data/research \\
        --env-file ~/signal-bot/.env

One line per check; exit 0 when every check passes, 1 otherwise.
"""

import argparse
import importlib
import sys
from pathlib import Path

from mce.outbox import credentials


REQUIRED = ("product.json", "thresholds.json", "fingerprints.jsonl", "pit/symbols.json")
Check = tuple[str, bool, str]


def check_specs(research: Path, specs: str) -> list[Check]:
    """Fingerprint of each rule module (imported from ``research``) against its registration."""
    if str(research) not in sys.path:
        sys.path.insert(0, str(research))
    rows: list[Check] = []
    for name in specs.split(","):
        if not (research / f"{name}.py").exists():
            rows.append((name, False, "rule file missing"))
            continue
        module = importlib.import_module(name)
        fp, registered = module.fingerprint(), module.registered_fingerprint()
        rows.append((name, fp == registered, f"fingerprint {fp}, registered {registered}"))
    return rows


def check_files(research: Path) -> list[Check]:
    return [(name, (research / name).exists(), "present") for name in REQUIRED]


def check_telegram(env_file: Path) -> list[Check]:
    found = (
        credentials(Path("/nonexistent"), env={}, dotenv=env_file) if env_file.exists() else None
    )
    detail = "token and chat id set" if found else f"no token / chat id in {env_file}"
    return [("telegram", found is not None, detail)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Checks before a deploy switches the runner")
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--specs", help="rule modules, default run_forward.SPECS of this checkout")
    args = parser.parse_args(argv)
    if args.specs is None:
        from run_forward import SPECS

        args.specs = SPECS
    rows = check_files(args.research) + check_telegram(args.env_file)
    rows += check_specs(args.research, args.specs)
    for name, ok, detail in rows:
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")
    passed = all(ok for _, ok, _ in rows)
    print("preflight: " + ("pass" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
