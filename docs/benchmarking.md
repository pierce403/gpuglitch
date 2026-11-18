# GPU validation and benchmarking

Use a driver that exposes the card to `nvidia-smi`. Install a CUDA-enabled PyTorch build into the Python environment running `ga100ctl` for validation and peer copies. The PyTorch inference backend also requires Transformers and safetensors. The llama.cpp backend uses a CUDA-enabled `llama-bench` on PATH that supports `--device` and JSON output. Each run uses the selected card's full driver UUID to set CUDA visibility in a fresh process. Full-device workloads require MIG to be disabled.

## Verify a baseline

Select the actual PCI address from the first command.

```sh
ga100ctl gpu list
gpu=0000:01:00.0
ga100ctl gpu inspect --gpu "$gpu"
ga100ctl validate --gpu "$gpu" --memory-mib 64 --seconds 0
ga100ctl validate --gpu "$gpu" --memory-mib 1024 --seconds 600
```

Validation reports CUDA-visible memory, SM count and compute capability. It writes and reads four bit patterns and a 64-bit word-address pattern across one requested allocation. A successful run covers that allocation only. Account for runtime and scratch memory when choosing an allocation size. An allocation failure produces a failed event. A zero-second stability run checks one 1024 × 1024 FP32 matrix product. A positive duration repeats and verifies the product until the interval elapses. The report includes negotiated and maximum PCIe link characteristics when available and the driver's NVLink status response. Hardware absence, unsupported driver calls, and an unavailable Python backend remain visible in the evidence.

## Measure inference

Choose an idle card and use the same driver, model weights, input lengths, backend version, repetitions, and cooling conditions for each comparison. Inspect before each run to capture changes in firmware, driver and available memory. The models below are example local paths.

```sh
ga100ctl benchmark --gpu "$gpu" --backend llama.cpp --model /models/model.gguf \
  --prompt-tokens 128 --generate-tokens 32 --repetitions 5
ga100ctl benchmark --gpu "$gpu" --backend pytorch --model /models/local-transformers-checkpoint \
  --prompt-tokens 128 --generate-tokens 32 --repetitions 5
```

- **llama.cpp** runs separate prompt and generation tests with GPU offload enabled. Results preserve the native JSON rows, build metadata, command, file SHA-256 and average token rates. For a split GGUF, select the first shard. The report hashes every file in the numbered set. Upstream's JSON throughput output has no isolated load-time measurement. [llama-bench documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md)
- **PyTorch** loads a local causal language model from safetensors, with repository-provided code disabled. Load time includes reading the checkpoint, moving the model to the GPU and synchronizing CUDA. It excludes computing file hashes. The prompt repeats one token for a reproducible shape. One unmeasured warmup precedes the trials. Prompt throughput divides prompt length by time to the first generated token, including generation setup. Generation throughput measures tokens after the first token using host timestamps from the Transformers streamer. The report retains each trial and its median rates, precision, PyTorch and Transformers versions, and hashes of local configuration and weight files. Hashing reads the files before loading and can warm the filesystem cache.
- **Telemetry** comes from repeated per-device `nvidia-smi` queries during worker execution. Timestamps, temperature, power, framebuffer use and clocks are retained when the driver reports them. Mean power is the arithmetic mean of available samples over the full worker lifetime, including setup. Short runs can yield very few samples. Sampled device memory usage includes other processes and can miss brief peaks. The PyTorch allocator peak on device zero is also recorded. Sampling alone cannot certify the electrical limits of a modified board.

## Measure two-GPU communication

```sh
ga100ctl benchmark --gpu "$gpu" --peer 0000:02:00.0 --backend peer-copy --repetitions 20
```

For a pair with CUDA peer access, this measures a verified one-way 64 MiB tensor copy from the selected GPU to the peer. The timed loop excludes allocation, one warmup copy and final data verification. The reported bandwidth is decimal GB/s. Run again with the two targets exchanged to measure the other direction. The result measures the configured transfer path without identifying its physical transport.

## Compare and export records

```sh
ga100ctl report
ga100ctl compare 2 5
ga100ctl --json report > gpu-history.json
```

Replace the event IDs with validation or benchmark events from your own report. Compare matched workloads within the same backend and retain the inputs alongside the measurements. Live performance figures require actual GPUs. Offline tests cover adapter inputs, driver output parsing, telemetry aggregation, child-process scoping and failure handling. vLLM and collective communication benchmarks are planned.
