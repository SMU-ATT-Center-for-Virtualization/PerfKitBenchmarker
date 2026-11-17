# Copyright 2026 PerfKitBenchmarker Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for the aiperf_cpu benchmark."""

import json
import os
import shlex
import shutil
from unittest import mock

from absl.testing import absltest
from absl.testing import flagsaver
from perfkitbenchmarker import errors
from perfkitbenchmarker import test_util
from perfkitbenchmarker import virtual_machine
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_benchmarks import aiperf_cpu_benchmark
from tests import pkb_common_test_case

_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "../data/aiperf_profile_export_sample.json",
)


class AiperfCpuBenchmarkTest(
    pkb_common_test_case.PkbCommonTestCase, test_util.SamplesTestMixin
):

  @flagsaver.flagsaver(aiperf_sweep="concurrency")
  def testGetSweepPointsDefaults(self):
    points = aiperf_cpu_benchmark.GetSweepPoints()

    self.assertEqual(
        [point.value for point in points],
        [1.0, 2.0, 4.0, 8.0],
    )
    self.assertEqual(
        [point.label for point in points],
        ["c1", "c2", "c4", "c8"],
    )
    self.assertEqual(
        [point.dimension for point in points],
        ["concurrency", "concurrency", "concurrency", "concurrency"],
    )

  @flagsaver.flagsaver(
      aiperf_sweep="request_rate",
      aiperf_request_rate=["1", "2.5"],
  )
  def testGetSweepPointsRequestRate(self):
    points = aiperf_cpu_benchmark.GetSweepPoints()

    self.assertEqual([point.value for point in points], [1.0, 2.5])
    self.assertEqual([point.label for point in points], ["r1", "r2_5"])
    self.assertEqual(
        [point.dimension for point in points],
        ["request_rate", "request_rate"],
    )

  @flagsaver.flagsaver(
      aiperf_sweep="request_rate",
      aiperf_request_rate=[],
  )
  def testGetSweepPointsRequiresValues(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.GetSweepPoints()

  @flagsaver.flagsaver(aiperf_concurrency=["1", "not-a-number"])
  def testGetSweepPointsRejectsInvalidValues(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.GetSweepPoints()

  @flagsaver.flagsaver(aiperf_concurrency=["1", "2.5"])
  def testGetSweepPointsRejectsFractionalConcurrency(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.GetSweepPoints()

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_fixed_schedule=True,
      aiperf_concurrency=["1", "2"],
  )
  def testCheckPrerequisitesRejectsFixedScheduleSweep(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.CheckPrerequisites(None)

  @flagsaver.flagsaver(aiperf_fixed_schedule=True)
  def testCheckPrerequisitesRejectsFixedScheduleWithSynthetic(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.CheckPrerequisites(None)

  @flagsaver.flagsaver(
      aiperf_dataset="synthetic",
      aiperf_input_tokens_mean=512,
      aiperf_output_tokens_mean=128,
  )
  def testBuildProfileCommandSynthetic(self):
    point = aiperf_cpu_benchmark.SweepPoint("concurrency", 8.0, "c8")

    command = aiperf_cpu_benchmark.BuildProfileCommand(
        "http://10.0.0.1:8080",
        point,
        "/tmp/aiperf_artifacts/run/c8",
    )
    args = shlex.split(command)

    self.assertIn("--concurrency", args)
    self.assertEqual(args[args.index("--concurrency") + 1], "8")
    self.assertIn("--streaming", args)
    self.assertIn("--isl", args)
    self.assertEqual(args[args.index("--isl") + 1], "512")
    self.assertIn("--osl", args)
    self.assertEqual(args[args.index("--osl") + 1], "128")
    self.assertIn("--request-count", args)
    self.assertIn("--warmup-request-count", args)
    self.assertIn("--random-seed", args)
    self.assertNotIn("--input-file", args)

  @flagsaver.flagsaver(aiperf_dataset="agentic_code")
  def testBuildProfileCommandAgenticCode(self):
    point = aiperf_cpu_benchmark.SweepPoint("concurrency", 1.0, "c1")

    command = aiperf_cpu_benchmark.BuildProfileCommand(
        "http://10.0.0.1:8080",
        point,
        "/tmp/aiperf_artifacts/run/c1",
        dataset_path="/tmp/aiperf_datasets/run/dataset.jsonl",
    )
    args = shlex.split(command)

    self.assertIn("--input-file", args)
    self.assertEqual(
        args[args.index("--input-file") + 1],
        "/tmp/aiperf_datasets/run/dataset.jsonl",
    )
    self.assertIn("--custom-dataset-type", args)
    self.assertEqual(
        args[args.index("--custom-dataset-type") + 1],
        "mooncake_trace",
    )
    self.assertIn("--no-fixed-schedule", args)
    self.assertNotIn("--fixed-schedule", args)
    self.assertNotIn("--isl", args)
    self.assertNotIn("--osl", args)

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_fixed_schedule=True,
      aiperf_concurrency=["1"],
  )
  def testBuildProfileCommandFixedSchedule(self):
    point = aiperf_cpu_benchmark.SweepPoint("concurrency", 1.0, "c1")

    command = aiperf_cpu_benchmark.BuildProfileCommand(
        "http://10.0.0.1:8080",
        point,
        "/tmp/aiperf_artifacts/run/c1",
        dataset_path="/tmp/aiperf_datasets/run/dataset.jsonl",
    )
    args = shlex.split(command)

    self.assertIn("--fixed-schedule", args)
    self.assertNotIn("--no-fixed-schedule", args)
    self.assertNotIn("--ignore-trace-delays", args)

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_fixed_schedule=False,
      aiperf_concurrency=["1"],
  )
  def testBuildProfileCommandIgnoresTraceDelays(self):
    point = aiperf_cpu_benchmark.SweepPoint("concurrency", 1.0, "c1")

    command = aiperf_cpu_benchmark.BuildProfileCommand(
        "http://10.0.0.1:8080",
        point,
        "/tmp/aiperf_artifacts/run/c1",
        dataset_path="/tmp/aiperf_datasets/run/dataset.jsonl",
    )
    args = shlex.split(command)

    self.assertIn("--no-fixed-schedule", args)
    self.assertIn("--ignore-trace-delays", args)

  @flagsaver.flagsaver(
      aiperf_model_size="12B",
      aiperf_model_quantization="Q8_0",
  )
  def testBuildProfileCommandUsesSelectedProfile(self):
    point = aiperf_cpu_benchmark.SweepPoint("concurrency", 1.0, "c1")

    command = aiperf_cpu_benchmark.BuildProfileCommand(
        "http://10.0.0.1:8080",
        point,
        "/tmp/aiperf_artifacts/run/c1",
    )
    args = shlex.split(command)

    self.assertEqual(
        args[args.index("--model") + 1],
        "gemma-4-12B-it",
    )
    self.assertEqual(
        args[args.index("--tokenizer") + 1],
        "google/gemma-4-12B-it",
    )

  def testModelProfilesCoverAllSizes(self):
    self.assertEqual(
        set(aiperf_cpu_benchmark.MODEL_PROFILES),
        {"E2B", "E4B", "12B", "26B-A4B", "31B"},
    )
    for size, profile in aiperf_cpu_benchmark.MODEL_PROFILES.items():
      with self.subTest(size=size):
        self.assertEqual(profile.size, size)
        self.assertEqual(set(profile.quant_sizes_gb), {"Q4_0", "Q8_0"})
        self.assertIn(f"gemma-4-{size}-it", profile.repo)
        self.assertTrue(profile.max_context > 0)
        self.assertTrue(profile.kv_bytes_per_token > 0)

  @flagsaver.flagsaver(aiperf_model_size="31B")
  def testGetSweepPointsUsesProfileDefaults(self):
    points = aiperf_cpu_benchmark.GetSweepPoints()

    self.assertEqual([point.value for point in points], [1.0, 2.0])
    self.assertEqual([point.label for point in points], ["c1", "c2"])

  @flagsaver.flagsaver(
      aiperf_model_size="12B",
      aiperf_model_quantization="Q8_0",
  )
  def testEffectiveProfileSettings(self):
    profile = aiperf_cpu_benchmark.GetModelProfile()

    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveModelRepo(profile),
        "ggml-org/gemma-4-12B-it-GGUF",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveModelFile(profile),
        "gemma-4-12B-it-Q8_0.gguf",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveModelAlias(profile),
        "gemma-4-12B-it",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveTokenizer(profile),
        "google/gemma-4-12B-it",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveServerCtxPerSlot(profile),
        16384,
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveServerStartupTimeout(profile),
        600,
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveQuantization(profile),
        "Q8_0",
    )

  @flagsaver.flagsaver(
      aiperf_model_size="12B",
      aiperf_model_repo="custom/repo",
      aiperf_model_file="custom-model.gguf",
      aiperf_model_alias="custom-alias",
      aiperf_tokenizer="custom-tokenizer",
  )
  def testExplicitModelFlagsOverrideProfile(self):
    profile = aiperf_cpu_benchmark.GetModelProfile()

    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveModelRepo(profile),
        "custom/repo",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveModelFile(profile),
        "custom-model.gguf",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveModelAlias(profile),
        "custom-alias",
    )
    self.assertEqual(
        aiperf_cpu_benchmark._EffectiveTokenizer(profile),
        "custom-tokenizer",
    )

  @flagsaver.flagsaver(
      aiperf_model_size="12B",
      aiperf_concurrency=["1", "2"],
  )
  def testStartServerUsesPerSlotContext(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.NumCpusForBenchmark.return_value = 16

    aiperf_cpu_benchmark._StartServer(server_vm, "/opt/pkb/models/model.gguf")

    nohup_calls = [
        call.args[0]
        for call in server_vm.RemoteCommand.call_args_list
        if call.args and call.args[0].startswith("nohup ")
    ]
    self.assertTrue(nohup_calls)
    args = shlex.split(nohup_calls[0].split(" > ")[0])
    args = args[1:]  # Drop the leading 'nohup'.
    self.assertEqual(
        args[args.index("--ctx-size") + 1],
        str(16384 * 2),
    )
    self.assertEqual(args[args.index("--parallel") + 1], "2")
    self.assertEqual(
        args[args.index("--alias") + 1],
        "gemma-4-12B-it",
    )

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_model_size="31B",
  )
  def testPrepareDatasetUsesProfileDefaults(self):
    client_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    client_vm.RemoteCommand.side_effect = [
        ("", ""),
        ("", ""),
        ("", ""),
        ("/tmp/aiperf_datasets/run/dataset.jsonl", ""),
    ]

    dataset_path = aiperf_cpu_benchmark.PrepareDataset(client_vm)

    self.assertEqual(dataset_path, "/tmp/aiperf_datasets/run/dataset.jsonl")
    synthesize_command = client_vm.RemoteCommand.call_args_list[2].args[0]
    self.assertIn("--max-isl 7168", synthesize_command)
    self.assertIn("--max-osl 128", synthesize_command)

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_model_size="31B",
      aiperf_agentic_max_isl=10000,
  )
  def testCheckPrerequisitesRejectsIslLargerThanContext(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.CheckPrerequisites(None)

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_model_size="E2B",
      aiperf_agentic_max_isl=200000,
  )
  def testCheckPrerequisitesRejectsIslLargerThanModelLimit(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.CheckPrerequisites(None)

  @flagsaver.flagsaver(
      aiperf_model_size="31B",
      aiperf_model_quantization="Q8_0",
      aiperf_server_ctx_size=20000,
      aiperf_concurrency=["1", "2"],
  )
  def testCheckPrerequisitesRejectsExcessiveMemory(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_cpu_benchmark.CheckPrerequisites(None)

  @flagsaver.flagsaver(
      aiperf_model_size="31B",
      aiperf_model_quantization="Q8_0",
  )
  def testCheckPrerequisitesAccepts31BQ8Defaults(self):
    aiperf_cpu_benchmark.CheckPrerequisites(None)

  @flagsaver.flagsaver(aiperf_sweep="request_rate")
  def testBuildProfileCommandRequestRate(self):
    point = aiperf_cpu_benchmark.SweepPoint("request_rate", 4.5, "r4_5")

    command = aiperf_cpu_benchmark.BuildProfileCommand(
        "http://10.0.0.1:8080",
        point,
        "/tmp/aiperf_artifacts/run/r4_5",
    )
    args = shlex.split(command)

    self.assertIn("--request-rate", args)
    self.assertEqual(args[args.index("--request-rate") + 1], "4.5")
    self.assertNotIn("--concurrency", args)

  def testParseExport(self):
    with open(_FIXTURE, encoding="utf-8") as f:
      data = json.load(f)

    samples = aiperf_cpu_benchmark.ParseExport(
        data,
        {"aiperf_url": "http://10.0.0.1:8080"},
    )
    samples_by_name = {s.metric: s for s in samples}

    ttft_p99 = samples_by_name["time_to_first_token_p99"]
    self.assertEqual(ttft_p99.value, 149.0)
    self.assertEqual(ttft_p99.unit, "ms")
    self.assertEqual(
        ttft_p99.metadata["aiperf_metric"],
        "time_to_first_token",
    )
    self.assertEqual(ttft_p99.metadata["aiperf_stat"], "p99")
    self.assertEqual(ttft_p99.metadata["aiperf_metric_count"], 100)
    self.assertEqual(ttft_p99.metadata["aiperf_metric_sum"], 10000.0)
    self.assertEqual(ttft_p99.metadata["aiperf_schema_version"], "1.7")
    self.assertEqual(
        ttft_p99.metadata["aiperf_benchmark_id"],
        "test-benchmark-id",
    )
    self.assertEqual(
        ttft_p99.metadata["aiperf_url"],
        "http://10.0.0.1:8080",
    )

    self.assertIn("request_throughput_avg", samples_by_name)
    self.assertNotIn("request_throughput_p99", samples_by_name)
    self.assertIn("error_count", samples_by_name)
    self.assertEqual(samples_by_name["error_count"].value, 0.0)
    self.assertEqual(samples_by_name["error_count"].unit, "count")

    self.assertNotIn("time_to_first_token_count", samples_by_name)
    self.assertNotIn("time_to_first_token_sum", samples_by_name)
    self.assertNotIn("schema_version_avg", samples_by_name)
    self.assertNotIn("run_info_avg", samples_by_name)

  def testParseExportCountsErrors(self):
    with open(_FIXTURE, encoding="utf-8") as f:
      data = json.load(f)
    data["error_summary"] = [
        {"error_details": {"code": 500}, "count": 3},
        {"error_details": {"code": 503}, "count": 2},
    ]

    samples = aiperf_cpu_benchmark.ParseExport(data, {})
    samples_by_name = {s.metric: s for s in samples}

    self.assertEqual(samples_by_name["error_count"].value, 5.0)

  @flagsaver.flagsaver(aiperf_dataset="synthetic")
  def testPrepareDatasetSynthetic(self):
    client_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)

    self.assertIsNone(aiperf_cpu_benchmark.PrepareDataset(client_vm))
    client_vm.RemoteCommand.assert_called_once_with(
        "rm -rf /tmp/aiperf_datasets"
    )

  @flagsaver.flagsaver(
      aiperf_dataset="agentic_code",
      aiperf_agentic_num_sessions=7,
      aiperf_agentic_seed=99,
  )
  def testPrepareDatasetAgenticCode(self):
    client_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    client_vm.RemoteCommand.side_effect = [
        ("", ""),
        ("", ""),
        ("", ""),
        ("/tmp/aiperf_datasets/run/dataset.jsonl", ""),
    ]

    dataset_path = aiperf_cpu_benchmark.PrepareDataset(client_vm)

    self.assertEqual(dataset_path, "/tmp/aiperf_datasets/run/dataset.jsonl")
    synthesize_command = client_vm.RemoteCommand.call_args_list[2].args[0]
    self.assertIn("agentic-code", synthesize_command)
    self.assertIn("--num-sessions 7", synthesize_command)
    self.assertIn("--seed 99", synthesize_command)

  def testRunProfilePoint(self):
    client_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)

    def _remote_command(command, **_):
      if command.startswith("find "):
        return (
            "/tmp/aiperf_artifacts/run/c1/profile_export_aiperf.json",
            "",
        )
      return "", ""

    def _pull_file(local_path, remote_path):
      del remote_path
      shutil.copy(_FIXTURE, local_path)

    client_vm.RemoteCommand.side_effect = _remote_command
    client_vm.PullFile.side_effect = _pull_file
    point = aiperf_cpu_benchmark.SweepPoint("concurrency", 1.0, "c1")
    temp_dir = self.create_tempdir().full_path

    with mock.patch.object(
        vm_util,
        "GetTempDir",
        return_value=temp_dir,
    ):
      samples = aiperf_cpu_benchmark.RunProfilePoint(
          client_vm,
          "http://10.0.0.1:8080",
          point,
          "/tmp/aiperf_artifacts/run/c1",
          None,
          {"aiperf_url": "http://10.0.0.1:8080"},
          0,
      )

    self.assertTrue(samples)
    self.assertEqual(
        samples[0].metadata["sweep_dimension"],
        "concurrency",
    )
    self.assertEqual(samples[0].metadata["concurrency"], 1.0)
    self.assertEqual(samples[0].metadata["point_index"], 0)
    self.assertEqual(samples[0].metadata["point_label"], "c1")


if __name__ == "__main__":
  absltest.main()
