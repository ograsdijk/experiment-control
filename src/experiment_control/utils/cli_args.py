from __future__ import annotations

import argparse


def add_manager_args(
    parser: argparse.ArgumentParser,
    *,
    default_manager_rpc: str = "tcp://127.0.0.1:6000",
    default_manager_pub: str = "tcp://127.0.0.1:6001",
) -> None:
    parser.add_argument("--manager-rpc", default=default_manager_rpc)
    parser.add_argument("--manager-pub", default=default_manager_pub)


def add_process_id_arg(
    parser: argparse.ArgumentParser,
    *,
    default: str | None,
    flags: tuple[str, ...] = ("--process-id",),
) -> None:
    parser.add_argument(*flags, dest="process_id", default=default)


def add_rpc_timeout_arg(
    parser: argparse.ArgumentParser,
    *,
    default_ms: int = 2000,
) -> None:
    parser.add_argument("--rpc-timeout-ms", type=int, default=default_ms)


def add_heartbeat_args(
    parser: argparse.ArgumentParser,
    *,
    default_period_s: float = 1.0,
) -> None:
    parser.add_argument("--heartbeat-endpoint", default=None)
    parser.add_argument("--heartbeat-period-s", type=float, default=default_period_s)
