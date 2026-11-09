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

`tools/verify_decoder.py` recompiles `blast_harness.c` + `blast.c` with MSVC and
compares the result against the committed `worker/bin/blast_harness.exe`:

```bash
python tools/verify_decoder.py
```

MSVC's linker writes a timestamp into the PE header, so a plain hash comparison
is only expected to agree on toolchains that honour `/Brepro`. When the hashes
differ the script does not stop there — it runs both executables over the same
real compressed block and compares the decoded bytes, which is the property that
actually matters. `tools/explain_build.py` shows the difference in detail (build
determinism, link timestamps, then the same behavioural comparison).

As of the current commit the two binaries have different hashes and **identical
decompressed output** for every block of a real `.ms14`: the build is
deterministic on this machine (two builds agree with each other) but differs from
the committed artefact by the link timestamp. CI runs the rebuild on every push.

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
