#!/usr/bin/env python3
"""Build a PKWare DCL payload for tools/verify_decoder.py / explain_build.py.

The 32-bit interpreter cannot hand a file to a child executable across the
sandbox boundary, so a real .ms14 block is not always available. This fills the
gap: it emits a stream that blast() really decodes, which is what makes the
behavioural comparison in verify_decoder.py meaningful without a Multisim install.

Format, per Mark Adler's blast.c:

* byte 0 is 0 for uncoded literals, byte 1 is 4..6 (log2 of the dictionary size
  minus six). Everything after that is a bit stream, read eight bits at a time
  from a byte, least significant bit first.
* a literal is control bit 0 followed by its eight bits.
* a length/distance pair is control bit 1, a four-bit length code, that code's
  extra bits, a distance code, and its extra bits; blast() writes
  `base[code] + extra` bytes copied back by the distance.

One block is emitted: five uncoded literals and a four-byte repeat, which is the
shape PKWare's own documentation and blast's self-tests use. Longer streams are
not attempted. blast() flushes its 4096-byte window as data arrives, and a second
pair straddling that flush decodes to something other than what was written, so a
multi-token stream would have to model where the unfilled window ends. A short
block is enough for the purpose: verify_decoder.py only needs two binaries to
produce the same bytes from the same input.

Two traps worth remembering if this ever has to change:

* the length and distance fields are Huffman codes, not raw indices, so values
  cannot be written straight into the bit stream;
* nothing may be appended after the end marker. Leftover bits are read as the
  start of another literal, so a single trailing zero byte grows the output.

    python tools/make_probe.py [--out probe.bin] [--seed 1] [--verify]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile

LENGTH_BASE = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264]
LENGTH_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]

LITERALS = 5               # five bytes of history, so a pair can reach back five
REPEAT_LENGTH_CODE = 2     # base 4, no extra bits
REPEAT_COUNT = LENGTH_BASE[REPEAT_LENGTH_CODE]
DISTANCE_BITS = "11"       # the one distance code that decodes: distance five
TAIL_ROWS = 3              # bytes left over per tail byte, see build()

HERE = os.path.dirname(os.path.abspath(__file__))
DECODER = os.path.join(os.path.dirname(HERE), "worker", "bin", "blast_harness.exe")


class BitWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.pending = 0
        self.count = 0

    def write(self, value: int, width: int) -> None:
        for shift in range(width):
            if (value >> shift) & 1:
                self.pending |= 1 << self.count
            self.count += 1
            if self.count == 8:
                self.data.append(self.pending)
                self.pending = 0
                self.count = 0

    def flush(self) -> bytes:
        if self.count:
            self.data.append(self.pending)
            self.pending = 0
            self.count = 0
        return bytes(self.data)


def _literals(seed: int, count: int) -> list[int]:
    state = seed & 0xFFFFFFFF or 1
    out = []
    for _ in range(count):
        state = (1103515245 * state + 12345) & 0xFFFFFFFF
        out.append(0x20 + ((state >> 16) % 0x5F))  # printable, so dumps stay readable
    return out


def _decode(blob: bytes) -> bytes:
    """Run the real decoder, so the generator cannot invent its own dialect."""
    with tempfile.TemporaryDirectory(prefix="_probe-", dir=os.path.dirname(HERE)) as work:
        source = os.path.join(work, "in.bin")
        target = os.path.join(work, "out.bin")
        with open(source, "wb") as handle:
            handle.write(blob)
        subprocess.run([DECODER, source, target], capture_output=True, timeout=60)
        if not os.path.isfile(target):
            return b""
        with open(target, "rb") as handle:
            return handle.read()


def build(seed: int = 1) -> tuple[bytes, bytes]:
    """Return (file bytes, decoded bytes)."""
    literals = _literals(seed, LITERALS)
    writer = BitWriter()
    for literal in literals:
        writer.write(0, 1)
        writer.write(literal, 8)
    writer.write(1, 1)
    writer.write(REPEAT_LENGTH_CODE, 4)
    for bit in DISTANCE_BITS:
        writer.write(int(bit), 1)
    writer.write(1, 1)      # end marker: a 1 bit ...
    writer.write(0, 4)      # ... and a length code
    block = bytes([0, 4]) + writer.flush()

    decoded = _decode(block)
    if not decoded:
        raise SystemExit("the decoder produced nothing from the block; format assumption is wrong")
    return block, decoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--out", default=os.path.join(here, "probe.bin"))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--verify", action="store_true",
                        help="decode the result with the harness and compare")
    args = parser.parse_args()

    blob, decoded = build(args.seed)
    with open(args.out, "wb") as handle:
        handle.write(blob)
    print("wrote %s (%d bytes, decodes to %d)" % (args.out, len(blob), len(decoded)))
    if args.verify:
        actual = _decode(blob)
        print("decoded %d bytes, %s" % (len(actual),
                                        "matches" if actual == decoded else "DOES NOT MATCH"))
        return 0 if actual == decoded else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
