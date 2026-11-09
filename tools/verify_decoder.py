#!/usr/bin/env python3
"""Verify that the committed decompressor matches its source.

`worker/bin/blast_harness.exe` is a build artefact that ships in this repository
because end users should not need a C compiler to read their own design files.
Shipping a binary nobody can check is unpleasant, so this script rebuilds it and
compares.

Three things are checked, in order of how much they actually prove:

1. the build is deterministic - two builds from the same source agree;
2. the rebuilt binary behaves like the committed one on a real payload: both must
   produce the same decoded bytes (verify_decoder.py --probe), because a hash
   comparison alone cannot tell a stamped toolchain from a tampered binary;
3. the hashes match, which on MSVC only holds between builds stamped at the same
   moment.

MSVC writes a link timestamp into the PE header and does not honour /Brepro, so
step 3 legitimately fails whenever the committed file was built at a different
time. That is why the default verdict is behavioural.

    python tools/verify_decoder.py                 # no probe available
    python tools/verify_decoder.py --probe p.bin   # compare decoded output too

Needs MSVC on Windows: CI passes the compiler path explicitly, locally it is
found through vswhere.

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
SOURCES = ["blast_harness.c", "blast.c"]
HEADERS = ["blast.h"]
CFLAGS = ["/nologo", "/O2", "/Brepro", "/W3", "/D_CRT_SECURE_NO_WARNINGS"]


class BuildError(RuntimeError):
    pass


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    return digest.hexdigest()


def _vswhere_install() -> str:
    vswhere = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                           "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not os.path.isfile(vswhere):
        raise BuildError("vswhere.exe not found; pass --cl or set MSIM_VCVARS64")
    proc = subprocess.run([vswhere, "-latest", "-products", "*",
                           "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                           "-property", "installationPath"],
                          capture_output=True, text=True)
    path = proc.stdout.strip().splitlines()
    if not path:
        raise BuildError("no Visual Studio installation has the C++ x64 tools")
    return path[0]


def find_cl(explicit: str = "") -> str:
    if explicit:
        if not os.path.isfile(explicit):
            raise BuildError("cl.exe not found at %s" % explicit)
        return explicit
    return os.path.join(_vswhere_install(), "VC", "Tools", "MSVC")


def find_vcvars(explicit: str = "") -> str:
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        raise BuildError("vcvars64.bat not found at %s" % explicit)
    install = _vswhere_install()
    candidate = os.path.join(install, "VC", "Auxiliary", "Build", "vcvars64.bat")
    if not os.path.isfile(candidate):
        raise BuildError("vcvars64.bat not found under %s" % install)
    return candidate


def build(workdir: str, vcvars: str = "") -> str:
    """Compile into workdir and return the new executable's path."""
    os.makedirs(workdir, exist_ok=True)
    for name in SOURCES + HEADERS:
        source = os.path.join(BIN, name)
        if not os.path.isfile(source):
            raise BuildError("missing source %s" % source)
        shutil.copy2(source, os.path.join(workdir, name))

    vcvars = find_vcvars(os.environ.get("MSIM_VCVARS64", "") or vcvars)
    script = os.path.join(workdir, "build.bat")
    # A batch file, not one long `cmd /c "call ... && cl ..."` line: cmd.exe
    # re-parses the quotes and the exit code gets lost.
    with open(script, "w", encoding="ascii") as handle:
        handle.write("@echo off\r\n")
        handle.write('call "%s" >nul 2>nul\r\n' % vcvars)
        handle.write("if errorlevel 1 exit /b 3\r\n")
        handle.write("cl %s /Fe:blast_harness.exe %s\r\n"
                     % (" ".join(CFLAGS), " ".join(SOURCES)))
        handle.write("exit /b %errorlevel%\r\n")

    # decode with replacement: cl emits the localised code page, which explodes
    # a strict utf-8 decode on a Chinese Windows.
    proc = subprocess.run(["cmd", "/c", script], cwd=workdir, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    product = os.path.join(workdir, "blast_harness.exe")
    if proc.returncode != 0 or not os.path.isfile(product):
        output = (proc.stdout or "") + (proc.stderr or "")
        raise BuildError("cl failed (%d): %s" % (proc.returncode, output[-2000:]))
    return product


def decompress(exe: str, payload: str, outdir: str, label: str) -> dict:
    target = os.path.join(outdir, "out-%s.bin" % label)
    proc = subprocess.run([exe, payload, target], capture_output=True, timeout=120)
    if not os.path.isfile(target):
        return {"exit": proc.returncode,
                "stderr": proc.stderr.decode("utf-8", "replace")[-300:].strip()}
    return {"exit": proc.returncode, "bytes": os.path.getsize(target),
            "sha256": sha256(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", default="", help="expected sha256 of the committed exe")
    parser.add_argument("--probe", default="", help="a payload to compare behaviour on")
    parser.add_argument("--vcvars", default="", help="path to vcvars64.bat")
    parser.add_argument("--keep", action="store_true", help="keep the build directory")
    args = parser.parse_args()

    if not os.path.isfile(COMMITTED):
        print(json.dumps({"ok": False, "reason": "committed executable missing", "path": COMMITTED}))
        return 3

    committed = sha256(COMMITTED)
    # Build beside the repository, not in %TEMP%: a standalone executable is
    # denied write access to the temp directory in some sandboxes.
    workroot = tempfile.mkdtemp(prefix="_decodercheck-", dir=ROOT)
    try:
        first = build(os.path.join(workroot, "build1"), args.vcvars)
        second = build(os.path.join(workroot, "build2"), args.vcvars)
        first_sha = sha256(first)

        report = {
            "committed": {"sha256": committed, "bytes": os.path.getsize(COMMITTED)},
            "rebuilt": {"sha256": first_sha, "bytes": os.path.getsize(first)},
            "build_is_deterministic": first_sha == sha256(second),
            "identical_bytes": committed == first_sha,
            "matches_expectation": None if not args.expect else committed == args.expect.lower(),
        }
        if committed == first_sha:
            report["ok"] = True
            report["verdict"] = "reproducible: the committed binary is what this source builds"
            print(json.dumps(report, indent=2))
            return 0

        if not args.probe:
            report["ok"] = False
            report["verdict"] = ("bytes differ - most likely the link timestamp; "
                                 "pass --probe to compare behaviour")
            print(json.dumps(report, indent=2))
            return 1
        if not os.path.isfile(args.probe):
            print(json.dumps({"ok": False, "reason": "probe not found: %s" % args.probe}))
            return 3

        outdir = os.path.join(workroot, "out")
        os.makedirs(outdir, exist_ok=True)
        left = decompress(COMMITTED, args.probe, outdir, "committed")
        right = decompress(first, args.probe, outdir, "rebuilt")
        report["behaviour"] = {"committed": left, "rebuilt": right}
        identical = (left.get("sha256") is not None
                     and left.get("sha256") == right.get("sha256")
                     and left.get("bytes") == right.get("bytes"))
        report["ok"] = identical and report["build_is_deterministic"]
        if identical:
            report["verdict"] = ("behaviour identical, bytes differ: the toolchain stamped "
                                 "the binaries, which explains the hash difference")
        else:
            report["verdict"] = "MISMATCH: the committed binary does not behave like its source"
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    except BuildError as exc:
        print(json.dumps({"ok": False, "reason": "cannot build", "error": str(exc)[:3000]}))
        return 2
    finally:
        if not args.keep:
            shutil.rmtree(workroot, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
