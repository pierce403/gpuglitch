import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ga100ctl import workloads


CARD = {"uuid": "GPU-12345678-abcd-ef12-3456-123456789abc"}
PEER = {"uuid": "GPU-12345678-abcd-ef12-3456-123456789def"}


class WorkloadTest(unittest.TestCase):
    @patch("ga100ctl.workloads._run")
    def test_unusable_target_never_launches_worker(self, run):
        for card in ({}, {"uuid": "0"}, dict(CARD, mig_mode="Enabled")):
            self.assertEqual(workloads.validate(card)["status"], "unavailable")
        self.assertEqual(workloads.benchmark(CARD, backend="peer-copy", peer_card=CARD)["status"], "unavailable")
        with self.assertRaises(ValueError):
            workloads.validate(CARD, memory_mib=0)
        run.assert_not_called()

    @patch("ga100ctl.workloads._sample", return_value={})
    def test_worker_environment_and_timeout(self, sample):
        cmd = [sys.executable, "-c", "import os,json; print(json.dumps(os.environ['CUDA_VISIBLE_DEVICES']))"]
        with patch.dict(os.environ, CUDA_VISIBLE_DEVICES="1"):
            self.assertEqual(workloads._run(cmd, [CARD["uuid"]], 5)["payload"], CARD["uuid"])
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "1")
        hung = [sys.executable, "-c", "import time; time.sleep(60)"]
        self.assertEqual(workloads._run(hung, [CARD["uuid"]], 0.03)["status"], "failed")

    @patch("ga100ctl.workloads.nvlink_status", return_value={"status": "unavailable"})
    @patch("ga100ctl.workloads._run")
    def test_validation_does_not_promote_failed_memory_check(self, run, nvlink):
        run.return_value = {"status": "passed", "payload": {"status": "failed", "detail": "memory mismatch"},
                            "samples": {CARD["uuid"]: [{"power_w": 120.0}, {"power_w": 140.0}]}}
        result = workloads.validate(CARD, memory_mib=16, seconds=5)
        self.assertEqual(run.call_args.args[1], [CARD["uuid"]])
        spec = json.loads(run.call_args.args[0][-1])
        self.assertEqual(spec["memory_mib"], 16)
        self.assertEqual(spec["seconds"], 5)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["telemetry"][CARD["uuid"]]["mean_sampled_power_w"], 130.0)

    @patch("ga100ctl.workloads._torch_run", return_value={"status": "passed"})
    def test_peer_targets(self, run):
        result = workloads.benchmark(CARD, backend="peer-copy", peer_card=PEER)
        self.assertEqual(run.call_args.args[1], [CARD["uuid"], PEER["uuid"]])
        self.assertEqual(result["parameters"]["operation"], "peer-copy")

    def test_split_gguf_hashes_each_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "example-00001-of-00002.gguf"
            second = Path(tmp) / "example-00002-of-00002.gguf"
            first.write_bytes(b"first shard")
            with self.assertRaises(FileNotFoundError):
                workloads._checkpoint(first)
            second.write_bytes(b"second shard")
            self.assertEqual(set(workloads._checkpoint(first)), {first.name, second.name})
            with self.assertRaises(ValueError):
                workloads._checkpoint(second)

    @patch("ga100ctl.workloads.shutil.which", return_value="/bin/llama-bench")
    @patch("ga100ctl.workloads._run")
    def test_llama_requires_separate_native_measurements(self, run, which):
        rows = [{"backends": "CUDA", "n_prompt": 128, "n_gen": 0, "avg_ts": 80.0},
                {"backends": "CUDA", "n_prompt": 0, "n_gen": 32, "avg_ts": 13.0}]
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "model.gguf"
            checkpoint.write_bytes(b"test")
            for payload, expected in [(rows, "passed"), (rows[:1], "failed"), (None, "failed"),
                                      ([dict(r, backends="CPU") for r in rows], "failed")]:
                run.return_value = {"status": "passed", "payload": payload, "samples": {}}
                result = workloads.benchmark(CARD, model=checkpoint)
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["model_sha256"][checkpoint.name], hashlib.sha256(b"test").hexdigest())
                self.assertEqual(run.call_args.args[1], [CARD["uuid"]])
                if expected == "passed":
                    self.assertEqual(result["generation_tokens_per_second"], 13.0)
                    self.assertIn("CUDA0", result["command"])
