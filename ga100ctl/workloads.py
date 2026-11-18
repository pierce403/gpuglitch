"""Explicitly targeted GPU validation and local model benchmarks."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .gpu import FULL_UUID, nvlink_status


def _target(card):
    uuid = card.get("uuid")
    if not isinstance(uuid, str) or not FULL_UUID.fullmatch(uuid):
        raise ValueError("a driver-reported full GPU UUID is required")
    if str(card.get("mig_mode")).lower() == "enabled":
        raise ValueError("this workload requires a full GPU with MIG disabled")
    return uuid


def _sample(uuid):
    query = "--query-gpu=temperature.gpu,power.draw,memory.used,clocks.current.graphics,clocks.current.memory"
    try:
        result = subprocess.run(["nvidia-smi", "-i", uuid, query, "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, check=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return {}
    fields = result.stdout.strip().split(",")
    keys = ["temperature_c", "power_w", "memory_used_mib", "graphics_clock_mhz", "memory_clock_mhz"]
    if len(fields) != len(keys):
        return {}
    sample = {}
    for key, field in zip(keys, fields):
        try:
            number = float(field.strip())
            if math.isfinite(number):
                sample[key] = number
        except ValueError:
            pass
    return sample


def _run(argv, uuids, timeout):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=",".join(uuids), HF_HUB_OFFLINE="1")
    samples = {uuid: [] for uuid in uuids}
    start = time.monotonic()
    deadline = start + timeout
    try:
        with subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, errors="replace") as child:
            try:
                while True:
                    for uuid in uuids:
                        sample = _sample(uuid)
                        if sample:
                            sample["elapsed_s"] = round(time.monotonic() - start, 3)
                            samples[uuid].append(sample)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"workload exceeded {timeout} seconds")
                    try:
                        stdout, stderr = child.communicate(timeout=min(1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
            if child.returncode:
                return {"status": "failed", "detail": stderr.strip()[-2000:] or f"child exited {child.returncode}",
                        "samples": samples}
    except (OSError, TimeoutError) as exc:
        return {"status": "failed", "detail": str(exc), "samples": samples}
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return {"status": "failed", "detail": "invalid JSON from workload", "samples": samples}
    return {"status": "passed", "payload": payload, "samples": samples}


def _telemetry(samples):
    result = {}
    for uuid, observations in samples.items():
        watts = [p["power_w"] for p in observations if "power_w" in p]
        temps = [p["temperature_c"] for p in observations if "temperature_c" in p]
        memory = [p["memory_used_mib"] for p in observations if "memory_used_mib" in p]
        result[uuid] = {"samples": observations, "mean_sampled_power_w": sum(watts) / len(watts) if watts else None,
                        "max_sampled_temperature_c": max(temps, default=None),
                        "max_sampled_device_memory_mib": max(memory, default=None)}
    return result


def _torch_run(spec, uuids, timeout):
    run = _run([sys.executable, "-m", "ga100ctl._torch_worker", json.dumps(spec)], uuids, timeout)
    payload = run.pop("payload", None)
    if run["status"] == "passed":
        if isinstance(payload, dict) and payload.get("status") in {"passed", "failed", "unavailable"}:
            run.update(payload)
        else:
            run.update(status="failed", detail="unexpected CUDA worker response")
    run["telemetry"] = _telemetry(run.pop("samples"))
    return run


def check_validation_options(memory_mib, seconds):
    if not 1 <= memory_mib <= 131072 or not 0 <= seconds <= 86400:
        raise ValueError("memory_mib must be 1..131072 and seconds must be 0..86400")


def validate(card, *, memory_mib=64, seconds=0):
    """Test a bounded allocation and optionally stress the selected GPU."""
    check_validation_options(memory_mib, seconds)
    spec = {"operation": "validate", "memory_mib": memory_mib, "seconds": seconds}
    try:
        uuid = _target(card)
    except ValueError as exc:
        return {"status": "unavailable", "detail": str(exc), "parameters": spec}
    result = _torch_run(spec, [uuid], max(180, memory_mib // 8) + seconds)
    result.update(parameters=spec, nvlink=nvlink_status(uuid),
                  pcie_link=card.get("pcie_link"), baseline_telemetry=card.get("telemetry"))
    return result


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkpoint(path):
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix in {".json", ".safetensors"})
    else:
        # llama.cpp can open all shards when given just the first filename.
        match = re.fullmatch(r"(.*)-(\d{5})-of-(\d{5})\.gguf", path.name, re.I)
        if match:
            if int(match[2]) != 1 or int(match[3]) < 1:
                raise ValueError("select the first file in a split GGUF checkpoint")
            files = [path.with_name(f"{match[1]}-{index:05d}-of-{match[3]}{path.suffix}")
                     for index in range(1, int(match[3]) + 1)]
        else:
            files = [path]
    return {p.name: _digest(p) for p in files}


def _llama_rates(rows, prompt, generate):
    if not isinstance(rows, list) or not rows:
        raise ValueError("llama-bench returned no rows")
    rates = {}
    for row in rows:
        if not isinstance(row, dict) or "CUDA" not in str(row.get("backends", "")).upper():
            raise ValueError("llama-bench did not report the CUDA backend")
        rate = row.get("avg_ts")
        if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
            raise ValueError("missing valid llama-bench throughput")
        shape = row.get("n_prompt"), row.get("n_gen")
        if shape == (prompt, 0):
            rates["prompt_tokens_per_second"] = rate
        if shape == (0, generate):
            rates["generation_tokens_per_second"] = rate
    if len(rates) != 2:
        raise ValueError("missing requested prompt or generation measurements")
    return rates


def check_benchmark_options(prompt_tokens, generate_tokens, repetitions):
    if not 1 <= prompt_tokens <= 131072 or not 2 <= generate_tokens <= 32768 or not 1 <= repetitions <= 100:
        raise ValueError("expected prompt_tokens 1..131072, generate_tokens 2..32768, repetitions 1..100")


def benchmark(card, *, model=None, backend="llama.cpp", peer_card=None, prompt_tokens=128,
              generate_tokens=32, repetitions=3):
    """Measure explicit local model inference or a two-GPU peer copy."""
    check_benchmark_options(prompt_tokens, generate_tokens, repetitions)
    if backend not in {"pytorch", "llama.cpp", "peer-copy"}:
        raise ValueError("unknown benchmark backend")
    try:
        uuids = [_target(card)]
        if backend == "peer-copy":
            if not peer_card:
                raise ValueError("a second GPU is required")
            uuids.append(_target(peer_card))
            if uuids[0] == uuids[1]:
                raise ValueError("peer must be a different GPU")
    except ValueError as exc:
        return {"status": "unavailable", "detail": str(exc)}
    spec = {"operation": backend, "repetitions": repetitions}
    if backend == "peer-copy":
        return dict(_torch_run(spec, uuids, 180), parameters=spec)
    if not model:
        raise ValueError("a path to a local model is required")
    path = Path(model).resolve()
    if backend == "llama.cpp" and not path.is_file():
        raise ValueError("llama.cpp expects a local GGUF file")
    if backend == "pytorch" and not path.is_dir():
        raise ValueError("pytorch expects a local Transformers checkpoint directory")
    binary = shutil.which("llama-bench") if backend == "llama.cpp" else None
    if backend == "llama.cpp" and not binary:
        return {"status": "unavailable", "detail": "llama-bench not found on PATH"}
    spec.update(model=str(path), prompt_tokens=prompt_tokens, generate_tokens=generate_tokens)
    try:
        fingerprint = _checkpoint(path)
    except OSError as exc:
        return {"status": "failed", "detail": str(exc), "parameters": spec}
    if backend == "pytorch":
        return dict(_torch_run(spec, uuids, 1800), parameters=spec, model_sha256=fingerprint)
    argv = [binary, "-m", str(path), "-p", str(prompt_tokens), "-n", str(generate_tokens),
            "-r", str(repetitions), "-ngl", "999", "--device", "CUDA0", "-o", "json"]
    run = _run(argv, uuids, 1800)
    rows = run.pop("payload", None)
    if run["status"] == "passed":
        try:
            run.update(_llama_rates(rows, prompt_tokens, generate_tokens), native_rows=rows)
        except ValueError as exc:
            run.update(status="failed", detail=str(exc))
    run.update(parameters=spec, command=argv, model_sha256=fingerprint,
               telemetry=_telemetry(run.pop("samples")))
    return run
