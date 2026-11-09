#!/usr/bin/env python3
"""msim-cli -- read Multisim design files and run SPICE decks, from the command line.

Two capabilities, both usable without a schematic and without driving a window:

    msim-cli design <file.ms14>          decode a design offline, print what is in it
    msim-cli spice  <deck.cir> [options]  run a SPICE deck through the installed Multisim

Everything is JSON in, JSON out, so an agent can call it without guessing at
prose output. ``--pretty`` is for humans.

Why a command line rather than a library: the caller is an agent following a
SKILL.md, and a process boundary is the only interface every agent runtime has
in common.

Exit codes
    0   success
    1   the operation failed (details in the JSON on stdout)
    2   bad command line
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from typing import Any, Dict, List, Optional

VERSION = "1.0.0"

# The worker modules live next to this file.  Keep the import local so that a
# broken installation produces a readable message instead of an ImportError
# traceback before argparse has had a chance to run.
HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "worker")
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class CliError(Exception):
    """A failure worth reporting as structured data rather than a traceback."""

    def __init__(self, code: str, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


def _emit(payload: Any, pretty: bool = False) -> None:
    """Write one JSON document to stdout.

    ``ensure_ascii=False`` keeps Chinese design names readable; the stream is
    forced to UTF-8 below so a Windows console in CP936 does not mangle them.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _fail(error: CliError, pretty: bool = False) -> int:
    _emit({"ok": False, "error": error.to_dict()}, pretty)
    return EXIT_FAIL


# --------------------------------------------------------------------------
# design -- offline .ms14 reader
# --------------------------------------------------------------------------


def cmd_design(args: argparse.Namespace) -> int:
    try:
        import msim_ms14
    except Exception as exc:  # pragma: no cover - installation damage
        raise CliError(
            "INTERNAL",
            "the .ms14 reader could not be imported",
            "%s\n\nexpected worker modules under %s" % (exc, WORKER),
        )

    try:
        payload = msim_ms14.summary(
            args.file,
            include_xml=bool(args.xml),
            xml_limit=int(args.xml_limit),
        )
    except msim_ms14.Ms14Error as exc:
        raise CliError(exc.code, exc.message, exc.detail)

    payload = dict(payload)
    payload["ok"] = True
    if args.refdes:
        wanted = {name.strip().upper() for name in args.refdes.split(",") if name.strip()}
        payload["components"] = [
            item for item in payload.get("components", [])
            if str(item.get("refdes") or "").upper() in wanted
        ]
    if not args.settings:
        for instrument in payload.get("instruments", []):
            instrument.pop("settings", None)
    _emit(payload, args.pretty)
    return EXIT_OK


# --------------------------------------------------------------------------
# spice -- SPICE deck through the installed Multisim
# --------------------------------------------------------------------------


def _session(config: Optional[str]) -> Any:
    """Build a Multisim COM session.

    pywin32 is imported here, not at module scope: the ``design`` subcommand has
    to keep working on a machine with no pywin32 and no Multisim at all.
    """
    try:
        import msim_com
    except Exception as exc:
        raise CliError(
            "NO_PYWIN32",
            "the SPICE subcommand needs pywin32 on a 32-bit Python",
            "import failed with: %s\n\n"
            "Multisim's Automation server is 32-bit, so a 64-bit interpreter gets "
            "E_ACCESSDENIED on Connect(). Install a 32-bit Python 3.11-3.13 and run "
            "'python -m pip install pywin32' with it." % exc,
        )
    try:
        session = msim_com.MultisimSession(config=config) if config else msim_com.MultisimSession()
    except TypeError:
        session = msim_com.MultisimSession()
    try:
        session.connect()
    except Exception as exc:
        text = str(exc)
        if "pywin32" in text or "win32com" in text or "No module named" in text:
            raise CliError(
                "NO_PYWIN32",
                "the SPICE subcommand needs pywin32 on a 32-bit Python",
                "%s\n\nMultisim's Automation server is 32-bit, so a 64-bit "
                "interpreter cannot connect to it. Install a 32-bit Python "
                "3.11-3.13 and run 'python -m pip install pywin32' with it. The "
                "'design' subcommand needs neither." % text,
            )
        raise CliError(
            "NO_MULTISIM",
            "Multisim could not be started through COM",
            "%s\n\nThis subcommand borrows the SPICE engine of a local NI Multisim "
            "14.3 installation. Install or repair Multisim, then retry." % text,
        )
    return session


def _shutdown(session: Any) -> None:
    """Disconnect cleanly, swallowing errors: a traceback here would mask the result."""
    if session is None:
        return
    try:
        session.shutdown()
    except Exception:
        pass


def cmd_spice(args: argparse.Namespace) -> int:
    try:
        import msim_deck
    except Exception as exc:  # pragma: no cover
        raise CliError("INTERNAL", "the SPICE runner could not be imported", str(exc))

    netlist_text = None
    netlist_path = None
    if args.deck == "-":
        netlist_text = sys.stdin.read()
    elif args.deck:
        netlist_path = args.deck
    if not netlist_text and not netlist_path:
        raise CliError("BAD_ARGS", "pass a deck path, or '-' to read it from stdin")

    session = None
    try:
        session = _session(args.python_config)
        result = msim_deck.run_deck(
            session,
            netlist=netlist_text,
            netlist_path=netlist_path,
            commands=args.command or None,
            output_dir=args.output_dir,
            max_points=int(args.max_points),
            plot=not args.no_plot,
            timeout=float(args.timeout),
            series=args.series.split(",") if args.series else None,
        )
    except msim_deck.DeckError as exc:
        raise CliError(exc.code, exc.message, exc.detail)
    finally:
        _shutdown(session)

    result = dict(result)
    result["ok"] = True
    if not args.rows:
        for analysis in result.get("analyses", []):
            analysis.pop("rows", None)
    _emit(result, args.pretty)
    return EXIT_OK


# --------------------------------------------------------------------------
# netlist -- turn a deck into something Multisim can open as a schematic
# --------------------------------------------------------------------------


def cmd_netlist(args: argparse.Namespace) -> int:
    import msim_spice

    if args.deck == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.deck, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            raise CliError("PATH_MISSING", "deck not found: %s" % args.deck, str(exc))

    # A SPICE deck's first line is a free-text title, never an element, but
    # Multisim turns the words in it into node names. normalise_netlist demotes a
    # bare title to a "*" comment so the engine ignores it; a deck that already
    # starts with a comment keeps it as the title, and one that starts with a dot
    # card has no title at all.
    source_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = None
    for line in source_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            title = stripped.lstrip("* ").strip() or None
        else:
            # A bare first line is a title too: SPICE treats line 1 as free text
            # whether or not it looks like a card.
            title = stripped or None
        break

    normalised = msim_spice.normalise_netlist(text)
    target = os.path.abspath(os.path.expanduser(args.out)) if args.out else None
    if target:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalised)

    lines = normalised.splitlines()
    elements: List[Dict[str, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", ".")):
            continue
        head = stripped.split()
        if head:
            elements.append({"designator": head[0], "line": stripped})

    _emit(
        {
            "ok": True,
            "out": target,
            "title": title,
            "lines": len(lines),
            "elements": elements,
            "element_count": len(elements),
            "netlist": None if target else normalised,
            "note": (
                "A netlist only becomes a schematic when Multisim opens it: "
                "drag the .cir file onto a Multisim window, or use File > Open. "
                "A deck with fewer than two elements, or without a supply for every "
                "gate, opens as an empty sheet."
            ),
        },
        args.pretty,
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# check -- environment diagnosis
# --------------------------------------------------------------------------


def _probe_import(name: str) -> Dict[str, Any]:
    try:
        __import__(name)
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True}


def cmd_check(args: argparse.Namespace) -> int:
    report: Dict[str, Any] = {
        "ok": True,
        "cli_version": VERSION,
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "bits": 64 if sys.maxsize > 2 ** 32 else 32,
            "platform": platform.platform(),
        },
        "worker_dir": WORKER,
        "modules": {},
        "decoder": {},
        "multisim": {},
        "advice": [],
    }

    for name in ("msim_ms14", "msim_spice", "msim_raster", "msim_deck", "msim_com"):
        report["modules"][name] = _probe_import(name)

    decoder = os.path.join(WORKER, "bin", "blast_harness.exe")
    report["decoder"] = {
        "path": decoder,
        "exists": os.path.isfile(decoder),
        "bytes": os.path.getsize(decoder) if os.path.isfile(decoder) else 0,
    }

    exe_candidates = [
        r"C:\Program Files (x86)\National Instruments\Circuit Design Suite 14.3\multisim.exe",
        r"C:\Program Files\National Instruments\Circuit Design Suite 14.3\multisim.exe",
    ]
    found = next((path for path in exe_candidates if os.path.isfile(path)), None)
    report["multisim"] = {"exe": found, "installed": bool(found)}

    bits = report["python"]["bits"]
    if bits != 32:
        report["advice"].append(
            "This interpreter is %d-bit. The 'design' subcommand works anyway; "
            "'spice' needs a 32-bit Python 3.11-3.13 with pywin32." % bits
        )
    if not report["modules"]["msim_com"]["ok"]:
        report["advice"].append(
            "'spice' is unavailable until pywin32 is installed in a 32-bit interpreter."
        )
    if not found:
        report["advice"].append(
            "NI Multisim 14.3 was not found in either Program Files location. "
            "'spice' borrows its engine, so it cannot work without it; "
            "'design' still can."
        )
    if not report["decoder"]["exists"]:
        report["advice"].append(
            "worker/bin/blast_harness.exe is missing; .ms14 decoding will fail. "
            "Rebuild it from worker/bin/blast_harness.c + blast.c."
        )

    _emit(report, args.pretty)
    return EXIT_OK


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msim-cli",
        description="Read Multisim .ms14 designs offline and run SPICE decks "
                    "through the local Multisim engine. Prints JSON.",
    )
    parser.add_argument("--version", action="version", version="msim-cli %s" % VERSION)
    # --pretty is repeated on every subparser as well: argparse only accepts an
    # option before the subcommand when it is declared on the parent, and users
    # naturally type it last.
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_pretty(subparser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        subparser.add_argument(
            "--pretty", action="store_true", default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )
        return subparser

    design = add_pretty(subparsers.add_parser(
        "design", help="decode a .ms14/.msm/.ewb design and list its contents"
    ))
    design.add_argument("file", help="path to the design file")
    design.add_argument(
        "--xml", action="store_true", help="also emit the decoded XML (can be megabytes)"
    )
    design.add_argument(
        "--xml-limit", type=int, default=200000, help="character cap for --xml (default 200000)"
    )
    design.add_argument(
        "--refdes", help="comma separated designators to keep, e.g. R1,R2,U1A"
    )
    design.add_argument(
        "--settings", action="store_true", help="include instrument settings"
    )
    design.set_defaults(func=cmd_design)

    spice = add_pretty(subparsers.add_parser(
        "spice", help="run a SPICE deck through the installed Multisim"
    ))
    spice.add_argument(
        "deck", nargs="?", help="deck file, or '-' to read the netlist from stdin"
    )
    spice.add_argument(
        "-c", "--command", action="append",
        help="analysis command, repeatable (default: op)",
    )
    spice.add_argument("-o", "--output-dir", help="where to write csv/png (no spaces)")
    spice.add_argument(
        "--max-points", type=int, default=200, help="rows sampled per analysis (default 200)"
    )
    spice.add_argument("--rows", action="store_true", help="include sampled rows in the output")
    spice.add_argument("--series", help="comma separated column names to plot")
    spice.add_argument("--no-plot", action="store_true", help="skip PNG rendering")
    spice.add_argument("--timeout", type=float, default=180.0, help="seconds (default 180)")
    spice.add_argument("--python-config", help="path to a python interpreter to run Multisim with")
    spice.set_defaults(func=cmd_spice)

    netlist = add_pretty(subparsers.add_parser(
        "netlist", help="normalise a deck, or write a .cir to open as a schematic"
    ))
    netlist.add_argument("deck", nargs="?", help="deck file, or '-' for stdin")
    netlist.add_argument("-o", "--out", help="write the normalised deck to this path")
    netlist.set_defaults(func=cmd_netlist)

    check = add_pretty(subparsers.add_parser(
        "check", help="report what this machine can and cannot do"
    ))
    check.set_defaults(func=cmd_check)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Chinese design names and paths are common; make stdout UTF-8 regardless of
    # the console code page so the JSON is not mangled on the way out.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CliError as exc:
        return _fail(exc, getattr(args, "pretty", False))
    except KeyboardInterrupt:
        return _fail(CliError("CANCELLED", "interrupted"), getattr(args, "pretty", False))
    except Exception as exc:  # noqa: BLE001 - last resort, keep stdout valid JSON
        import traceback

        return _fail(
            CliError(
                "INTERNAL",
                "%s: %s" % (type(exc).__name__, exc),
                traceback.format_exc()[-4000:],
            ),
            getattr(args, "pretty", False),
        )


if __name__ == "__main__":
    sys.exit(main())
