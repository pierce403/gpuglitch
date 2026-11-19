import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ga100ctl import cli
from ga100ctl.history import History


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "state" / "history.sqlite3"

    def invoke(self, *args, simulate=True, code=0, as_json=True):
        flags = ["--db", str(self.db)]
        if simulate:
            flags.append("--simulate")
        if as_json:
            flags.append("--json")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            observed = cli.main([*flags, *args])
        self.assertEqual(observed, code, stderr.getvalue())
        if code == 2:
            return stderr.getvalue()
        return json.loads(stdout.getvalue()) if as_json else stdout.getvalue()

    @mock.patch("ga100ctl.cli.gpu.discover", side_effect=AssertionError("hardware access"))
    @mock.patch("ga100ctl.cli.pico.discover", side_effect=AssertionError("hardware access"))
    def test_passive_simulation(self, _pico, _gpu):
        doctor = self.invoke("doctor")
        self.assertEqual(len(doctor["gpus"]), 2)
        self.assertEqual(self.invoke("pico", "list")["picos"], doctor["picos"])
        self.assertFalse(self.db.exists())

    def test_memory_check_finds_both_fixture_cards_without_testing_hardware(self):
        with mock.patch("ga100ctl.cli.workloads.check_memory", side_effect=AssertionError("CUDA workload")):
            scan = self.invoke("memory-check")
        self.assertEqual(scan["state"], "simulated")
        self.assertEqual([run["bdf"] for run in scan["cards"]], ["0000:41:00.0", "0000:81:00.0"])
        self.assertTrue(all(run["result"]["verified_mib"] is None for run in scan["cards"]))
        events = self.invoke("report")["events"]
        self.assertEqual([e["kind"] for e in events], ["inspection", "memory-check"] * 2)
        targeted = self.invoke("memory-check", "--gpu", "0000:81:00.0")
        self.assertEqual(len(targeted["cards"]), 1)
        self.assertEqual(targeted["cards"][0]["card_id"], scan["cards"][1]["card_id"])

    def test_memory_enable_requires_confirmation_one_supported_gpu_and_live_host(self):
        card = {"bdf": "0000:41:00.0", "uuid": None, "vendor_id": "10de",
                "device_id": "20c2", "model": "CMP 170HX"}
        source = Path("/tmp/cmpunlocker")
        args = ("memory-enable", "--source", str(source))
        with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
             mock.patch.object(cli, "_cmpunlocker_source") as checked, \
             mock.patch.object(cli, "_run_installer") as installed:
            self.assertIn("real GPU host", self.invoke(*args, "--confirm", code=2))
            self.assertIn("--confirm", self.invoke(*args, simulate=False, code=2))
            self.assertIn("Expected one GPU matching", self.invoke(
                *args, "--gpu", "0000:42:00.0", "--confirm", simulate=False, code=2))
            checked.assert_not_called()
            installed.assert_not_called()
            for cards, message in [([], "exactly one visible NVIDIA GPU"),
                                   ([card, dict(card, bdf="0000:42:00.0", device_id="1db6")],
                                    "exactly one visible NVIDIA GPU"),
                                   ([dict(card, device_id="1db6")], "supports only CMP 170HX")]:
                with mock.patch.object(cli.gpu, "discover", return_value=cards):
                    self.assertIn(message, self.invoke(*args, "--confirm", simulate=False, code=2))
            installed.assert_not_called()

    def test_memory_enable_runs_pinned_installer_and_journals_pending_reboot(self):
        card = {"bdf": "0000:41:00.0", "uuid": None, "vendor_id": "10de",
                "device_id": "20c2", "model": "CMP 170HX"}
        source = Path("/tmp/cmpunlocker")
        outcome = {"returncode": 0, "output_tail": "Log saved to: /tmp/cmpunlocker/logs/install.log\n",
                   "timed_out": False, "log_path": "/tmp/cmpunlocker/logs/install.log"}
        with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
             mock.patch.object(cli, "_cmpunlocker_source", return_value=(source, "abc123")) as checked, \
             mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as auth, \
             mock.patch.object(cli, "_run_installer", return_value=outcome) as installed:
            result = self.invoke("memory-enable", "--source", str(source), "--gpu", card["bdf"],
                                 "--confirm", simulate=False)
        self.assertEqual(result["state"], "pending_power_cycle")
        self.assertEqual(result["result"]["expected_driver_memory_mib_after_boot"], 65536)
        self.assertIn("PCIe Gen2 modprobe setting", result["result"]["host_changes"])
        checked.assert_called_once_with(source)
        auth.assert_called_once_with(["sudo", "-v"], check=False, timeout=120)
        installed.assert_called_once_with(
            ["sudo", "-n", "bash", str(source / "install.sh"), "--no-iommu", "--no-gen2-service",
             "--no-passthrough"], source)
        event = self.invoke("report", simulate=False)["events"][-1]
        self.assertEqual(event["kind"], "memory-enable")
        self.assertEqual(event["state"], "pending_power_cycle")
        self.assertEqual(event["inputs"]["source_commit"], cli.CMPUNLOCKER_COMMIT)
        self.assertEqual(event["result"]["installer_log"], outcome["log_path"])

    def test_memory_enable_records_installer_failure_and_timeout(self):
        card = {"bdf": "0000:41:00.0", "vendor_id": "10de", "device_id": "2082",
                "model": "CMP 170HX"}
        source = Path("/tmp/cmpunlocker")
        with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
             mock.patch.object(cli, "_cmpunlocker_source", return_value=(source, "abc123")), \
             mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             mock.patch.object(cli, "_run_installer") as installed:
            installed.return_value = {"returncode": 1, "output_tail": "build failed\n",
                                      "timed_out": False, "log_path": "/tmp/install.log"}
            failed = self.invoke("memory-enable", "--source", str(source), "--confirm",
                                 simulate=False, code=1)
            self.assertEqual(failed["result"]["expected_driver_memory_mib_after_boot"], 40960)
            self.assertIn("partial", failed["result"]["detail"])
            self.assertEqual(failed["result"]["installer_output_tail"], "build failed\n")
            installed.return_value = dict(installed.return_value, returncode=-9, timed_out=True)
            timed_out = self.invoke("memory-enable", "--source", str(source), "--confirm",
                                    simulate=False, code=1)
            self.assertIn("timed out", timed_out["result"]["detail"])
        states = [event["state"] for event in self.invoke("report", simulate=False)["events"]
                  if event["kind"] == "memory-enable"]
        self.assertEqual(states, ["failed", "failed"])

    def test_memory_enable_journals_failed_sudo_auth_without_running_installer(self):
        card = {"bdf": "0000:41:00.0", "vendor_id": "10de", "device_id": "20c2",
                "model": "CMP 170HX"}
        source = Path("/tmp/cmpunlocker")
        with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
             mock.patch.object(cli, "_cmpunlocker_source", return_value=(source, "abc123")), \
             mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as auth, \
             mock.patch.object(cli, "_run_installer") as installed:
            result = self.invoke("memory-enable", "--source", str(source), "--confirm",
                                 simulate=False, code=1)
        auth.assert_called_once_with(["sudo", "-v"], check=False, timeout=120)
        installed.assert_not_called()
        self.assertEqual(result["state"], "failed")
        self.assertIn("did not start", result["result"]["detail"])
        event = self.invoke("report", simulate=False)["events"][-1]
        self.assertEqual(event["state"], "failed")

    def test_cmpunlocker_source_requires_pinned_clean_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "install.sh").write_text("echo test\n")
            outputs = [str(source) + "\n", cli.CMPUNLOCKER_COMMIT + "\n", ""]
            with mock.patch.object(cli.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=value) for value in outputs]) as git:
                found, digest = cli._cmpunlocker_source(source)
            self.assertEqual(found, source)
            self.assertEqual(len(digest), 64)
            self.assertEqual(git.call_count, 3)
            for revision, status, message in [
                    ("0" * 40, "", "pinned commit"),
                    (cli.CMPUNLOCKER_COMMIT, " M install.sh", "local changes")]:
                outputs = [str(source) + "\n", revision + "\n", status]
                with mock.patch.object(cli.subprocess, "run", side_effect=[
                        subprocess.CompletedProcess([], 0, stdout=value) for value in outputs]):
                    with self.assertRaisesRegex(ValueError, message):
                        cli._cmpunlocker_source(source)

    def test_ff_burn_requires_explicit_interactive_reviewed_plan(self):
        card = {"bdf": "0000:41:00.0", "vendor_id": "10de", "device_id": "20c2",
                "model": "CMP 170HX"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            baseline = root / "baseline.json"
            baseline.write_text("{}")
            plan = root / "plan.json"
            manifest = {"schema": "ga100-public-ff-plan-v2", "bdf": card["bdf"],
                        "plan_id": "a" * 64, "status": "logical-plan-complete",
                        "records": [{"row": 300, "word": 1}], "features": ["memory"],
                        "route": "cmp", "public_sources_only": True}
            plan.write_text(json.dumps(manifest))
            journal = root / "burn.jsonl"
            args = ("memory-ff", "burn", "--source", str(source), "--baseline", str(baseline),
                    "--plan", str(plan), "--bdf", card["bdf"], "--plan-id", "a" * 64,
                    "--journal", str(journal), "--confirm-burn")
            with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
                 mock.patch.object(cli, "_ff_tools_source", return_value=(source, {"ffburn.py3": "h1", "ffplan.py3": "h2"})), \
                 mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as runner:
                self.assertIn("real GPU host", self.invoke(*args, code=2))
                self.assertIn("--confirm-burn", self.invoke(*args[:-1], simulate=False, code=2))
                self.assertIn("interactive terminal", self.invoke(*args, simulate=False, code=2))
                runner.assert_not_called()
                with mock.patch.object(cli.sys, "stdin", mock.Mock(isatty=lambda: True)):
                    wrong = list(args)
                    wrong[wrong.index("--plan-id") + 1] = "b" * 64
                    self.assertIn("match the reviewed plan", self.invoke(*wrong, simulate=False, code=2))
                    manifest["public_sources_only"] = False
                    plan.write_text(json.dumps(manifest))
                    self.assertIn("published mappings", self.invoke(*args, simulate=False, code=2))
                    manifest["public_sources_only"] = True
                    plan.write_text(json.dumps(manifest))
                    journal.write_text("existing attempt")
                    self.assertIn("new file", self.invoke(*args, simulate=False, code=2))
                    runner.assert_not_called()

    def test_ff_burn_delegates_three_confirmations_and_records_outcome(self):
        card = {"bdf": "0000:41:00.0", "vendor_id": "10de", "device_id": "20c2",
                "model": "CMP 170HX"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            baseline = root / "baseline.json"
            baseline.write_text("{}")
            plan = root / "plan.json"
            plan_id = "a" * 64
            plan.write_text(json.dumps({"schema": "ga100-public-ff-plan-v2", "bdf": card["bdf"],
                                        "plan_id": plan_id, "status": "logical-plan-complete",
                                        "records": [{"row": 300, "word": 1}], "features": ["memory"],
                                        "route": "cmp", "public_sources_only": True}))
            journal = root / "burn.jsonl"
            args = ("memory-ff", "burn", "--source", str(source), "--baseline", str(baseline),
                    "--plan", str(plan), "--bdf", card["bdf"], "--plan-id", plan_id,
                    "--journal", str(journal), "--confirm-burn")
            with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
                 mock.patch.object(cli, "_ff_tools_source", return_value=(source, {"ffburn.py3": "h1", "ffplan.py3": "h2"})) as checked, \
                 mock.patch.object(cli.sys, "stdin", mock.Mock(isatty=lambda: True)), \
                 mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as runner:
                done = self.invoke(*args, simulate=False)
                self.assertEqual(done["state"], "pending_cold_boot_verification")
                self.assertEqual(done["result"]["upstream_exit_code"], 0)
                self.assertFalse(journal.exists())  # Only the upstream burner creates this.
                self.assertEqual(checked.call_count, 2)
                command = runner.call_args.args[0]
                self.assertEqual(command[:5], ["sudo", sys.executable, "-I", "-S", str(source / "ffburn.py3")])
                self.assertEqual(command[-4:], ["--execute-plan", plan_id, "--journal", str(journal)])
                self.assertNotIn("input", runner.call_args.kwargs)
                self.assertNotIn("stdin", runner.call_args.kwargs)
                self.assertIs(runner.call_args.kwargs["stdout"], runner.call_args.kwargs["stderr"])
                self.assertEqual(self.invoke("report", simulate=False)["events"][-1]["state"],
                                 "pending_cold_boot_verification")
                runner.return_value = subprocess.CompletedProcess([], 1)
                failed = self.invoke(*args, simulate=False, code=1)
                self.assertEqual(failed["state"], "failed")
                self.assertIn("partial or unknown", failed["result"]["detail"])
                self.assertEqual(self.invoke("report", simulate=False)["events"][-1]["state"], "failed")

    def test_ff_burn_rejects_unsupported_variant_before_source_or_sudo(self):
        card = {"bdf": "0000:41:00.0", "vendor_id": "10de", "device_id": "2082",
                "model": "CMP 170HX"}
        with mock.patch.object(cli.gpu, "discover", return_value=[card]), \
             mock.patch.object(cli, "_ff_tools_source") as source, \
             mock.patch.object(cli.subprocess, "run") as runner:
            args = ("memory-ff", "burn", "--source", "/tmp/source", "--baseline", "/tmp/base.json",
                    "--plan", "/tmp/plan.json", "--bdf", card["bdf"], "--plan-id", "a",
                    "--journal", "/tmp/burn.jsonl", "--confirm-burn")
            self.assertIn("2082 is unsupported", self.invoke(*args, simulate=False, code=2))
            source.assert_not_called()
            runner.assert_not_called()

    def test_ff_tools_source_requires_pinned_clean_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for name in ("ffburn.py3", "ffplan.py3"):
                (source / name).write_text("pass\n")
            outputs = [str(source) + "\n", cli.FF_TOOLS_COMMIT + "\n", ""]
            fake_hash = hashlib.sha256(b"pass\n").hexdigest()
            with mock.patch.dict(cli.FF_TOOLS_SCRIPT_SHA256,
                                 {"ffburn.py3": fake_hash, "ffplan.py3": fake_hash}, clear=True):
                with mock.patch.object(cli.subprocess, "run", side_effect=[
                        subprocess.CompletedProcess([], 0, stdout=value) for value in outputs]):
                    actual, hashes = cli._ff_tools_source(source)
                self.assertEqual(actual, source)
                self.assertEqual(set(hashes), {"ffburn.py3", "ffplan.py3"})
                self.assertTrue(all(len(value) == 64 for value in hashes.values()))
                for revision, status, message in [("0" * 40, "", "pinned commit"),
                                                   (cli.FF_TOOLS_COMMIT, " M ffburn.py3", "local changes")]:
                    outputs = [str(source) + "\n", revision + "\n", status]
                    with mock.patch.object(cli.subprocess, "run", side_effect=[
                            subprocess.CompletedProcess([], 0, stdout=value) for value in outputs]):
                        with self.assertRaisesRegex(ValueError, message):
                            cli._ff_tools_source(source)
            outputs = [str(source) + "\n", cli.FF_TOOLS_COMMIT + "\n", ""]
            with mock.patch.object(cli.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=value) for value in outputs]):
                with self.assertRaisesRegex(ValueError, "script hashes differ"):
                    cli._ff_tools_source(source)

    def test_ff_plan_runs_offline_memory_only_and_preserves_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, output = root / "source", root / "capture.json", root / "plan.json"
            source.mkdir()
            baseline.write_text("{}")
            hashes = {"ffplan.py3": "planner-hash", "ffburn.py3": "burner-hash"}
            def run_planner(command, **_kwargs):
                self.assertEqual(command[1:3], ["-I", "-S"])
                self.assertEqual(command[4:6], ["plan", "--baseline"])
                self.assertEqual(command[-6:], ["--changes", "memory", "--route", "cmp", "--output", str(output)])
                output.write_text(json.dumps({"status": "incomplete", "features": ["memory"],
                                              "route": "cmp", "public_sources_only": True,
                                              "records": [], "plan_id": "id",
                                              "missing_evidence": ["IFF boundary"]}))
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="")
            with mock.patch.object(cli, "_ff_tools_source", return_value=(source, hashes)), \
                 mock.patch.object(cli.gpu, "discover", side_effect=AssertionError("GPU access")), \
                 mock.patch.object(cli.subprocess, "run", side_effect=run_planner) as runner:
                result = self.invoke("memory-ff", "plan", "--source", str(source),
                                     "--baseline", str(baseline), "--output", str(output),
                                     simulate=False, code=1)
                self.assertEqual(result["state"], "incomplete")
                self.assertEqual(result["missing_evidence"], ["IFF boundary"])
                self.assertEqual(result["record_count"], 0)
                self.assertTrue(output.exists())
                self.assertIn("new file", self.invoke("memory-ff", "plan", "--source", str(source),
                                                    "--baseline", str(baseline), "--output", str(output),
                                                    simulate=False, code=2))
                runner.assert_called_once()
            self.assertFalse(self.db.exists())

    def test_ff_simulate_regenerates_memory_plan_offline_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, plan, output = (root / name for name in
                                              ("source", "capture.json", "plan.json", "simulation.json"))
            source.mkdir()
            baseline.write_text("{}")
            manifest = {"features": ["memory"], "route": "cmp", "public_sources_only": True,
                        "plan_id": "reviewed-id", "records": [{"row": 300, "word": 123}]}
            plan.write_text(json.dumps(manifest))
            replay = {"simulation_only": True, "plan_id": "reviewed-id", "records_applied": 1,
                      "cold_boot_verified": False, "working_features_verified": False}
            hashes = {"ffplan.py3": "planner-hash", "ffburn.py3": "burner-hash"}
            with mock.patch.object(cli, "_ff_tools_source", return_value=(source, hashes)), \
                 mock.patch.object(cli.gpu, "discover", side_effect=AssertionError("GPU access")), \
                 mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess(
                     [], 0, stdout=json.dumps(replay), stderr="")) as runner:
                result = self.invoke("memory-ff", "simulate", "--source", str(source),
                                     "--baseline", str(baseline), "--plan", str(plan),
                                     "--output", str(output), simulate=False)
                self.assertEqual(result["state"], "logical-replay-complete")
                self.assertEqual(result["records_applied"], 1)
                self.assertEqual(json.loads(output.read_text()), replay)
                self.assertIn("simulate", runner.call_args.args[0])
                self.assertIn("new file", self.invoke("memory-ff", "simulate", "--source", str(source),
                                                    "--baseline", str(baseline), "--plan", str(plan),
                                                    "--output", str(output), simulate=False, code=2))
                runner.assert_called_once()
            self.assertFalse(self.db.exists())

    def test_ff_verify_checks_post_boot_rows_shadows_and_identity_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, plan, post = (root / name for name in
                                            ("source", "baseline.json", "plan.json", "post.json"))
            source.mkdir()
            uuid = "GPU-12345678-1234-5678-9abc-123456789abc"
            before = [0] * 512
            after = before.copy()
            after[300] = 0x00010001

            def capture(rows, time, boot, field):
                options = {"CRC_STATUS": "0x55", "NV_FUSE_OPT_FBIO_DISABLE": field,
                           "FUSECTRL": "0xe0040000", "FUSEDEBUG": "0x00000000",
                           "DISABLE_SW_OVERRIDE_STATUS": "0x00000001",
                           "EN_SW_OVERRIDE": "0x00000000"}
                return {"schema": "ga100-public-capture-v1", "bdf": "0000:01:00.0",
                        "pci_vendor": "0x10de", "pci_device": "0x20c2", "gpu_uuid": uuid,
                        "gpu_uuid_observation": {"value": uuid, "source": "user-supplied BDF mapping",
                                                 "live_verified": False},
                        "utc": time, "boot_id": boot,
                        "complete": True, "ok": True, "non_programming": True, "raw_requested": True,
                        "physical_rows_captured": True, "options_match": True, "raw_match": True,
                        "raw_address_restored": True, "options": [options, options.copy()],
                        "raw_rows": [rows, rows.copy()]}

            baseline.write_text(json.dumps(capture(before, "2026-11-18T10:00:00+00:00", "boot-1", 1)))
            post_capture = capture(after, "2026-11-18T11:00:00+00:00", "boot-2", 0)
            post.write_text(json.dumps(post_capture))
            plan.write_text(json.dumps({"schema": "ga100-public-ff-plan-v2",
                                        "status": "logical-plan-complete", "features": ["memory"],
                                        "route": "cmp", "public_sources_only": True,
                                        "bdf": "0000:01:00.0", "plan_id": "reviewed-id",
                                        "records": [{"row": 300, "word": 0x00010001}]}))
            replay = {"simulation_only": True, "plan_id": "reviewed-id", "records_applied": 1,
                      "public_fields": {"FBIO_DISABLE": 0}}

            def read_evidence(path):
                raw = json.loads(path.read_text())
                self.assertEqual(len(raw["options"]), 2)
                self.assertEqual(raw["options"][0], raw["options"][1])
                self.assertEqual(len(raw["raw_rows"]), 2)
                self.assertTrue(all(len(p) == 512 for p in raw["raw_rows"]))
                if raw["raw_rows"][0] != raw["raw_rows"][1]:
                    raise ValueError("Raw passes disagree")
                if raw["options"][0]["CRC_STATUS"] != "0x55":
                    raise ValueError("CRC is not clean")
                return {"schema": "raw_rows", "options_repeated": True, "rows_repeated": True,
                        "bdf": raw["bdf"], "uuid": raw["gpu_uuid"].lower(),
                        "rows": dict(enumerate(raw["raw_rows"][0])),
                        "opts": {"NV_FUSE_OPT_FBIO_DISABLE": raw["options"][0]["NV_FUSE_OPT_FBIO_DISABLE"]}}

            arguments = ("memory-ff", "verify", "--source", str(source), "--baseline", str(baseline),
                         "--plan", str(plan), "--post", str(post))
            with mock.patch.object(cli, "_ff_tools_source", return_value=(source, {"ffplan.py3": "hash"})), \
                 mock.patch.object(cli.runpy, "run_path", return_value={
                     "read_evidence": read_evidence, "option_name": lambda field: "NV_FUSE_OPT_" + field}), \
                 mock.patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess(
                     [], 0, stdout=json.dumps(replay), stderr="")) as runner, \
                 mock.patch.object(cli.gpu, "discover", side_effect=AssertionError("GPU access")):
                result = self.invoke(*arguments, simulate=False)
                self.assertEqual(result["state"], "ff-replay-observed")
                self.assertEqual(result["planned_rows_matching"], 1)
                self.assertTrue(result["host_boot_id_changed"])
                self.assertIsNone(result["verified_memory_mib"])
                self.assertEqual(runner.call_args.args[0][1:3], ["-I", "-S"])

                post_capture["raw_rows"][0][300] = 0
                post_capture["raw_rows"][1][300] = 0
                post_capture["raw_rows"][0][310] = 5
                post_capture["raw_rows"][1][310] = 5
                post_capture["options"][0]["NV_FUSE_OPT_FBIO_DISABLE"] = 1
                post_capture["options"][1]["NV_FUSE_OPT_FBIO_DISABLE"] = 1
                post.write_text(json.dumps(post_capture))
                mismatch = self.invoke(*arguments, simulate=False, code=1)
                self.assertEqual(mismatch["state"], "ff-replay-mismatch")
                self.assertEqual(mismatch["planned_row_mismatches"][0]["row"], 300)
                self.assertEqual(mismatch["unexpected_changed_rows"][0]["row"], 310)
                self.assertEqual(mismatch["memory_option_mismatches"][0]["field"], "FBIO_DISABLE")

                post_capture["gpu_uuid_observation"]["value"] = "GPU-ffffffff-ffff-ffff-ffff-ffffffffffff"
                post.write_text(json.dumps(post_capture))
                self.assertIn("GPU UUID", self.invoke(*arguments, simulate=False, code=2))
            self.assertFalse(self.db.exists())

    def test_installer_streams_to_stderr_and_retains_bounded_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            output = io.StringIO()
            program = ("from pathlib import Path; "
                       "Path('logs').mkdir(); Path('logs/install_test.log').write_text('full log'); "
                       "print('start'); print('x' * 5000); print('done')")
            original_popen = subprocess.Popen
            with contextlib.redirect_stderr(output), \
                 mock.patch.object(cli.subprocess, "Popen", wraps=original_popen) as launch:
                result = cli._run_installer(
                    [sys.executable, "-u", "-c", program],
                    source, timeout=5)
            self.assertIs(launch.call_args.kwargs["preexec_fn"], os.setpgrp)
            self.assertNotIn("start_new_session", launch.call_args.kwargs)
            self.assertEqual(result["returncode"], 0)
            self.assertIn("start", output.getvalue())
            self.assertTrue(output.getvalue().endswith("done\n"))
            self.assertLessEqual(len(result["output_tail"]), 4000)
            self.assertTrue(result["output_tail"].endswith("done\n"))
            self.assertEqual(result["log_path"], str(source / "logs/install_test.log"))

    def test_installer_timeout_stops_its_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                result = cli._run_installer(
                    [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(10)"],
                    Path(directory), timeout=0.1)
            self.assertTrue(result["timed_out"])
            self.assertNotEqual(result["returncode"], 0)
            self.assertIn("started", result["output_tail"])

    def test_memory_check_limits_automatic_targets_and_keeps_each_result(self):
        cards = [{"bdf": f"0000:{bus}:00.0", "uuid": f"GPU-{bus}", "cmp_170hx": bus != "03",
                  "telemetry": {"memory_total_mib": 8192}} for bus in ["01", "02", "03"]]
        with mock.patch.object(cli.gpu, "discover", return_value=cards), \
             mock.patch.object(cli.gpu, "nvlink_status", return_value={"status": "unavailable"}), \
             mock.patch.object(cli.gpu, "ecc_after_check", side_effect=[
                 {"delta": {}}, {"delta": {"uncorrected": 1}}]), \
             mock.patch.object(cli.workloads, "check_memory", side_effect=[
                 {"status": "unavailable", "detail": "busy"},
                 {"status": "passed", "verified_mib": 7000}]) as worker:
            scan = self.invoke("memory-check", simulate=False, code=1)
        self.assertEqual(scan["state"], "failed")
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(scan["cards"][0]["state"], "unavailable")
        second = scan["cards"][1]["result"]
        self.assertEqual(second["memory_test_status"], "passed")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["verified_mib"], 7000)

    def test_inventory_report_note_and_compare(self):
        self.assertIn("Multiple GPUs", self.invoke("gpu", "inspect", code=2))
        one = self.invoke("gpu", "inspect", "--gpu", "0000:41:00.0")
        two = self.invoke("gpu", "inspect", "--gpu", "0000:81:00.0")
        note = self.invoke("note", "--card", one["card_id"], "--topic", "physical", "Intake photo 1")
        report = self.invoke("report", "--card", one["card_id"])
        self.assertEqual(report["cards"][0]["baseline"], one["gpu"])
        self.assertEqual(report["events"][-1]["id"], note["event_id"])
        self.assertIn("Intake photo 1", self.invoke("report", as_json=False))
        result = self.invoke("compare", str(one["event_id"]), str(two["event_id"]))
        self.assertEqual(result["measurements"]["telemetry.memory_total_mib"], [8192, 10240])
        self.assertEqual(self.invoke("report", simulate=False)["events"], [])

    @mock.patch("ga100ctl.gpu.discover", side_effect=AssertionError("live device access"))
    @mock.patch("ga100ctl.gpu._smi", side_effect=AssertionError("live driver access"))
    def test_simulated_links_require_selected_card(self, _smi, _discover):
        self.assertIn("Multiple GPUs", self.invoke("gpu", "links", code=2))
        result = self.invoke("gpu", "links", "--gpu", "0000:41:00.0")
        self.assertEqual(result["state"], "simulated")
        self.assertEqual(result["pcie"]["current_link_width"], "4")
        self.assertEqual(result["nvlink"]["status"], "unavailable")
        self.assertFalse(self.db.exists())

    @mock.patch("ga100ctl.gpu.discover")
    @mock.patch("ga100ctl.gpu._smi")
    def test_live_links_distinguish_unavailable_and_inactive(self, smi, discover):
        card = {"bdf": "0000:41:00.0", "uuid": "GPU-TEST", "pcie_link": {}}
        discover.return_value = [card]
        smi.return_value = "GPU 0: Example GPU\n  Link 0: [Not Supported]\n"
        absent = self.invoke("gpu", "links", "--gpu", card["bdf"], simulate=False, code=1)
        self.assertEqual(absent["state"], "unavailable")
        smi.assert_called_once_with("nvlink", "-s", "-i", card["uuid"])
        smi.return_value = "GPU 0: Example GPU\n  Link 0: <inactive>\n"
        inactive = self.invoke("gpu", "links", "--gpu", card["uuid"], simulate=False)
        self.assertEqual(inactive["nvlink"]["status"], "observed")
        self.assertIn("<inactive>", inactive["nvlink"]["detail"])
        self.assertEqual(inactive["pcie"]["status"], "unavailable")
        self.assertFalse(self.db.exists())

    @mock.patch("ga100ctl.cli.workloads.validate", side_effect=AssertionError("live workload"))
    def test_simulated_workload_journal(self, _):
        result = self.invoke("validate", "--gpu", "0000:41:00.0")
        self.assertEqual(result["state"], "simulated")
        self.assertEqual(self.invoke("report")["events"][-1]["inputs"]["memory_mib"], 64)
        self.assertIn("distinct", self.invoke("benchmark", "--backend", "peer-copy", "--gpu",
                                               "0000:41:00.0", "--peer", "0000:41:00.0", code=2))

    def test_peer_run_appears_in_both_card_histories(self):
        result = self.invoke("benchmark", "--backend", "peer-copy", "--gpu", "0000:41:00.0",
                             "--peer", "0000:81:00.0")
        report = self.invoke("report")
        for card in report["cards"]:
            events = self.invoke("report", "--card", card["id"])["events"]
            self.assertEqual(events[-1]["id"], result["event_id"])
        self.assertEqual(len(report["cards"]), 2)

    def test_report_before_first_observation_is_readonly(self):
        self.assertEqual(self.invoke("report")["cards"], [])
        self.assertFalse(self.db.exists())

    def test_operator_label_when_driver_identity_is_missing(self):
        card = {"uuid": None, "serial": None, "bdf": "0000:41:00.0", "model": "CMP"}
        with mock.patch.object(cli.gpu, "discover", side_effect=[[card.copy()], [dict(card, bdf="0000:42:00.0")]]):
            first = self.invoke("gpu", "inspect", "--asset-tag", "LAB-017", simulate=False)
            second = self.invoke("gpu", "inspect", "--asset-tag", "LAB-017", simulate=False)
        self.assertEqual(first["card_id"], second["card_id"])
        self.assertEqual(second["gpu"]["bdf"], "0000:42:00.0")

    def test_live_workload_failure_retained(self):
        card = {"uuid": "test", "bdf": "0000:41:00.0", "model": "test"}
        with mock.patch.object(cli.gpu, "discover", return_value=[card]):
            result = self.invoke("validate", "--gpu", card["bdf"], simulate=False, code=1)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(self.invoke("report", simulate=False)["events"][-1]["state"], "unavailable")

    def test_flash_requires_confirmation_and_keeps_copy_outcome(self):
        args = ["pico", "flash", "--board", "pico2_w", "--serial", "BOOT", "--build-dir", "/tmp/unused"]
        with mock.patch.object(cli.pico, "flash", return_value={"status": "copied_runtime_unverified"}) as flash:
            self.assertIn("--confirm", self.invoke(*args, code=2, simulate=False))
            flash.assert_not_called()
            self.invoke(*args, "--confirm", simulate=False, code=1)
        event = self.invoke("report", simulate=False)["events"][-1]
        self.assertEqual(event["state"], "copied_runtime_unverified")
        self.assertEqual(event["inputs"]["serial"], "BOOT")

    def test_interruption_and_failure_journaled(self):
        with contextlib.closing(History(self.db, "live")) as history:
            for exc, state in [(KeyboardInterrupt(), "interrupted"), (ValueError("test"), "failed")]:
                with self.assertRaises(type(exc)):
                    cli.run_event(history, "test", {}, mock.Mock(side_effect=exc))
                self.assertEqual(history.report()["events"][-1]["state"], state)
