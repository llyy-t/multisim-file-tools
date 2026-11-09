#!/usr/bin/env python3
"""Dissect the difference between the committed decompressor and a fresh build.

`tools/verify_decoder.py` asks whether the shipped binary can be trusted;
`tools/make_probe.py` supplies a payload it can be tested on. This script is the
forensic view, for when someone asks "why are the hashes different at all?".

It reports:

1. whether two builds from the same source on this machine agree;
2. which bytes differ between the committed binary and a rebuild, split into the
   PE link timestamp, the Rich header, and everything else (code);
3. what both binaries produce from the same payload.

Design note: a differing Rich header or timestamp is cosmetic. Differing code
(within .text) means the toolchain, not just the stamp, produced different
machine code - the two binaries can still be functionally identical, which is
what step 3 decides. Neither finding implies tampering on its own.

    python tools/explain_build.py
    python tools/explain_build.py --probe probe.bin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "worker", "bin")
COMMITTED = os.path.join(BIN, "blast_harness.exe")

sys.path.insert(0, HERE)
from verify_decoder import BuildError, build, decompress, sha256  # noqa: E402


def pe_layout(path: str) -> dict:
    """Locate the two machine-specific stamps in a PE image."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:2] != b"MZ":
        raise ValueError("%s is not a PE image" % path)
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("%s has no PE signature at 0x%X" % (path, pe))
    layout = {
        "link_timestamp_offset": pe + 8,
        "link_timestamp": struct.unpack_from("<L", data, pe + 8)[0],
        "rich_header": None,
    }
    marker = data.find(b"Rich", max(pe - 0x200, 0), pe)
    if marker != -1 and marker + 8 <= len(data):
        key = struct.unpack_from("<L", data, marker + 4)[0]
        for offset in range(marker - 4, 0, -4):
            if struct.unpack_from("<L", data, offset)[0] ^ key == 0x536E6144:
                layout["rich_header"] = {"start": offset, "end": marker + 8, "key": key}
                break
    return layout


def diff_profile(left: str, right: str) -> dict:
    with open(left, "rb") as handle:
        a = handle.read()
    with open(right, "rb") as handle:
        b = handle.read()
    if len(a) != len(b):
        return {"same_size": False, "size_a": len(a), "size_b": len(b)}

    layout = pe_layout(left)
    stamp = layout["link_timestamp_offset"]
    rich = layout["rich_header"]
    differing = [index for index in range(len(a)) if a[index] != b[index]]
    in_stamp = [i for i in differing if stamp <= i < stamp + 4]
    in_rich = ([i for i in differing if rich["start"] <= i < rich["end"]]
               if rich else [])
    rest = [i for i in differing if i not in in_stamp and i not in in_rich]
    return {
        "same_size": True,
        "size": len(a),
        "differing_bytes": len(differing),
        "in_link_timestamp": len(in_stamp),
        "in_rich_header": len(in_rich),
        "elsewhere": len(rest),
        "first_elsewhere": rest[0] if rest else None,
    }


def run(probe: str, keep: bool) -> int:
    workroot = tempfile.mkdtemp(prefix="_buildcheck-", dir=ROOT)
    try:
        first_dir = os.path.join(workroot, "build1")
        second_dir = os.path.join(workroot, "build2")
        outdir = os.path.join(workroot, "out")
        for path in (first_dir, second_dir, outdir):
            os.makedirs(path)

        first = build(first_dir)
        second = build(second_dir)

        report = {
            "committed": {"sha256": sha256(COMMITTED), "bytes": os.path.getsize(COMMITTED)},
            "rebuilt_a": {"sha256": sha256(first), "bytes": os.path.getsize(first)},
            "rebuilt_b": {"sha256": sha256(second), "bytes": os.path.getsize(second)},
            "build_is_deterministic": sha256(first) == sha256(second),
            "layout": {"committed": pe_layout(COMMITTED), "rebuilt": pe_layout(first)},
            "difference": {
                "committed_vs_rebuilt": diff_profile(COMMITTED, first),
                "rebuilt_vs_rebuilt": diff_profile(first, second),
            },
        }
        if probe:
            report["probe"] = {"path": probe, "bytes": os.path.getsize(probe)}
            left = decompress(COMMITTED, probe, outdir, "committed")
            right = decompress(first, probe, outdir, "rebuilt")
            report["behaviour"] = {"committed": left, "rebuilt": right}
            report["identical_behaviour"] = bool(left.get("sha256")
                                                and left.get("sha256") == right.get("sha256"))
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("build_is_deterministic", False) else 1
    finally:
        if not keep:
            shutil.rmtree(workroot, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", default="",
                        help="payload to compare behaviour on (see tools/make_probe.py)")
    parser.add_argument("--keep", action="store_true", help="keep the build directory")
    args = parser.parse_args()

    if args.probe and not os.path.isfile(args.probe):
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
