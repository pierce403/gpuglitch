import subprocess
import unittest
from unittest.mock import Mock, patch

from ga100ctl import _torch_worker, workloads


MIB = 1024**2
CARD = {"uuid": "GPU-12345678-abcd-ef12-3456-123456789abc"}


class MemoryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.torch = Mock()
        self.torch.cuda.mem_get_info.return_value = (70000 * MIB, 81920 * MIB)
        self.torch.cuda.get_device_properties.return_value.multi_processor_count = 108

    @patch("ga100ctl._torch_worker._memory_check")
    def test_auto_size_uses_free_memory_and_reserves_five_percent_of_total(self, check):
        check.return_value = {"patterns": ["64-bit word index"]}
        result = _torch_worker._check_capacity(self.torch, {"memory_mib": None})
        planned = 70000 - 4096
        check.assert_called_once_with(self.torch, planned)
        self.assertEqual(result["verified_bytes"], planned * MIB)
        self.assertEqual(result["sm_count"], 108)
        self.assertEqual(result["status"], "passed")

    @patch("ga100ctl._torch_worker._memory_check")
    def test_reserve_floor_and_oversized_requests_do_not_allocate(self, check):
        self.torch.cuda.mem_get_info.return_value = (600 * MIB, 8192 * MIB)
        result = _torch_worker._check_capacity(self.torch, {"memory_mib": 89})
        self.assertEqual(result["reserve_bytes"], 512 * MIB)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["verified_bytes"], 0)
        self.torch.cuda.mem_get_info.return_value = (400 * MIB, 8192 * MIB)
        self.assertEqual(_torch_worker._check_capacity(self.torch, {"memory_mib": None})["status"],
                         "unavailable")
        check.assert_not_called()

    @patch("ga100ctl._torch_worker._memory_check", side_effect=RuntimeError("address-pattern mismatch"))
    def test_failed_verification_reports_zero_verified(self, check):
        result = _torch_worker._check_capacity(self.torch, {"memory_mib": 16})
        self.assertEqual(result["verified_bytes"], 0)
        self.assertEqual(result["planned_bytes"], 16 * MIB)
        self.assertEqual(result["status"], "failed")
        self.assertIn("mismatch", result["detail"])

    @patch("ga100ctl.workloads._torch_run")
    @patch("ga100ctl.workloads.subprocess.run")
    def test_busy_or_unknown_client_state_skips_auto_check(self, run, worker):
        for stdout in ("10\n20\n", "N/A\n"):
            run.return_value.stdout = stdout
            result = workloads.check_memory(CARD)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["verified_mib"], 0)
        run.side_effect = subprocess.TimeoutExpired(["nvidia-smi"], 5)
        self.assertEqual(workloads.check_memory(CARD)["status"], "unavailable")
        worker.assert_not_called()

    @patch("ga100ctl.workloads._torch_run", return_value={"status": "unavailable", "detail": "missing PyTorch"})
    @patch("ga100ctl.workloads.subprocess.run")
    def test_worker_unavailable_does_not_promote_capacity(self, run, worker):
        run.return_value.stdout = ""
        result = workloads.check_memory(CARD)
        self.assertEqual(worker.call_args.args[1], [CARD["uuid"]])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["verified_bytes"], 0)
        self.assertIsNone(result["requested_bytes"])
        run.reset_mock()
        bounded = workloads.check_memory(CARD, memory_mib=16)
        run.assert_not_called()
        self.assertEqual(bounded["requested_bytes"], 16 * MIB)
