"""Ubuntu CLI for USB provisioning, PCI inventory and GPU workloads."""
import argparse
import hashlib
import json
import os
import signal
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
import threading
from contextlib import closing
from functools import partial
from importlib.resources import files
from pathlib import Path

from . import __version__, gpu, pico, workloads
from .history import History, identifier


CMPUNLOCKER_COMMIT = "2c7e2ecd7944e791fec040d2680036beb4a9327b"
CMPUNLOCKER_MEMORY_MIB = {"20c2": 65536, "2082": 40960}
CMPUNLOCKER_FLAGS = ("--no-iommu", "--no-gen2-service", "--no-passthrough")
FF_TOOLS_COMMIT = "04573c9dbf82997fb481f26769ec58dc625d8318"
FF_TOOLS_SCRIPT_SHA256 = {
    "ffburn.py3": "121159ebda1d2d49cb1c011b4b845815ce4869a87bff4ce6928cfa93fcc4fbd0",
    "ffplan.py3": "3b4ed7bd49eb5531c30137ef3289878b1b0b91691e4921d7732db6edf2e85929",
}


def parser():
    root = argparse.ArgumentParser(prog="ga100ctl", description=__doc__)
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--db", type=Path, help="SQLite inventory path")
    root.add_argument("--simulate", action="store_true", help="use the bundled offline fixture")
    root.add_argument("--json", action="store_true", help="print structured JSON")
    commands = root.add_subparsers(dest="command", required=True)
    captures = commands.add_parser("capture", help="work with saved GA100 captures")
    capture_commands = captures.add_subparsers(dest="action", required=True)
    review = capture_commands.add_parser("review", help="check the quality of a saved capture offline")
    review.add_argument("file", type=Path, help="path to a saved capture JSON file")
    review.add_argument("--baseline", type=Path, help="compare with an earlier capture of the same device")
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
    links = gpu_cmd.add_parser("links", help="show current PCIe and NVLink status for one GPU")
    links.add_argument("--gpu", help="full PCI address or UUID (optional with exactly one device)")
    memory = commands.add_parser("memory-check", help="discover CMP 170HX cards and test exposed GPU memory")
    memory.add_argument("--gpu", help="test only this NVIDIA GPU (full PCI address or UUID)")
    memory.add_argument("--memory-mib", type=int, help="test this many MiB instead of sizing from free memory")
    enable = commands.add_parser("memory-enable", help="install pinned cmpunlocker software memory profile")
    enable.add_argument("--source", type=Path, required=True, help="clean cmpunlocker checkout at the pinned revision")
    enable.add_argument("--gpu", help="full PCI address or UUID of the only visible NVIDIA GPU")
    enable.add_argument("--confirm", action="store_true", help="authorize host driver and PCIe configuration changes")
    ff = commands.add_parser("memory-ff", help="review or program card-specific fuseless-fuse records")
    ff_actions = ff.add_subparsers(dest="action", required=True)
    burn = ff_actions.add_parser("burn", help="invoke the pinned upstream FF burner interactively")
    burn.add_argument("--source", type=Path, required=True, help="clean ga100-ff-tools checkout at the pinned revision")
    burn.add_argument("--baseline", type=Path, required=True, help="raw capture used to create the plan")
    burn.add_argument("--plan", type=Path, required=True, help="manually reviewed public FF plan")
    burn.add_argument("--bdf", required=True, help="full PCI address of the selected card")
    burn.add_argument("--plan-id", required=True, help="full plan ID from offline burner review")
    burn.add_argument("--journal", type=Path, required=True, help="new upstream programming journal path")
    burn.add_argument("--confirm-burn", action="store_true", help="authorize invoking the irreversible upstream burner")
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


def scan_memory(args, cards, history):
    if args.memory_mib is not None:
        workloads.check_validation_options(args.memory_mib, 0)
    targets = [select(cards, args.gpu)] if args.gpu else [card for card in cards if card.get("cmp_170hx")]
    if not targets:
        return {"state": "unavailable", "cards": [], "detail": "No CMP 170HX detected by PCI ID."}

    def check(card):
        if args.simulate:
            outcome = {"status": "simulated", "detail": "Fixture only. No memory tested.", "verified_mib": None}
            outcome["ecc"] = {"status": "unavailable"}
        else:
            outcome = workloads.check_memory(card, memory_mib=args.memory_mib)
            outcome["ecc"] = gpu.ecc_after_check(card)
            outcome["memory_test_status"] = outcome["status"]
            ecc_changes = outcome["ecc"].get("delta") or {}
            if outcome["status"] == "passed" and (ecc_changes.get("uncorrected") or 0) > 0:
                outcome.update(status="failed", detail="Uncorrected DRAM ECC errors increased during observation.")
        outcome["driver_total_mib"] = card.get("telemetry", {}).get("memory_total_mib")
        outcome["links"] = gpu.link_report(card, simulate=args.simulate)
        return outcome

    runs = []
    for card in targets:
        inputs = {"target_bdf": card["bdf"], "target_uuid": card.get("uuid"), "memory_mib": args.memory_mib}
        event = run_event(history, "memory-check", inputs, partial(check, card), card)
        runs.append({"bdf": card["bdf"], **event})
    states = {run["state"] for run in runs}
    state = next((s for s in ("failed", "unavailable", "simulated") if s in states), "passed")
    return {"state": state, "cards": runs}


def _cmpunlocker_source(path):
    source = path.resolve(strict=True)
    if not source.is_dir() or not (source / "install.sh").is_file():
        raise ValueError("--source must be the cmpunlocker checkout containing install.sh")

    def git(*arguments):
        result = subprocess.run(["git", "-C", str(source), *arguments],
                                capture_output=True, text=True, check=True, timeout=15)
        return result.stdout.strip()

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != source:
            raise ValueError("--source must name the repository root")
        revision = git("rev-parse", "HEAD")
        if revision != CMPUNLOCKER_COMMIT:
            raise ValueError(f"cmpunlocker must be at pinned commit {CMPUNLOCKER_COMMIT}")
        if git("status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError("cmpunlocker checkout has local changes or untracked files")
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot verify cmpunlocker checkout: {exc.stderr.strip()}") from exc
    return source, hashlib.sha256((source / "install.sh").read_bytes()).hexdigest()


def _run_installer(command, source, timeout=1800):
    """Show a long installer on stderr while retaining a bounded journal excerpt."""
    output_tail = ""
    log_directory = source / "logs"
    previous_logs = {path.name: (path.stat().st_mtime_ns, path.stat().st_size)
                     for path in log_directory.glob("install_*.log")}
    timed_out = False
    announced_log = None
    with subprocess.Popen(command, cwd=source, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1,
                          preexec_fn=os.setpgrp) as process:
        stream = sys.stderr

        def forward_output():
            nonlocal output_tail, announced_log
            for line in process.stdout:
                print(line, end="", file=stream, flush=True)
                output_tail = (output_tail + line)[-4000:]
                if line.startswith("Log saved to: "):
                    announced_log = line.removeprefix("Log saved to: ").strip()

        reader = threading.Thread(target=forward_output, daemon=True)
        reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        reader.join(timeout=5)
        if reader.is_alive():
            process.stdout.close()
            reader.join(timeout=1)
        returncode = process.returncode

    # The upstream installer writes a complete log under the checkout even
    # when it stops before printing its final "Log saved to" line.
    new_logs = [path for path in log_directory.glob("install_*.log")
                if previous_logs.get(path.name) != (path.stat().st_mtime_ns, path.stat().st_size)]
    newest_log = max(new_logs, key=lambda path: path.stat().st_mtime, default=None)
    return {"returncode": returncode, "output_tail": output_tail,
            "timed_out": timed_out,
            "log_path": announced_log or (str(newest_log) if newest_log else None)}


def enable_memory(args, cards, history):
    if len(cards) != 1:
        raise ValueError("memory-enable requires exactly one visible NVIDIA GPU because cmpunlocker changes host-wide driver settings")
    card = select(cards, args.gpu)
    if card.get("vendor_id") != "10de" or card.get("device_id") not in CMPUNLOCKER_MEMORY_MIB:
        raise ValueError("memory-enable supports only CMP 170HX PCI IDs 10de:20c2 and 10de:2082")
    source, installer_sha256 = _cmpunlocker_source(args.source)
    expected_mib = CMPUNLOCKER_MEMORY_MIB[card["device_id"]]
    command = ["sudo", "-n", "bash", str(source / "install.sh"), *CMPUNLOCKER_FLAGS]
    inputs = {"target_bdf": card["bdf"], "target_uuid": card.get("uuid"),
              "pci_id": "10de:" + card["device_id"], "source": str(source),
              "source_commit": CMPUNLOCKER_COMMIT, "installer_sha256": installer_sha256,
              "installer_flags": list(CMPUNLOCKER_FLAGS)}

    def install():
        # Authenticate while this process still owns the operator's terminal.
        # The long-running installer gets a separate process group so a timeout
        # can stop its child build commands. It retains the controlling TTY,
        # which keeps sudo's terminal-scoped authentication ticket usable.
        try:
            auth = subprocess.run(["sudo", "-v"], check=False, timeout=120)
        except subprocess.TimeoutExpired:
            return {"status": "failed", "method": "cmpunlocker", "sudo_auth_exit_code": None,
                    "detail": "Sudo authentication timed out; installer did not start."}
        if auth.returncode:
            return {"status": "failed", "method": "cmpunlocker",
                    "sudo_auth_exit_code": auth.returncode,
                    "detail": "Sudo authentication failed; installer did not start."}
        result = _run_installer(command, source)
        report = {"method": "cmpunlocker", "source_commit": CMPUNLOCKER_COMMIT,
                  "installer_sha256": installer_sha256, "installer_exit_code": result["returncode"],
                  "sudo_auth_exit_code": auth.returncode,
                  "expected_driver_memory_mib_after_boot": expected_mib,
                  "host_changes": ["NVIDIA kernel modules", "PCIe Gen2 modprobe setting"],
                  "installer_log": result["log_path"],
                  "installer_output_tail": result["output_tail"]}
        if result["timed_out"]:
            return dict(report, status="failed", detail="Installer timed out and was stopped. Host changes may be partial; inspect its log before retrying.")
        if result["returncode"]:
            return dict(report, status="failed", detail="Installer failed. Host changes may be partial; inspect its log before retrying.")
        return dict(report, status="pending_power_cycle",
                    detail="Installer finished. Perform a cold power cycle, then run ga100ctl memory-check to measure usable memory.")

    return {"source": "live", **run_event(history, "memory-enable", inputs, install, card)}


def _ff_tools_source(path):
    """Accept only the reviewed upstream tree, without vendoring its burner."""
    source = path.resolve(strict=True)
    scripts = tuple(FF_TOOLS_SCRIPT_SHA256)
    if not source.is_dir() or any(not (source / name).is_file() for name in scripts):
        raise ValueError("--source must be a ga100-ff-tools checkout with ffburn.py3 and ffplan.py3")

    def git(*arguments):
        result = subprocess.run(["git", "-C", str(source), *arguments],
                                capture_output=True, text=True, check=True, timeout=15)
        return result.stdout.strip()

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != source:
            raise ValueError("--source must name the ga100-ff-tools repository root")
        if git("rev-parse", "HEAD") != FF_TOOLS_COMMIT:
            raise ValueError(f"ga100-ff-tools must be at pinned commit {FF_TOOLS_COMMIT}")
        if git("status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError("ga100-ff-tools checkout has local changes or untracked files")
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot verify ga100-ff-tools checkout: {exc.stderr.strip()}") from exc
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in scripts}
    if hashes != FF_TOOLS_SCRIPT_SHA256:
        raise ValueError("ga100-ff-tools script hashes differ from the reviewed pinned revision")
    return source, hashes


def _ff_burn_inputs(args, cards):
    bdf = gpu._bdf(args.bdf)
    if bdf is None:
        raise ValueError("--bdf must be a full PCI address, for example 0000:01:00.0")
    matches = [card for card in cards if card.get("bdf") == bdf]
    if len(matches) != 1:
        raise ValueError(f"No unique NVIDIA GPU found at {bdf}")
    card = matches[0]
    if card.get("vendor_id") != "10de" or card.get("device_id") not in {"20c2", "2080"}:
        raise ValueError("The upstream FF burner accepts only 10de:20c2 or glitch identity 10de:2080; 10de:2082 is unsupported")

    source, hashes = _ff_tools_source(args.source)
    baseline = args.baseline.resolve(strict=True)
    plan = args.plan.resolve(strict=True)
    if not baseline.is_file() or not plan.is_file():
        raise ValueError("--baseline and --plan must be existing regular files")
    journal = args.journal.absolute()
    if journal.exists() or journal.is_symlink():
        raise ValueError("--journal must name a new file; never reuse a programming journal")
    if not journal.parent.is_dir():
        raise ValueError("--journal parent directory must already exist")
    try:
        manifest = json.loads(plan.read_text())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read FF plan JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "ga100-public-ff-plan-v2":
        raise ValueError("--plan must use the upstream public FF plan v2 schema")
    if gpu._bdf(str(manifest.get("bdf", ""))) != bdf or manifest.get("plan_id") != args.plan_id:
        raise ValueError("--bdf and --plan-id must match the reviewed plan exactly")
    records = manifest.get("records")
    features = manifest.get("features")
    if (manifest.get("status") != "logical-plan-complete"
            or not isinstance(records, list) or not records
            or not isinstance(features, list) or "memory" not in features):
        raise ValueError("The selected plan must be complete and include nonempty memory records")
    if manifest.get("route") != "cmp" or manifest.get("public_sources_only") is not True:
        raise ValueError("memory-ff burn accepts only the CMP route with published mappings")
    if not sys.stdin.isatty():
        raise ValueError("memory-ff burn requires an interactive terminal for the upstream confirmations")
    return card, bdf, source, hashes, baseline, plan, journal


def burn_ff_memory(args, cards, history):
    card, bdf, source, hashes, baseline, plan, journal = _ff_burn_inputs(args, cards)
    command = ["sudo", sys.executable, "-I", "-S", str(source / "ffburn.py3"),
               "--baseline", str(baseline), "--plan", str(plan),
               "--bdf", bdf, "--execute-plan", args.plan_id, "--journal", str(journal)]
    inputs = {"target_bdf": bdf, "pci_id": "10de:" + card["device_id"],
              "source": str(source), "source_commit": FF_TOOLS_COMMIT,
              "source_scripts_sha256": hashes, "baseline": str(baseline),
              "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
              "plan": str(plan), "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
              "plan_id": args.plan_id, "journal": str(journal)}

    def invoke():
        # The upstream script must ask its own three questions on inherited stdin.
        # We never feed answers or impose a timeout during an irreversible write.
        _, current_hashes = _ff_tools_source(source)
        if current_hashes != hashes:
            raise ValueError("ga100-ff-tools scripts changed after initial review")
        result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr, check=False)
        exit_code = result.returncode if result.returncode >= 0 else 128 - result.returncode
        if exit_code:
            return {"status": "failed", "upstream_exit_code": exit_code,
                    "upstream_journal": str(journal),
                    "detail": "Upstream burner exited unsuccessfully. OTP state may be partial or unknown; inspect its journal before any further action."}
        return {"status": "pending_cold_boot_verification", "upstream_exit_code": 0,
                "upstream_journal": str(journal),
                "detail": "Upstream reported same-session array verification only. Cold-boot replay and usable memory remain unverified."}

    return {"source": "live", **run_event(history, "memory-ff-burn", inputs, invoke, card)}


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
    if cmd == "gpu" and args.action == "links":
        return {"source": scope, **gpu.link_report(select(gpus(), args.gpu), simulate=args.simulate)}
    if cmd == "pico" and args.simulate:
        raise ValueError("The USB fixture supports discovery. Provisioning uses real build artifacts.")
    if cmd == "pico" and args.action == "flash" and not args.confirm:
        raise ValueError("Replacing the device image requires --confirm")
    if cmd == "memory-enable":
        if args.simulate:
            raise ValueError("memory-enable requires a real GPU host")
        if not args.confirm:
            raise ValueError("Changing the host driver requires --confirm")
    if cmd == "memory-ff":
        if args.simulate:
            raise ValueError("memory-ff burn requires a real GPU host")
        if not args.confirm_burn:
            raise ValueError("Invoking irreversible FF programming requires --confirm-burn")

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
        if cmd == "memory-ff":
            return burn_ff_memory(args, cards, history)
        if cmd == "memory-enable":
            return enable_memory(args, cards, history)
        if cmd == "memory-check":
            return dict(source=scope, **scan_memory(args, cards, history))
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


def show_memory_check(data):
    print(f"Memory check ({data['source']})  {data['state']}")
    if not data["cards"]:
        print(data["detail"])
    for run in data["cards"]:
        result = run["result"]
        print(f"\n{run['bdf']}  {run['state']}  event #{run['event_id']}")
        for name, key in (("Driver total", "driver_total_mib"), ("Verified allocation", "verified_mib")):
            mib = result.get(key)
            print(f"  {name}  {mib} MiB" if mib is not None else f"  {name}  unavailable")
        delta = result["ecc"].get("delta") or {}
        print("  DRAM ECC change  corrected {} / uncorrected {}".format(
            delta.get("corrected") if delta.get("corrected") is not None else "unknown",
            delta.get("uncorrected") if delta.get("uncorrected") is not None else "unknown"))
        pcie = result["links"]["pcie"]
        print("  PCIe  {}  width {}".format(pcie.get("current_link_speed") or "unknown",
                                            pcie.get("current_link_width") or "unknown"))
        if result.get("detail"):
            print(f"  {result['detail']}")


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
        if args.command == "capture":
            if args.simulate:
                raise ValueError("capture review reads the file you supply. Omit --simulate.")
            companion = shutil.which("ga100-capture-review", path=sysconfig.get_path("scripts"))
            companion = companion or shutil.which("ga100-capture-review")
            if not companion:
                raise ValueError(
                    "Install the capture reader from the gpuglitch checkout with "
                    "cargo install --locked --path tools/capture-review and add Cargo's bin directory to PATH."
                )
            command = [companion, str(args.file.absolute())]
            if args.baseline is not None:
                command.extend(["--baseline", str(args.baseline.absolute())])
            result = subprocess.run(command, check=False)
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
        data = execute(args)
        if args.command == "report" and not args.json:
            show_report(data)
        elif args.command == "memory-check" and not args.json:
            show_memory_check(data)
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
        if args.command == "memory-ff" and args.action == "burn":
            return data["result"]["upstream_exit_code"]
        incomplete = {"failed", "unavailable", "interrupted",
                      "copied_runtime_unverified", "transfer_error_runtime_unverified"}
        return 1 if data.get("state") in incomplete else 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"ga100ctl: {exc}", file=sys.stderr)
        return 2
