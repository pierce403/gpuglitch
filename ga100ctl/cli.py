"""Ubuntu CLI for USB provisioning, PCI inventory and GPU workloads."""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from functools import partial
from importlib.resources import files
from pathlib import Path

from . import __version__, gpu, pico, workloads
from .history import History, identifier


def parser():
    root = argparse.ArgumentParser(prog="ga100ctl", description=__doc__)
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--db", type=Path, help="SQLite inventory path")
    root.add_argument("--simulate", action="store_true", help="use the bundled offline fixture")
    root.add_argument("--json", action="store_true", help="print structured JSON")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="inspect hardware and available host tools")
    pico_cmd = commands.add_parser("pico").add_subparsers(dest="action", required=True)
    pico_cmd.add_parser("list", help="discover USB devices")
    for operation in ("build", "flash"):
        cmd = pico_cmd.add_parser(operation, help=f"{operation} USB identity firmware")
        cmd.add_argument("--board", choices=("pico2_w", "pico2"), required=True)
        cmd.add_argument("--build-dir", type=Path, required=True)
        if operation == "build":
            cmd.add_argument("--sdk", type=Path, required=True)
        else:
            cmd.add_argument("--serial", required=True, help="exact BOOTSEL serial from pico list")
            cmd.add_argument("--confirm", action="store_true", help="authorize replacing the Pico image")

    gpu_cmd = commands.add_parser("gpu").add_subparsers(dest="action", required=True)
    gpu_cmd.add_parser("list", help="enumerate NVIDIA PCI functions")
    inspect = gpu_cmd.add_parser("inspect", help="record a baseline or subsequent observation")
    inspect.add_argument("--gpu", help="full PCI address or UUID (optional with exactly one device)")
    inspect.add_argument("--asset-tag", help="unique operator-assigned physical card label")
    validate = commands.add_parser("validate", help="exercise a bounded allocation and optionally run compute")
    validate.add_argument("--gpu", required=True)
    validate.add_argument("--memory-mib", type=int, default=64)
    validate.add_argument("--seconds", type=int, default=0, help="compute stability-test duration")

    bench = commands.add_parser("benchmark", help="measure local model inference or GPU peer copy")
    bench.add_argument("--gpu", required=True)
    bench.add_argument("--backend", choices=("llama.cpp", "pytorch", "peer-copy"), default="llama.cpp")
    bench.add_argument("--peer", help="second GPU for peer-copy")
    bench.add_argument("--model", help="local GGUF or Transformers checkpoint directory")
    bench.add_argument("--prompt-tokens", type=int, default=128)
    bench.add_argument("--generate-tokens", type=int, default=32)
    bench.add_argument("--repetitions", type=int, default=3)

    note = commands.add_parser("note", help="append a dated operator observation")
    note.add_argument("--card", required=True, help="card ID from an inspection or report")
    note.add_argument("--topic", required=True, choices=(
        "physical", "firmware", "software", "experiment", "fuse-observation", "memory", "stability"))
    note.add_argument("text")
    report = commands.add_parser("report", help="read local history, including first-observed baselines")
    report.add_argument("--card", help="filter by historical card ID")
    compare = commands.add_parser("compare", help="compare measurements from two events of the same kind")
    compare.add_argument("left", type=int)
    compare.add_argument("right", type=int)
    return root


def select(cards, target):
    if target is None and len(cards) == 1:
        return cards[0]
    if not cards:
        raise ValueError("No visible NVIDIA GPU. Use doctor for discovery details.")
    if target:
        matches = [c for c in cards if target.casefold() in {
            str(c.get("bdf") or "").casefold(), str(c.get("uuid") or "").casefold()}]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"Expected one GPU matching {target}")
    raise ValueError("Multiple GPUs visible. Select one with --gpu BDF or --gpu UUID.")


def run_event(history, kind, inputs, run, card=None, peer=None):
    card_id, event = history.begin(kind, inputs, card, peer)
    try:
        result = run()
        state = result.get("status", "completed")
        history.finish(event, state, result)
    except KeyboardInterrupt:
        history.finish(event, "interrupted", {"detail": "operator interrupted the host process"})
        raise
    except Exception as exc:
        history.finish(event, "failed", {"detail": str(exc)})
        raise
    return {"card_id": card_id, "event_id": event, "state": state, "result": result}


def scalars(data, prefix=""):
    """Yield comparable leaves; time-series samples and backend-native rows stay in the report."""
    for key, value in data.items():
        label = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from scalars(value, label)
        elif not isinstance(value, list) or all(isinstance(v, (str, int, float, bool)) for v in value):
            yield label, value


def compare_runs(report, left, right):
    events = {e["id"]: e for e in report["events"]}
    if left not in events or right not in events:
        raise ValueError("Both event IDs must exist in the selected inventory")
    a, b = events[left], events[right]
    if a["kind"] != b["kind"]:
        raise ValueError("Select events of the same kind")
    def measurements(event):
        data = dict(event["result"] or {})
        # Align per-device telemetry by role across cards with different UUIDs.
        readings = data.get("telemetry") or {}
        if readings and all(key.startswith("GPU-") for key in readings):
            target = event["inputs"].get("target_uuid")
            data["telemetry"] = {"target" if key == target else "peer": val for key, val in readings.items()}
        return dict(scalars(data))
    x, y = measurements(a), measurements(b)
    return {"kind": a["kind"], "events": [left, right], "cards": [a["card_id"], b["card_id"]],
            "states": [a["state"], b["state"]], "settings": [a["inputs"], b["inputs"]],
            "measurements": {k: [x.get(k), y.get(k)] for k in sorted(x.keys() | y.keys())}}


def provision(args, history):
    params = {"board": args.board, "build_dir": str(args.build_dir.resolve())}
    if args.action == "build":
        params["sdk"] = str(args.sdk.resolve())
        run = partial(pico.build, board=args.board, sdk=args.sdk, build_dir=args.build_dir)
    else:
        params["serial"] = args.serial
        run = partial(pico.flash, board=args.board, serial=args.serial,
                      build_dir=args.build_dir, confirm=args.confirm)
    return run_event(history, "pico-" + args.action, params, run)


def run_workload(args, cards, history):
    card = select(cards, args.gpu)
    peer = None
    params = {"target_bdf": card["bdf"], "target_uuid": card.get("uuid")}
    if args.command == "validate":
        workloads.check_validation_options(args.memory_mib, args.seconds)
        params.update(memory_mib=args.memory_mib, seconds=args.seconds)
        run = partial(workloads.validate, card, memory_mib=args.memory_mib, seconds=args.seconds)
    else:
        workloads.check_benchmark_options(args.prompt_tokens, args.generate_tokens, args.repetitions)
        params.update(backend=args.backend, repetitions=args.repetitions)
        if args.backend == "peer-copy":
            if not args.peer or args.model:
                raise ValueError("peer-copy requires --peer and accepts no model")
            peer = select(cards, args.peer)
            if peer == card:
                raise ValueError("The two GPU targets must be distinct")
            params.update(peer_uuid=peer.get("uuid"), peer_bdf=peer["bdf"])
        else:
            if not args.model or args.peer:
                raise ValueError("Inference requires --model and accepts one target")
            params.update(model=args.model, prompt_tokens=args.prompt_tokens, generate_tokens=args.generate_tokens)
        run = partial(workloads.benchmark, card, model=args.model, backend=args.backend, peer_card=peer,
                      prompt_tokens=args.prompt_tokens, generate_tokens=args.generate_tokens,
                      repetitions=args.repetitions)
    if args.simulate:
        run = partial(dict, status="simulated", detail="Fixture selected. No workload was executed.")
    return run_event(history, args.command, params, run, card, peer)


def execute(args):
    scope = "fixture-v1" if args.simulate else "live"
    fixture = json.loads(files("ga100ctl").joinpath("simulated.json").read_text()) if args.simulate else {}

    def gpus():
        return fixture["gpus"] if args.simulate else gpu.discover()

    def picos():
        return fixture["picos"] if args.simulate else pico.discover()

    cmd = args.command
    if cmd == "doctor":
        names = ("cmake", "arm-none-eabi-gcc", "nvidia-smi", "llama-bench")
        return {"source": scope, "version": __version__, "picos": picos(), "gpus": gpus(),
                "host_tools": {name: shutil.which(name) for name in names} if not args.simulate else {}}
    if cmd in ("gpu", "pico") and args.action == "list":
        return {"source": scope, cmd + "s": gpus() if cmd == "gpu" else picos()}
    if cmd == "pico" and args.simulate:
        raise ValueError("The USB fixture supports discovery. Provisioning uses real build artifacts.")
    if cmd == "pico" and args.action == "flash" and not args.confirm:
        raise ValueError("Replacing the device image requires --confirm")

    state_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    db = args.db or state_dir / "ga100ctl/history.sqlite3"
    readonly = cmd in ("report", "compare")
    if readonly and not db.exists():
        if cmd == "report" and not args.card:
            return {"schema_version": 1, "source": scope, "cards": [], "events": []}
        raise ValueError("No inventory exists at the selected database path")
    with closing(History(db, scope, readonly=readonly)) as history:
        if cmd == "report":
            return history.report(args.card)
        if cmd == "compare":
            return dict(source=scope, **compare_runs(history.report(), args.left, args.right))
        if cmd == "note":
            if not args.text.strip():
                raise ValueError("An observation needs text")
            return {"source": scope, "event_id": history.note(args.card, args.topic, args.text)}
        if cmd == "pico":
            return dict(source=scope, **provision(args, history))
        cards = gpus()
        if cmd == "gpu":
            card = select(cards, args.gpu)
            if args.asset_tag is not None:
                tag = identifier(args.asset_tag)
                if tag is None:
                    raise ValueError("An asset tag must contain a label")
                card["asset_tag"] = tag
            card_id, event_id = history.inspect(card)
            return {"source": scope, "card_id": card_id, "event_id": event_id, "gpu": card}
        return dict(source=scope, **run_workload(args, cards, history))


def show_report(data):
    print(f"GPU inventory ({data['source']})")
    for card in data["cards"]:
        baseline = card["baseline"]
        print(f"\n{card['id']}  {baseline['model']}  first seen {card['first_seen']}")
        print(f"  First seen at PCI {baseline['bdf']}   UUID {baseline.get('uuid') or 'unavailable'}"
              f"   serial {baseline.get('serial') or 'unavailable'}")
        if baseline.get("asset_tag"):
            print(f"  Operator asset tag  {baseline['asset_tag']}")
        if not any(baseline.get(key) for key in ("uuid", "serial", "asset_tag")):
            print("  Physical identity unavailable. This entry identifies one observation.")
    for event in data["events"]:
        print(f"\n#{event['id']}  {event['started']}  {event['kind']}  {event['state']}  "
              f"{event['card_id'] or 'Pico'}")
        if event["inputs"]:
            print("  Settings  " + json.dumps(event["inputs"], ensure_ascii=False, sort_keys=True))
        if event["result"] is not None:
            for key, value in scalars(event["result"]):
                print(f"  {key}  {value}")
    if not data["events"]:
        print("No observations recorded.")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        data = execute(args)
        if args.command == "report" and not args.json:
            show_report(data)
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
        incomplete = {"failed", "unavailable", "interrupted",
                      "copied_runtime_unverified", "transfer_error_runtime_unverified"}
        return 1 if data.get("state") in incomplete else 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"ga100ctl: {exc}", file=sys.stderr)
        return 2
