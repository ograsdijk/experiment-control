from __future__ import annotations

import argparse
import json
import sys

from ..utils.instance_lock import read_instance_lock_status


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser("experiment_control instance lock status")
    parser.add_argument("instance_id", help="Stack instance_id")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON status",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    status = read_instance_lock_status(str(ns.instance_id))
    if ns.json:
        sys.stdout.write(json.dumps(status, sort_keys=True) + "\n")
        return
    sys.stdout.write(
        "instance_lock "
        f"instance_id={status.get('instance_id')!r} "
        f"status={status.get('status')!r} "
        f"lock_path={status.get('lock_path')!r} "
        f"owner_pid={status.get('owner_pid')} "
        f"owner_alive={status.get('owner_alive')} "
        f"manager_rpc={status.get('manager_rpc')!r}\n"
    )


if __name__ == "__main__":
    main()
