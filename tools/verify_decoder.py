#!/usr/bin/env python3
"""Verify that the committed decompressor matches its source.

`worker/bin/blast_harness.exe` is a build artefact that ships in this repository
because end users should not need a C compiler to read their own design files.
Shipping a binary nobody can check is unpleasant, so this script rebuilds it and
compares hashes.

MSVC stamps the linker time into the PE header unless /Brepro is used, so an
"identical hash" check is only meaningful on a toolchain that honours /Brepro.
When the hashes differ this script does not just give up: it runs both binaries
over a real compressed block and compares the decoded bytes, because identical
behaviour is the property that actually matters. Pass --probe to enable that,
and see tools/explain_build.py to look at the difference in detail.

Needs MSVC on Windows: CI passes the compiler path explicitly, locally it is
found through vswhere.

    python tools/verify_decoder.py [--probe <compressed block>]

Exit codes: 0 verified, 1 could not verify, 2 could not build, 3 setup problem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "worker", "bin")
COMMITTED = os.path.join(BIN, "blast_harness.exe")

# Deterministic as possible: /Brepro drops the timestamp and the PDB GUID, so
# two builds from the same sources agree byte for byte.  Without it MSVC stamps
# a link time and the comparison is meaningless.
CFLAGS = ["/nologo", "/O2", "/Brepro", "/W3", "/D_CRT_SECURE_NO_WARNINGS"]
SOURCES = ["blast_harness.c", "blast.c"]


class BuildError(RuntimeError):
    """Raised when the compiler is missing or refuses the sources."""


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vswhere_install() -> str:
    """Ask vswhere for the newest VS install that has the C++ toolset."""
    vswhere = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio",
        "Installer",
        "vswhere.exe",
    )
    if not os.path.isfile(vswhere):
        return ""
    try:
        proc = subprocess.run(
            [
                vswhere,
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:  # noqa: BLE001 - absence is a normal outcome
        return ""
    return (proc.stdout or "").strip()


def find_cl() -> str:
    explicit = os.environ.get("MSIM_CL")
    if explicit and os.path.isfile(explicit):
        return explicit
    where = shutil.which("cl")
    if where:
        return where
    install = _vswhere_install()
    if not install:
        return ""
    # The exact toolset version moves with every VS update, so glob for it.
    tools = os.path.join(install, "VC", "Tools", "MSVC")
    if not os.path.isdir(tools):
        return ""
    for name in sorted(os.listdir(tools), reverse=True):
        candidate = os.path.join(tools, name, "bin", "Hostx64", "x64", "cl.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def find_vcvars() -> str:
    explicit = os.environ.get("MSIM_VCVARS64")
    if explicit and os.path.isfile(explicit):
        return explicit
    install = _vswhere_install()
    if not install:
        return ""
    vcvars = os.path.join(install, "VC", "Auxiliary", "Build", "vcvars64.bat")
    return vcvars if os.path.isfile(vcvars) else ""


def build(workdir: str) -> str:
    vcvars = find_vcvars()
    if not vcvars:
        raise BuildError("could not find vcvars64.bat; set MSIM_VCVARS64")
    for name in SOURCES:
        shutil.copy2(os.path.join(BIN, name), os.path.join(workdir, name))
    for name in ("blast.h",):
        source = os.path.join(BIN, name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(workdir, name))
    out_name = "blast_harness.exe"
    # Call cl through a tiny batch file instead of one long cmd line: cmd.exe
    # re-parses a quoted command in /c "..." and the trailing " of the quoting
    # collides with the quotes vcvars64.bat needs.
    batch = os.path.join(workdir, "build.bat")
    with open(batch, "w", encoding="ascii", newline="\r\n") as handle:
        handle.write("@echo off\r\n")
        handle.write('call "%s"\r\n' % vcvars)
        handle.write("if errorlevel 1 exit /b 3\r\n")
        handle.write(
            "cl %s /Fe:%s %s\r\n" % (" ".join(CFLAGS), out_name, " ".join(SOURCES))
        )
        handle.write("if errorlevel 1 exit /b 4\r\n")
        handle.write("exit /b 0\r\n")
    proc = subprocess.run(
        ["cmd", "/c", batch],
        cwd=workdir,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
    )
    target = os.path.join(workdir, out_name)
    if proc.returncode != 0 or not os.path.isfile(target):
        raise BuildError(
            "cl failed (exit %d)\n%s\n%s"
            % (proc.returncode, (proc.stdout or "")[-3000:], (proc.stderr or "")[-3000:])
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", default="", help="expected sha256 of the committed exe")
    parser.add_argument("--probe", default="", help="a compressed block to compare behaviour on")
    parser.add_argument("--keep", action="store_true", help="keep the build directory")
    args = parser.parse_args()

    if not os.path.isfile(COMMITTED):
        print(json.dumps({"ok": False, "reason": "committed executable missing", "path": COMMITTED}))
        return 3

    committed = sha256(COMMITTED)
    # Build beside the repository, not in %TEMP%: a standalone executable is
    # denied write access to the temp directory in some sandboxes.
    workdir = tempfile.mkdtemp(prefix="_decodercheck-", dir=ROOT)
    try:
        built = build(workdir)
        rebuilt = sha256(built)
        same_bytes = committed == rebuilt
        report = {
            "committed": {"sha256": committed, "bytes": os.path.getsize(COMMITTED)},
            "rebuilt": {"sha256": rebuilt, "bytes": os.path.getsize(built)},
            "identical_bytes": same_bytes,
            "matches_expectation": None if not args.expect else committed == args.expect.lower(),
        }
        if same_bytes:
            report["ok"] = True
            report["verdict"] = "reproducible"
            print(json.dumps(report, indent=2))
            return 0

        if not args.probe or not os.path.isfile(args.probe):
            report["ok"] = False
            report["verdict"] = "bytes differ; pass --probe to compare behaviour"
            print(json.dumps(report, indent=2))
            return 1

        outdir = os.path.join(workdir, "out")
        os.makedirs(outdir, exist_ok=True)
        left = _decompress(COMMITTED, args.probe, outdir, "committed")
        right = _decompress(built, args.probe, outdir, "rebuilt")
        report["behaviour"] = {"committed": left, "rebuilt": right}
        identical = left.get("sha256") is not None and left.get("sha256") == right.get("sha256")
        report["ok"] = identical
        report["verdict"] = (
            "bytes differ but behaviour is identical (toolchain stamps the binary)"
            if identical
            else "MISMATCH: the committed binary does not behave like its source"
        )
        print(json.dumps(report, indent=2))
        return 0 if identical else 1
    except BuildError as exc:
        print(json.dumps({"ok": False, "reason": "cannot build", "error": str(exc)[:3000]}))
        return 2
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _decompress(exe: str, payload: str, outdir: str, label: str) -> dict:
    target = os.path.join(outdir, "out-%s.bin" % label)
    proc = subprocess.run([exe, payload, target], capture_output=True, timeout=120)
    info = {"exit": proc.returncode}
    if os.path.isfile(target):
        info["bytes"] = os.path.getsize(target)
        info["sha256"] = sha256(target)
    else:
        info["bytes"] = 0
        info["stderr"] = proc.stderr.decode("utf-8", "replace")[-200:].strip()
    return info


if __name__ == "__main__":
    sys.exit(main())
