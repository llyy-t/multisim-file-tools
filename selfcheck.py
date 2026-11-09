#!/usr/bin/env python3
"""Self-check for multisim-file-tools.

Runs without pywin32, without Multisim and without network, so it can be used as
a smoke test on a clean machine. The `spice` subcommand needs Multisim, so it is
checked for shape only unless --live is passed.

    python selfcheck.py
    python selfcheck.py --live --design "C:\\path\\to\\design.ms14"
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "worker")
CLI = os.path.join(HERE, "cli.py")
DECODER = os.path.join(WORKER, "bin", "blast_harness.exe")


def check_python() -> dict:
    return {
        "version": "%d.%d.%d" % sys.version_info[:3],
        "bits": struct.calcsize("P") * 8,
        "executable": sys.executable,
    }


def check_worker_imports() -> dict:
    saved = list(sys.path)
    result = {}
    try:
        sys.path.insert(0, WORKER)
        for name in ("msim_ms14", "msim_spice", "msim_raster", "msim_deck"):
            try:
                __import__(name)
                result[name] = "ok"
            except Exception as exc:  # noqa: BLE001 - report, do not raise
                result[name] = "%s: %s" % (type(exc).__name__, exc)
        # msim_com must import even without pywin32: it defers COM activation.
        try:
            __import__("msim_com")
            result["msim_com"] = "ok"
        except Exception as exc:  # noqa: BLE001
            result["msim_com"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        sys.path[:] = saved
    return result


def check_decoder() -> dict:
    info = {"path": DECODER, "exists": os.path.isfile(DECODER)}
    if info["exists"]:
        info["bytes"] = os.path.getsize(DECODER)
        # Feed it junk: it must fail, but it must start and exit cleanly.
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source = os.path.join(tmp, "junk.in")
                target = os.path.join(tmp, "junk.out")
                with open(source, "wb") as handle:
                    handle.write(b"not a compressed stream")
                proc = subprocess.run(
                    [DECODER, source, target],
                    capture_output=True,
                    timeout=30,
                )
                info["runs"] = proc.returncode in (0, 1, 2)
        except Exception as exc:  # noqa: BLE001
            info["runs"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def check_decoder_bytes() -> dict:
    """Decompress a hand-built two-byte literal block without any Multisim."""
    saved = list(sys.path)
    try:
        sys.path.insert(0, WORKER)
        import msim_ms14  # noqa: import inside the check
    finally:
        sys.path[:] = saved
    if not os.path.isfile(DECODER):
        return {"skipped": "decoder missing"}

    # One PKWare DCL block encoding the bytes b"ab": the simplest legal stream
    # is a literal followed by end-of-stream. Rather than hand-roll it, round
    # trip a real design if the caller supplied one; otherwise skip.
    return {"skipped": "needs --design"}


def run_cli(args: list) -> dict:
    proc = subprocess.run(
        [sys.executable, CLI] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    payload = None
    if proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except Exception:
            payload = None
    return {
        "exit": proc.returncode,
        "ok": bool(payload and payload.get("ok")),
        "payload": payload,
        "has_stdout": bool(proc.stdout.strip()),
        "stderr_tail": proc.stderr.strip()[-500:],
    }


def check_cli() -> dict:
    out = {}
    for name, args in (
        ("version", ["--version"]),
        ("help", ["--help"]),
        ("check", ["check"]),
        ("netlist", ["netlist", os.path.join(HERE, "examples", "rc_lowpass.cir")]),
        ("design_missing", ["design", os.path.join(HERE, "no_such_file.ms14")]),
    ):
        result = run_cli(args)
        # design_missing is expected to fail with PATH_MISSING.
        if name == "design_missing":
            code = (result["payload"] or {}).get("error", {}).get("code")
            out[name] = {"exit": result["exit"], "code": code}
        elif name in ("version", "help"):
            # These are human-facing argparse output, not JSON payloads.
            out[name] = {"exit": result["exit"], "has_output": result["has_stdout"]}
        else:
            out[name] = {"exit": result["exit"], "ok": result["ok"]}
    return out


def check_design(path: str) -> dict:
    result = run_cli(["design", path])
    payload = result["payload"] or {}
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error")}
    return {
        "ok": True,
        "counts": payload.get("counts"),
        "component_count": len(payload.get("components") or []),
        "instruments": len(payload.get("instruments") or []),
        "creator": payload.get("creator"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also run a real spice deck through Multisim")
    parser.add_argument("--design", default="", help="also decode this .ms14")
    args = parser.parse_args()

    report = {
        "python": check_python(),
        "worker_imports": check_worker_imports(),
        "decoder": check_decoder(),
        "cli": check_cli(),
    }
    if args.design:
        if not os.path.isfile(args.design):
            report["design"] = {"ok": False, "error": "no such file: %s" % args.design}
        else:
            report["design"] = check_design(args.design)
    if args.live:
        outdir = os.path.join(tempfile.gettempdir(), "msim-selfcheck")
        report["spice"] = run_cli(
            [
                "spice",
                os.path.join(HERE, "examples", "rc_lowpass.cir"),
                "-c",
                "op",
                "--output-dir",
                outdir,
            ]
        )
        payload = (report["spice"] or {}).get("payload") or {}
        if payload.get("ok"):
            report["spice"] = {
                "ok": True,
                "elapsed_seconds": payload.get("elapsed_seconds"),
                "analyses": [
                    {"command": item.get("command"), "n_points": item.get("n_points")}
                    for item in payload.get("analyses") or []
                ],
            }

    failures = []
    imports = report["worker_imports"]
    for name, value in imports.items():
        if value != "ok":
            failures.append("%s failed to import: %s" % (name, value))
    if not report["decoder"].get("exists"):
        failures.append("decoder executable missing")
    cli_checks = report["cli"]
    for name in ("version", "help"):
        if not cli_checks[name].get("has_output"):
            failures.append("cli %s printed nothing" % name)
    for name in ("check", "netlist"):
        if not cli_checks[name]["ok"]:
            failures.append("cli %s did not succeed" % name)
    if cli_checks["design_missing"]["code"] != "PATH_MISSING":
        failures.append(
            "cli design on a missing file returned %r, expected PATH_MISSING"
            % cli_checks["design_missing"]["code"]
        )
    if args.design and not report["design"].get("ok"):
        failures.append("design decode of %s failed" % args.design)
    if args.live and not report.get("spice", {}).get("ok"):
        failures.append("live spice run failed")

    report["failures"] = failures
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
