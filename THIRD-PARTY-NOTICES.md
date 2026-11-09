# Third-party notices

This project is MIT licensed (see `LICENSE`). It contains or invokes the
following third-party components.

## blast — PKWare DCL decompressor

`worker/bin/blast.c` and `worker/bin/blast.h` are Mark Adler's `blast` library,
as shipped in the zlib contributions directory. They are included verbatim so
that `worker/bin/blast_harness.exe` can be inspected and rebuilt from source
rather than trusted as an opaque binary.

```
Copyright (C) 2003 Mark Adler
For conditions of distribution and use, see copyright notice in blast.h
```

The relevant permission is reproduced by `blast.h` itself: use, copy and
distribute for any purpose, provided the notice is retained and the author is
not misrepresented as the origin of the software.

`worker/bin/blast_harness.c` is original work in this project and is MIT
licensed like the rest of it.

### Is the shipped binary the one this source produces?

`tools/verify_decoder.py` recompiles `blast_harness.c` + `blast.c` with MSVC,
builds a payload with `tools/make_probe.py`, and requires the committed
`worker/bin/blast_harness.exe` and the rebuild to decode it to the same bytes:

```bash
python tools/make_probe.py --out probe.bin --verify
python tools/verify_decoder.py --probe probe.bin
```

Comparing hashes alone would prove less than it appears to. The check is
behavioural on purpose: two builds from this source on one machine are
byte-identical, but a different MSVC release emits different machine code, so the
committed file (145408 bytes, sha256 `d2d7cd6917ff9e7b4dfa5ef08009bc599198af291db4ffb982237a4a2244facb`)
never matches a rebuild elsewhere — 6305 bytes differ from a VS 2022 rebuild here,
and only four of those are the PE link timestamp. What matters is that both
binaries turn the same compressed input into the same output, which is what CI
asserts on every push. `tools/explain_build.py` prints the full breakdown
(build determinism, link timestamp, Rich header, then the behaviour comparison)
if you want to see it yourself.

To rebuild and replace the committed binary anyway:

```powershell
$env:MSIM_VCVARS64 = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
python tools/verify_decoder.py --keep   # inspect, then copy the rebuilt exe if you prefer it
```

or by hand (MSVC):

```bat
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
cd worker\bin
cl /nologo /O2 /Brepro /Fe:blast_harness.exe blast_harness.c blast.c
```

## National Instruments / Emerson — Multisim

This project is **not** affiliated with, authorised by, sponsored by, or
endorsed by National Instruments, Emerson, or any of their subsidiaries. "NI",
"National Instruments", "Multisim" and "Ultiboard" are trademarks of their
respective owners and are used here only to describe what this tool
interoperates with.

* No NI software is included, bundled, redistributed or modified by this
  project. The `spice` subcommand starts a Multisim installation that the user
  already owns and has licensed, by calling its documented COM automation
  interface.
* No licence check, activation, dongle or serial validation is bypassed,
  circumvented or interfered with.
* The `design` subcommand reads a container format. Design files are opened
  read-only; nothing in this project writes, repairs or migrates a `.ms14`.

## File format compatibility

`design` decompresses the container that Multisim design files use, in order to
let people read their own project files, on any machine, without launching the
application. This is a compatibility shim, in the same spirit as a file-format
reader for any other application's documents. It contains no NI code and
reverses no protection: the container is plain compression with no encryption,
no access control and no key material, and the decompressor used is a public
general-purpose library (see above).

If you are a rights holder and object to this interoperability work, open an
issue in the repository and it will be addressed.

## Data directories

This tool reads from a Multisim installation and from Multisim's data
directories, and it never writes to them:

* `MSCOMP_S.PRD` and the other database files under
  `C:\ProgramData\National Instruments\Circuit Design Suite\14.3\database\`
  are opened read-only, and in practice not even opened by this tool — COM
  handles them inside Multisim.
* Design files under `C:\Users\<you>\Documents\National Instruments\Circuit
  Design Suite 14.3\` are only ever read.
* The only paths this project writes are the ones you pass on the command line
  (`--output-dir`, `-o`) plus a scratch directory for the decompressor, chosen
  from `MSIM_MS14_WORKDIR`, `%LOCALAPPDATA%\msim-ms14-tmp`, `_ms14_tmp` next to
  the package, or `%TEMP%` — whichever the decompressor proves it can write to.
