"""On-hardware diagnostic for Windows brightness application.

Run this on the actual laptop (Windows) to verify that brightness
requests really change the internal display, independent of the GUI:

    python brightness_diagnostic.py            # interactive
    python brightness_diagnostic.py --yes      # no confirmation prompt

It reads the current brightness through WMI, applies three test
values through the application's own verified applier, reads the
panel back after each step, restores the original brightness, and
prints a PASS/FAIL line per step. Exit code 0 means every step was
verified on the hardware; 1 means at least one was not; 2 means the
check could not run (not Windows, or no WMI brightness readback).

The diagnostic never touches the camera and restores the original
brightness even on failure.
"""

from __future__ import annotations

import sys

import adaptive_brightness as engine


def run_diagnostic(applier_apply=None, read_current=None, out=print) -> int:
    """Execute the set/verify/restore sequence. Returns an exit code."""
    apply_fn = applier_apply or engine.apply_brightness
    read_fn = read_current or engine.get_current_brightness

    initial = read_fn()
    if initial is None:
        out("FAIL: could not read the current display brightness via WMI.")
        out("The internal panel may not expose WmiMonitorBrightness, or "
            "this is not running on Windows.")
        return 2
    out(f"Initial display brightness (WMI readback): {initial}%")

    steps = []
    for delta in (+15, -25, +10):
        steps.append(max(10, min(95, initial + delta)))
    steps = [value for index, value in enumerate(steps)
             if index == 0 or value != steps[index - 1]]

    all_ok = True
    try:
        for target in steps:
            result = apply_fn(target)
            readback = read_fn()
            verified = (
                result.ok and readback is not None and abs(readback - target) <= 1
            )
            all_ok = all_ok and verified
            out(
                f"apply target={target}% status={result.status} "
                f"method={result.method} attempts={result.attempts} "
                f"readback={readback}% -> {'PASS' if verified else 'FAIL'}"
            )
    finally:
        restore = apply_fn(initial)
        restored = read_fn()
        out(
            f"restore target={initial}% status={restore.status} "
            f"readback={restored}%"
        )

    out(
        "RESULT: PASS - brightness requests verifiably change the display"
        if all_ok
        else "RESULT: FAIL - at least one step was not verified on the panel"
    )
    return 0 if all_ok else 1


def main() -> int:
    if sys.platform != "win32":
        print("This diagnostic must run on Windows, on the target laptop.")
        return 2
    if "--yes" not in sys.argv:
        input("This will briefly change your screen brightness and restore "
              "it. Press Enter to continue...")
    return run_diagnostic()


if __name__ == "__main__":
    raise SystemExit(main())
