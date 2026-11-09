---
name: multisim-file-tools
description: Read NI Multisim design files (.ms14/.msm/.ewb/.ms11/.ms12/.ms13) offline without starting Multisim, and run SPICE decks through a locally installed Multisim engine. Use when a task involves inspecting a Multisim project's components, values, instrument settings or schema; when a .ms14 must be read on a machine that has no Multisim; or when SPICE simulation is wanted on Windows and Multisim is the only simulator installed.
---

# Multisim file tools

> Unofficial third-party tool. Not affiliated with, authorised by, or endorsed by National Instruments / Emerson. It bundles no NI software, bypasses no licence check, and drives only a Multisim installation you own. See the repository `README.md` and `THIRD-PARTY-NOTICES.md`.

Two capabilities, one command line. Both print JSON on stdout.

```bash
python cli.py check                        # what can this machine do?
python cli.py design <file>                # read a design offline, no Multisim needed
python cli.py spice <deck> -c "<command>"  # run a SPICE deck through Multisim
python cli.py netlist <deck> [-o out.cir]  # normalise a deck for opening as a schematic
```

Run `python cli.py check` first, once per session. It reports interpreter bits, which modules imported, whether the decompressor is present, and whether Multisim was found. If it says `"installed": false`, `spice` cannot work on this machine but `design` still can.

## Which capability do you need?

| The task is | Use | Why |
| --- | --- | --- |
| "What is in this Multisim project?" | `design` | Pure Python + one bundled executable. No Multisim, no COM, no window. |
| "Read this .ms14 on a Linux/Mac box or a machine without Multisim" | `design` | It only needs Python 3.9+ and the file itself. |
| "Simulate this circuit and give me numbers or a plot" | `spice` | Borrows Multisim's own SPICE3 engine. Needs Multisim installed. |
| "I want an editable schematic, not a simulation" | `netlist`, then open the `.cir` in Multisim | A netlist only becomes a schematic when Multisim itself opens it. |

## `design` — reading a design file

```bash
python cli.py design "C:\path\to\project.ms14"
python cli.py design project.ms14 --refdes R1,R2,U1A --pretty
python cli.py design project.ms14 --xml --xml-limit 50000
```

Returns `components[]`, `instruments[]`, `counts`, and the `creator` block:

```json
{
  "counts": {"items": 3951, "wires": 166, "symbols": 96, "pins": 82,
             "parts": 6, "instruments": 1},
  "components": [
    {"refdes": "U1A", "class": "CIITSymbolComp", "type": null, "sections": 2,
     "value": "7420N", "model": null, "parameters": []},
    {"refdes": "V1", "class": "CIITSymbolComp", "type": null, "sections": 1,
     "value": "1kHz 5V", "model": null,
     "parameters": ["0", "1k", "0", "50", "0", "5", "0", "1n", "0", "1n"]}
  ],
  "creator": {"application": "multisim", "versionMajor": "14",
              "versionMinor": "3", "date": "2026-10-18T18:02:36.000Z"}
}
```

Reading the output correctly:

- `counts.parts` excludes instruments; `counts.instruments` counts them separately. A design with 19 instruments and no placed parts is normal — somebody left every instrument on one sheet.
- `value` is the display string the schematic shows (`1kHz 5V`). `parameters` is the **authoritative** parameter array. When the two disagree, `parameters` wins: Multisim keeps a stale copy in the symbol text after a value edit.
- `parameters` is positional and `0`-heavy. The device value is usually the single non-zero entry; for a source, the first non-zero entry is the amplitude. Do not try to name every position.
- Instrument entries carry `kind` and (with `--settings`) a `settings` map naming `NI_EWB_*` keys. These are real analyser settings — trigger position, sample counts, thresholds — not cosmetics.
- A value of the form `_uc<hex>` cannot be recovered. Those are process-local pointer numbers written into the file, not an encoding. Report them as unrecoverable rather than guessing.
- Designators are `refdes` (`R1`, `U1A`); the XML's internal names (`&UNI!0!0!0XLA1!0…`) are noise. Never show them to a user.

`--xml` emits the decoded document, which for a large design is megabytes. Ask for it only when the fields above are not enough, and cap it with `--xml-limit`.

## `spice` — running a deck through Multisim

```bash
python cli.py spice examples\rc_lowpass.cir -c "op" -c "tran 10u 2m" -c "ac dec 10 10 100k"
python cli.py spice mydeck.cir -c "dc V1 0 5 0.1" --output-dir C:\runs\run1
python cli.py spice - -c "op" < mydeck.cir
```

Each `-c` is one analysis. The default is `op`. Supported SPICE3 subset:

| Kind | Form | Notes |
| --- | --- | --- |
| Operating point | `op` | One row per node. |
| Transient | `tran <step> <stop>` | Multisim picks its own internal step, so expect more rows than `stop/step`. |
| DC sweep | `dc <source> <start> <stop> <inc>` | Nested sweeps work too. |
| AC | `ac dec|oct|lin <n> <fstart> <fstop>` | Needs `AC <mag>` on a source, otherwise "No sources with AC parameters were found". |
| Transfer function | `tf <out> <src>` | |
| Noise | `noise v(<node>) <src> dec <n> <f1> <f2>` | |

Behaviour worth knowing before you write a deck:

- **Line 1 of a deck is a title, not an element.** Multisim treats it as free text, so words in it become node names: a title `RC low-pass` creates a floating node called `low-pass` and every analysis dies with `Matrix is singular`. Start the file with `* anything` or keep the title to one harmless line.
- **Every node needs a DC path to ground.** A capacitor in series with the input, or a current source feeding a lone node, gives a singular matrix.
- **Give sources an `AC` value** if you want an AC sweep, and a `DC` value if you want a meaningful operating point: `V1 in 0 DC 0 PULSE(0 5 0 1n 1n 0.5m 1m) AC 1`.
- **Library parts do not exist here.** This path runs raw SPICE3, so `74LS00`, `OPAMP_5T` and `D_CHIP` are unknown. Model logic with a behavioural source instead: `B1 y 0 V = 5 - 5*((V(a)>2.5) & (V(b)>2.5))` is a NAND gate.
- **Output paths must not contain spaces.** Multisim's command engine rejects command-file paths with spaces, so `--output-dir` with a space raises `BAD_ARGS`. Pick e.g. `C:\runs\run1`.

The result carries one object per analysis:

```json
{"command": "tran 10u 2m", "plotname": "Transient Analysis",
 "columns": ["time", "V(in)", "V(out)", "I(V1)"],
 "n_points": 290, "sampled_step": 1,
 "csv": {"path": "...\\plot1.csv", "bytes": 14542},
 "png": {"path": "...\\plot1.png", "bytes": 9201},
 "summary": [...]}
```

- `n_points` is the engine's row count; the returned `rows` (only with `--rows`) are sampled every `sampled_step` rows, so do not assume one row per requested sample.
- Read `csv.path` when you need the full data. It is written next to the deck run, not truncated.
- `png.path` is a chart rendered from the returned data — it is **not** a screenshot of Multisim. Never describe it as one.
- `missing[]` lists analyses that produced no plot, with the engine's own reason. A singular matrix lands here, not in an exception.
- The first call costs about six seconds because it starts Multisim through COM. Later calls in the same process are fast; separate invocations pay it again.

## `netlist` — from deck to schematic

```bash
python cli.py netlist circuit.cir -o C:\work\circuit.cir
```

It normalises the deck and lists the elements it parsed. To turn that into an editable schematic, open the `.cir` in Multisim (drag it onto the window, or File > Open): Multisim rebuilds real library parts and wires from it. This skill cannot do that step for you — it has no window control.

## Failure codes

| `error.code` | Meaning | What to do |
| --- | --- | --- |
| `PATH_MISSING` | The file is not there | Check the path; quote paths with spaces. |
| `BAD_ARGS` | Not a Multisim design, or a path with spaces | `design` needs a `.ms14`-family file; output paths must have no spaces. |
| `DECODE_ERROR` | The container could not be decompressed | The file is truncated or corrupt. `detail` carries the decoder's own words. |
| `NO_PYWIN32` | `spice` ran without a 32-bit pywin32 | Use a 32-bit Python 3.11–3.13 with `pip install pywin32`; `design` works regardless. |
| `NO_MULTISIM` | COM could not start Multisim | Multisim 14.3 is not installed or is broken. `design` still works. |
| `COM_ERROR` | The engine refused the deck | Read `detail`: it is the SPICE log, including the exact line Multisim objected to. |

## Reporting results

When writing up findings, keep these two apart:

- Data produced by `spice` is **simulation output**. Charts are rendered from that data. Say so.
- Data produced by `design` is **file content**. It describes what somebody drew, not what the circuit does.

Neither is a screenshot of Multisim. If a user needs a screenshot of the application itself, that is a different tool.
