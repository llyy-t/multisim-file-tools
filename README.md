# multisim-file-tools

Read NI Multisim design files without opening Multisim, and run SPICE decks
through a Multisim installation you already own.

This is an **agent skill** (a `SKILL.md` plus scripts) and a plain command-line
tool. Both halves are in the same box: the model reads `SKILL.md`, the tools run
`cli.py`.

```bash
python cli.py check                        # what can this machine do?
python cli.py design project.ms14          # read a design offline
python cli.py spice deck.cir -c "tran 10u 2m"   # simulate through Multisim
python cli.py netlist deck.cir -o deck.cir # normalise a deck for schematic import
```

Every subcommand prints JSON on stdout and exits 0 on success, 1 on a
`{"ok": false, "error": {...}}` payload, 2 on a usage mistake.

## Why this exists

Two jobs that are awkward to do any other way:

**Reading a Multisim project on a machine that cannot run Multisim.** A `.ms14`
is a PKWare-DCL compressed XML container. `design` decompresses it with a
bundled decoder and reports the parts, values, parameter arrays, instrument
settings and creator/version metadata. No Multisim, no Windows-only COM, no
16-bit-era weirdness — just Python and the file.

**Simulating without drawing anything.** `spice` writes a Multisim command file
and drives `DoCommandLine` over COM. Multisim's own SPICE3 engine does the work
and the tool parses the ASCII raw output back into a table, a CSV and a chart.
Useful when you want a quick operating point, sweep or transient on a circuit
that only exists as a netlist.

What it deliberately does **not** do: it never types into the Multisim window,
never clicks menus, never takes screenshots, and never saves a design. There is
no GUI automation here at all. If you need the application driven by mouse and
keyboard, that is a different tool.

## Requirements

| Subcommand | Needs |
| --- | --- |
| `design`, `netlist` | Python 3.9+. Nothing else. Works on Windows, Linux, macOS. |
| `spice` | Windows + Multisim 14.3 installed + a **32-bit** Python 3.11–3.13 with `pywin32`. |
| `check` | Nothing; it tells you which of the above you have. |

The 32-bit requirement is not a choice: Multisim's automation server is a 32-bit
COM server, and a 64-bit Python cannot connect to it.

```powershell
# only needed for `spice`
C:\path\to\Python313-32\python.exe -m pip install pywin32
```

`python cli.py check` prints the interpreter it is running under, which modules
loaded, whether the bundled decompressor is present and executable, whether
Multisim was found, and a list of `advice` strings for anything missing. Run it
first whenever something behaves oddly.

## Install as an agent skill

Drop the directory where your agent looks for skills, or point the agent at
`SKILL.md`. Claude Code, for example:

```bash
git clone https://github.com/llyy-t/multisim-file-tools
cp -r multisim-file-tools ~/.claude/skills/
```

The skill's own entry point is `SKILL.md` — it is written for a model, not for a
human, and covers the output shape, the failure codes and the traps.

## Usage

### `design` — read a design file

```bash
python cli.py design "C:\Users\me\Documents\National Instruments\Circuit Design Suite 14.3\project.ms14"
python cli.py design project.ms14 --refdes R1,R2,U1A --pretty
python cli.py design project.ms14 --xml --xml-limit 50000
```

```
{
  "ok": true,
  "file": "...\\project.ms14",
  "creator": {"application": "multisim", "versionMajor": "14", "versionMinor": "3",
              "date": "2026-10-18T18:02:36.000Z"},
  "counts": {"items": 3951, "wires": 166, "symbols": 96, "pins": 82,
             "parts": 6, "instruments": 1},
  "components": [
    {"refdes": "U1A", "sections": 2, "value": "7420N", "parameters": []},
    {"refdes": "V1",  "sections": 1, "value": "1kHz 5V",
     "parameters": ["0","1k","0","50","0","5","0","1n","0","1n"]}
  ],
  "instruments": [{"refdes": "XLA1", "kind": "logic analyzer", "settings": {...}}]
}
```

`value` is what the schematic displays; `parameters` is the authoritative
parameter array. When they disagree, believe `parameters`. Values of the form
`_uc<hex>` are process-local pointer numbers baked into the file and cannot be
recovered — the tool reports them as unrecoverable instead of guessing.

### `spice` — simulate through Multisim

```bash
python cli.py spice examples/rc_lowpass.cir -c "op" -c "tran 10u 2m" -c "ac dec 10 10 100k"
python cli.py spice deck.cir -c "dc V1 0 5 0.1" --output-dir C:\runs\run1
python cli.py spice - -c "op" < deck.cir
```

The first call takes about six seconds because it starts Multisim. Each
`-c` is one analysis; the default is `op`. Supported: `op`, `tran`, `dc`, `ac`,
`tf`, `noise`. Per analysis you get the column names, the row count, a path to
a CSV with the full data, and a path to a PNG chart rendered from that data.

Things that bite, all of them documented in `SKILL.md`:

* **Line 1 of a deck is a title, not an element.** A title like `RC low-pass`
  creates a floating node and every analysis dies with `Matrix is singular`.
  Start the file with `* `.
* **Sources need `AC <mag>`** for an AC sweep, and a `DC` value for a
  meaningful operating point.
* **Library parts do not exist here.** This path runs raw SPICE3, so `74LS00`
  and `OPAMP_5T` are unknown. Use behavioural sources for logic.
* **Output paths must not contain spaces.** Multisim's command engine rejects
  command-file paths with spaces.

### `netlist` — deck to schematic

```bash
python cli.py netlist circuit.cir -o C:\work\circuit.cir
```

Normalises a deck and lists what it parsed. To get an editable schematic, open
that `.cir` in Multisim — it rebuilds library parts and wiring from it. This
tool cannot do that step itself, because it does not drive the application.

## Repository layout

```
cli.py                    command line entry point, JSON in/out
SKILL.md                  the agent-facing instructions
worker/msim_ms14.py       .ms14 container: decompress, parse, summarise
worker/msim_spice.py      SPICE deck parsing, ASCII raw parsing, CSV, plotting
worker/msim_raster.py     PNG canvas and bitmap font, for charts
worker/msim_deck.py       deck + analyses -> commands -> raw -> tables
worker/msim_com.py        Multisim COM session (only imported by `spice`)
worker/bin/blast*.c/.h    PKWare DCL decompressor (Mark Adler, zlib licence)
worker/bin/blast_harness.exe   thin Win32 wrapper around blast
examples/rc_lowpass.cir   the deck used in the examples above
```

## Relation to `multisim-toolkit`

[`llyy-t/multisim-toolkit`](https://github.com/llyy-t/multisim-toolkit) is the
full plugin: it registers tools inside a DeepSeek Harness agent, drives the
Multisim window with Win32 messages, launches analyses from the menu, captures
real screenshots and waveforms, and saves designs. That one is Windows-only,
Harness-only, and does everything.

This repository is the portable, free subset — the two pieces that do not need
a window (file reading) or that can ride on the COM API (simulation). It shares
the `worker/` modules with the plugin; anything added here has to keep the
plugin working too.

## Licence and disclaimer

MIT, see `LICENSE`. Third-party components and the National Instruments
position are in `THIRD-PARTY-NOTICES.md`.

**Unofficial.** Not affiliated with, authorised by, or endorsed by National
Instruments / Emerson. "Multisim" and "NI" are their trademarks, used only to
say what this tool talks to. No NI software is included or redistributed; the
`spice` subcommand drives a Multisim you own through its documented COM
automation interface, and no licence or activation mechanism is bypassed. The
`design` subcommand only reads files you point it at, read-only.
