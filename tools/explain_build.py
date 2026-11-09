#!/usr/bin/env python3
"""Explain why two builds of blast_harness.exe differ, and prove they behave the same.

`tools/verify_decoder.py` recompiles the committed decompressor and compares
SHA-256. When the hashes disagree, the honest question is not "which binary is
right" but "do they do the same thing". This script answers that:

1. build twice with identical flags, to show whether the build itself is
   deterministic on this machine;
2. compare each binary's PE link timestamp, which a date-stamped toolchain
   changes on every build even when the code is identical;
3. run the committed and the rebuilt executable over a real compressed block and
   compare the decoded bytes.

Step 3 is the one that matters: unequal hashes plus identical output means a
nondeterministic toolchain, not a tampered binary.

    python tools/explain_build.py --probe <compressed block from a .ms14>

Note when reading the output: this sandbox denies write access to %TEMP% for
standalone executables (error 5), so pass a --probe that is already extracted and
let the script write under the repository if the temporary directory fails.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "worker", "bin")
COMMITTED = os.path.join(BIN, "blast_harness.exe")

sys.path.insert(0, HERE)
from verify_decoder import BuildError, build, sha256  # noqa: E402


def link_timestamp(path: str) -> int:
    with open(path, "rb") as handle:
        data = handle.read(0x400)
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<L", data, pe + 8)[0]


def decompress(exe: str, payload: str, outdir: str, label: str) -> dict:
    target = os.path.join(outdir, "out-%s.bin" % label)
    proc = subprocess.run([exe, payload, target], capture_output=True, timeout=120)
    info = {
        "exit": proc.returncode,
        "stderr_tail": proc.stderr.decode("utf-8", "replace")[-200:].strip(),
    }
    if os.path.isfile(target):
        info["bytes"] = os.path.getsize(target)
        info["sha256"] = sha256(target)
    else:
        info["bytes"] = 0
    return info


def run(probe: str, keep: bool) -> int:
    # Write outputs beside the repository, not in %TEMP%: a standalone exe is
    # denied write access to the temporary directory in this environment.
    workroot = tempfile.mkdtemp(prefix="_buildcheck-", dir=ROOT)
    try:
        first_dir = os.path.join(workroot, "build1")
        second_dir = os.path.join(workroot, "build2")
        outdir = os.path.join(workroot, "out")
        for path in (first_dir, second_dir, outdir):
            os.makedirs(path)
        first = build(first_dir)
        second = build(second_dir)

        committed_behaviour = decompress(COMMITTED, probe, outdir, "committed")
        rebuilt_behaviour = decompress(first, probe, outdir, "rebuilt")
        identical = (
            committed_behaviour.get("sha256") is not None
            and committed_behaviour.get("sha256") == rebuilt_behaviour.get("sha256")
            and committed_behaviour.get("bytes") == rebuilt_behaviour.get("bytes")
        )

        report = {
            "committed": {"sha256": sha256(COMMITTED), "bytes": os.path.getsize(COMMITTED)},
            "rebuilt_a": {"sha256": sha256(first), "bytes": os.path.getsize(first)},
            "rebuilt_b": {"sha256": sha256(second), "bytes": os.path.getsize(second)},
            "build_is_deterministic": sha256(first) == sha256(second),
            "link_timestamps": {
                "committed": link_timestamp(COMMITTED),
                "rebuilt_a": link_timestamp(first),
                "rebuilt_b": link_timestamp(second),
            },
            "behaviour": {"committed": committed_behaviour, "rebuilt": rebuilt_behaviour},
            "identical_behaviour": identical,
        }
        print(json.dumps(report, indent=2))
        return 0 if identical else 1
    finally:
        if not keep:
            shutil.rmtree(workroot, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True, help="a compressed block extracted from a .ms14")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    if not os.path.isfile(args.probe):
        print(json.dumps({"ok": False, "reason": "probe not found: %s" % args.probe}))
        return 3
    if not os.path.isfile(COMMITTED):
        print(json.dumps({"ok": False, "reason": "committed executable missing"}))
        return 3
    try:
        return run(args.probe, args.keep)
    except BuildError as exc:
        print(json.dumps({"ok": False, "reason": "cannot build", "error": str(exc)[:3000]}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
