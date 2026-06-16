"""Helpers for translating subprocess exit codes into human-readable form.

POSIX:
    - Negative `rc` values from `subprocess.Popen.poll()` indicate the child
      was killed by a signal (`-rc`).
    - A child that itself exits with `128 + signum` (the shell convention used
      by bash, sh, etc.) is recognized opportunistically when the value lands
      in the conventional range `129..192` AND maps to a known signal name.
      Plain non-shell exit codes that happen to fall in that range remain
      classified as ordinary exit codes.

Windows:
    - `Popen.poll()` returns the raw process exit code as a signed Python int.
    - Codes with the high bit set (>= 0x80000000) are NTSTATUS values; those
      that match the table below are translated to their symbolic name.
"""

from __future__ import annotations

import signal
import sys

# Source: ntstatus.h from the Windows SDK. Limited to codes routinely seen in
# crashing child processes (CRT/runtime aborts, native vendor-driver faults,
# loader failures, file/handle conflicts). Extend as new failure modes show up.
_WINDOWS_NTSTATUS_DESCRIPTIONS: dict[int, str] = {
    0x40010005: "DBG_CONTROL_C",
    0x80000003: "STATUS_BREAKPOINT",
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC0000017: "STATUS_NO_MEMORY",
    0xC0000018: "STATUS_CONFLICTING_ADDRESSES",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION",
    0xC0000022: "STATUS_ACCESS_DENIED",
    0xC0000025: "STATUS_NONCONTINUABLE_EXCEPTION",
    0xC0000026: "STATUS_INVALID_DISPOSITION",
    0xC0000054: "STATUS_FILE_LOCK_CONFLICT",
    0xC000008C: "STATUS_ARRAY_BOUNDS_EXCEEDED",
    0xC000008D: "STATUS_FLOAT_DENORMAL_OPERAND",
    0xC000008E: "STATUS_FLOAT_DIVIDE_BY_ZERO",
    0xC000008F: "STATUS_FLOAT_INEXACT_RESULT",
    0xC0000090: "STATUS_FLOAT_INVALID_OPERATION",
    0xC0000091: "STATUS_FLOAT_OVERFLOW",
    0xC0000092: "STATUS_FLOAT_STACK_CHECK",
    0xC0000093: "STATUS_FLOAT_UNDERFLOW",
    0xC0000094: "STATUS_INTEGER_DIVIDE_BY_ZERO",
    0xC0000095: "STATUS_INTEGER_OVERFLOW",
    0xC0000096: "STATUS_PRIVILEGED_INSTRUCTION",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC00000FE: "STATUS_BAD_STACK",
    0xC0000135: "STATUS_DLL_NOT_FOUND",
    0xC0000139: "STATUS_ENTRYPOINT_NOT_FOUND",
    0xC000013A: "STATUS_CONTROL_C_EXIT",
    0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC0000194: "STATUS_POSSIBLE_DEADLOCK",
    0xC0000235: "STATUS_HANDLE_NOT_CLOSABLE",
    0xC000026E: "STATUS_VOLUME_DISMOUNTED",
    0xC0000374: "STATUS_HEAP_CORRUPTION",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0xC0000417: "STATUS_INVALID_CRUNTIME_PARAMETER",
    0xC000041D: "STATUS_FATAL_USER_CALLBACK_EXCEPTION",
    0xC0000420: "STATUS_ASSERTION_FAILURE",
    0xC015000F: "STATUS_SXS_EARLY_DEACTIVATION",
}


def _coerce_int(rc: object) -> int | None:
    if rc is None:
        return None
    if not isinstance(rc, (str, bytes, bytearray, int)):
        return None
    try:
        return int(rc)
    except (TypeError, ValueError):
        return None


def _posix_signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except (ValueError, AttributeError):
        return f"SIG{signum}"


def derive_signal_name(rc: object) -> str | None:
    """Return a symbolic name for the process exit, or None when it doesn't apply.

    On Windows, returns the NTSTATUS symbolic name when the exit code matches
    a known status. On POSIX, returns the signal name when `rc < 0`, or when
    `rc` looks like a shell-propagated `128 + signum` value that maps to a
    real signal.
    """
    rc_int = _coerce_int(rc)
    if rc_int is None:
        return None
    if sys.platform == "win32":
        unsigned = rc_int & 0xFFFFFFFF
        return _WINDOWS_NTSTATUS_DESCRIPTIONS.get(unsigned)
    if rc_int < 0:
        return _posix_signal_name(-rc_int)
    if 129 <= rc_int <= 192:
        # Shell convention: 128 + signum. Only honor it when the implied signum
        # corresponds to a real signal, otherwise it's just a plain exit code.
        signum = rc_int - 128
        try:
            return signal.Signals(signum).name
        except (ValueError, AttributeError):
            return None
    return None


def describe_exit_code(rc: object) -> str | None:
    """Return a human-readable description of the exit code.

    Format is stable for log scraping:
      - Known names:    ``"NAME (0xHEX)"``  on Windows
                        ``"NAME (signal N)"`` on POSIX signal exits
                        ``"NAME (exit code N)"`` on POSIX 128+signum exits
      - Unknown NTSTATUS: ``"NTSTATUS 0xHEX"``
      - Unknown POSIX signal: ``"signal N"``
      - Plain exit:     ``"exit code N"``
    """
    rc_int = _coerce_int(rc)
    if rc_int is None:
        return None
    name = derive_signal_name(rc_int)
    if sys.platform == "win32":
        unsigned = rc_int & 0xFFFFFFFF
        if name is not None:
            return f"{name} (0x{unsigned:08X})"
        if unsigned >= 0x80000000:
            return f"NTSTATUS 0x{unsigned:08X}"
        return f"exit code {rc_int}"
    if rc_int < 0:
        if name is not None:
            return f"{name} (signal {-rc_int})"
        return f"signal {-rc_int}"
    if name is not None:
        return f"{name} (exit code {rc_int})"
    return f"exit code {rc_int}"


def exit_code_hex(rc: object) -> str | None:
    """Return the unsigned 32-bit hex form of an exit code, or None."""
    rc_int = _coerce_int(rc)
    if rc_int is None:
        return None
    return f"0x{rc_int & 0xFFFFFFFF:08X}"
