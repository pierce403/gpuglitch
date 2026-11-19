"""Fresh interpreter for CUDA workloads, launched with selected UUIDs."""

import json
import statistics
import sys
import time


def _memory_check(torch, mib):
    count = mib * 1024 * 1024 // 8
    storage = torch.empty(count, device="cuda:0", dtype=torch.int64)
    for value in [0, -1, 0x5555555555555555, -6148914691236517206]:
        storage.fill_(value)
        # Check each chunk so the comparison tensor stays small.
        for chunk in storage.split(262144):
            if not bool(torch.all(chunk == value).item()):
                raise RuntimeError(f"memory mismatch in fill pattern {value}")
    # Fill the entire allocation before readback to exercise aliasing.
    def addresses(start, length):
        return torch.arange(start, start + length, device=storage.device, dtype=torch.int64)

    size = 262144
    for start in range(0, count, size):
        chunk = storage[start:start + size]
        chunk.copy_(addresses(start, chunk.numel()))
    for start in range(0, count, size):
        chunk = storage[start:start + size]
        if not torch.equal(chunk, addresses(start, chunk.numel())):
            raise RuntimeError(f"address-pattern mismatch at chunk starting at word {start}")
    return {"tested_mib": mib, "patterns": ["zeros", "ones", "alternating 01", "alternating 10", "64-bit word index"]}


def _stability(torch, seconds):
    x = torch.ones((1024, 1024), dtype=torch.float32, device="cuda:0")
    y = torch.empty_like(x)
    start = time.monotonic()
    count = 0
    while True:
        torch.mm(x, x, out=y)
        if not bool(torch.all(y == 1024).item()):
            raise RuntimeError("unexpected FP32 matrix product")
        count += 1
        elapsed = time.monotonic() - start
        if elapsed >= seconds:
            return {"elapsed_s": elapsed, "verified_products": count, "matrix_dimension": 1024}


def _validate(torch, spec):
    props = torch.cuda.get_device_properties(0)
    result = {"cuda_initialized": True, "gpu_name": props.name, "sm_count": props.multi_processor_count,
              "cuda_visible_memory_bytes": props.total_memory,
              "compute_capability": list(torch.cuda.get_device_capability(0))}
    try:
        result["memory_integrity"] = _memory_check(torch, spec["memory_mib"])
        result["stability"] = _stability(torch, spec["seconds"])
    except RuntimeError as exc:
        result.update(status="failed", detail=str(exc))
    return result


def _check_capacity(torch, spec):
    mib = 1024 * 1024
    free, total = torch.cuda.mem_get_info(0)
    # Leave room for CUDA, bounded verification scratch, and other driver allocations.
    reserve = max(512 * mib, (total + 19) // 20)
    available_mib = max(0, (free - reserve) // mib)
    requested = spec["memory_mib"]
    planned = min(available_mib, 131072) if requested is None else requested
    result = {
        "cuda_free_bytes": free,
        "cuda_total_bytes": total,
        "reserve_bytes": reserve,
        "requested_mib": requested,
        "planned_mib": planned,
        "planned_bytes": planned * mib,
        "verified_bytes": 0,
        "verified_mib": 0,
        "sm_count": torch.cuda.get_device_properties(0).multi_processor_count,
    }
    if planned < 1 or planned > available_mib:
        return dict(result, status="unavailable",
                    detail=f"{available_mib} MiB available for testing after reserving headroom")
    try:
        result["memory_integrity"] = _memory_check(torch, planned)
    except RuntimeError as exc:
        return dict(result, status="failed", detail=str(exc))
    return dict(result, status="passed", verified_bytes=planned * mib, verified_mib=planned)


def _peer_copy(torch, repeats):
    if torch.cuda.device_count() != 2 or not torch.cuda.can_device_access_peer(0, 1):
        return {"status": "unavailable", "detail": "CUDA peer access unavailable for the selected pair"}
    src = torch.arange(16 * 1024 * 1024, device="cuda:0", dtype=torch.int32)
    dst = torch.empty_like(src, device="cuda:1")
    dst.copy_(src)
    torch.cuda.synchronize(0)
    torch.cuda.synchronize(1)
    start = time.monotonic()
    for _ in range(repeats):
        dst.copy_(src)
    torch.cuda.synchronize(0)
    torch.cuda.synchronize(1)
    elapsed = time.monotonic() - start
    if not torch.equal(dst, torch.arange(dst.numel(), device=dst.device, dtype=dst.dtype)):
        raise RuntimeError("peer copy verification failed")
    size = src.numel() * src.element_size()
    return {"copy_bytes": size, "elapsed_s": elapsed, "one_way_GB_per_second": size * repeats / elapsed / 1e9}


class _TokenTimes:
    """Transformers' greedy streamer delivers the prompt first, then generated tokens."""

    def __init__(self):
        self.prompt_seen = False
        self.times = []

    def put(self, value):
        if self.prompt_seen:
            if value.numel() != 1:
                raise ValueError("unexpected batched tokens from model")
            self.times.append(time.monotonic())
        self.prompt_seen = True

    def end(self):
        pass


def _inference(torch, spec):
    import transformers
    from transformers import AutoModelForCausalLM

    start = time.monotonic()
    model = AutoModelForCausalLM.from_pretrained(
        spec["model"], local_files_only=True, trust_remote_code=False, use_safetensors=True, torch_dtype="auto"
    ).to("cuda:0").eval()
    torch.cuda.synchronize()
    load_time = time.monotonic() - start
    token = model.config.bos_token_id
    token = token if isinstance(token, int) and 0 <= token < model.config.vocab_size else 0
    ids = torch.full((1, spec["prompt_tokens"]), token, dtype=torch.long, device="cuda:0")
    mask = torch.ones_like(ids)
    count = spec["generate_tokens"]

    trials = []
    with torch.inference_mode():
        for i in range(spec["repetitions"] + 1):
            timer = _TokenTimes()
            torch.cuda.synchronize()
            start = time.monotonic()
            model.generate(input_ids=ids, attention_mask=mask, min_new_tokens=count, max_new_tokens=count,
                           pad_token_id=token, do_sample=False, num_beams=1, use_cache=True, streamer=timer)
            torch.cuda.synchronize()
            if len(timer.times) != count:
                raise RuntimeError("model produced an unexpected token count")
            if i:  # First trial warms kernels and allocator.
                ttft = timer.times[0] - start
                decode = timer.times[-1] - timer.times[0]
                trials.append({"time_to_first_token_s": ttft, "prompt_tokens_per_second": spec["prompt_tokens"] / ttft,
                               "generation_tokens_per_second": (count - 1) / decode})
    return {"transformers_version": transformers.__version__, "model_load_s": load_time, "dtype": str(model.dtype),
            "prompt_token_id": token, "trials": trials, "warmup_trials": 1,
            "prompt_tokens_per_second": statistics.median(t["prompt_tokens_per_second"] for t in trials),
            "generation_tokens_per_second": statistics.median(t["generation_tokens_per_second"] for t in trials)}


def execute(spec):
    try:
        import torch
    except ImportError as exc:
        return {"status": "unavailable", "detail": str(exc)}
    versions = {"pytorch_version": str(torch.__version__), "pytorch_cuda_version": torch.version.cuda}
    if not torch.cuda.is_available():
        return dict(versions, status="unavailable", detail="PyTorch cannot initialize selected CUDA device")
    ops = {"validate": _validate, "pytorch": _inference, "memory-check": _check_capacity}
    try:
        torch.cuda.init()
        torch.cuda.reset_peak_memory_stats(0)
        if spec["operation"] == "peer-copy":
            result = _peer_copy(torch, spec["repetitions"])
        else:
            result = ops[spec["operation"]](torch, spec)
        result["peak_allocated_bytes_device0"] = torch.cuda.max_memory_allocated(0)
        return {"status": "passed", **versions, **result}
    except ImportError as exc:
        return dict(versions, status="unavailable", detail=str(exc))
    except Exception as exc:
        return dict(versions, status="failed", detail=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    print(json.dumps(execute(json.loads(sys.argv[1]))))
