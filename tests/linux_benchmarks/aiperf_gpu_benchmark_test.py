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
"""Tests for the aiperf_gpu benchmark."""

import shlex
from unittest import mock

from absl.testing import absltest
from absl.testing import flagsaver
from perfkitbenchmarker import errors
from perfkitbenchmarker import virtual_machine
from perfkitbenchmarker.linux_benchmarks import aiperf_gpu_benchmark
from tests import pkb_common_test_case


def _BenchmarkConfig(machine_type: str = "g2-standard-48"):
  return mock.Mock(
      vm_groups={
          "server": mock.Mock(vm_spec=mock.Mock(machine_type=machine_type))
      }
  )


class AiperfGpuBenchmarkTest(pkb_common_test_case.PkbCommonTestCase):

  def testDefaultConfig(self):
    config = aiperf_gpu_benchmark.GetConfig({})
    server = config["vm_groups"]["server"]["vm_spec"]["GCP"]

    self.assertEqual(server["machine_type"], "g2-standard-48")
    self.assertEqual(server["zone"], "asia-northeast1-b")
    self.assertEqual(server["boot_disk_size"], 128)
    self.assertIsNone(server["image"])
    self.assertEqual(config["flags"]["llama_cpp_backend"], "cuda")
    self.assertEqual(config["flags"]["aiperf_gpu_driver_version"], "580")
    self.assertEqual(config["flags"]["aiperf_gpu_cuda_version"], "12.6")

  @flagsaver.flagsaver(aiperf_model_size="E2B")
  def testCheckPrerequisitesRejectsUnsupportedModel(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark.CheckPrerequisites(_BenchmarkConfig())

  @flagsaver.flagsaver(aiperf_model_size="12B")
  def testCheckPrerequisitesRejects12B(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark.CheckPrerequisites(_BenchmarkConfig())

  @flagsaver.flagsaver(aiperf_model_size="26B-A4B")
  def testCheckPrerequisitesAccepts26B(self):
    aiperf_gpu_benchmark.CheckPrerequisites(_BenchmarkConfig())

  @flagsaver.flagsaver(aiperf_model_size="31B")
  def testCheckPrerequisitesAccepts31B(self):
    aiperf_gpu_benchmark.CheckPrerequisites(_BenchmarkConfig())

  @flagsaver.flagsaver(
      aiperf_model_size="31B", aiperf_model_quantization="Q8_0"
  )
  def testCheckPrerequisitesAccepts31BQ8OnDefaultVm(self):
    aiperf_gpu_benchmark.CheckPrerequisites(_BenchmarkConfig())

  @flagsaver.flagsaver(
      aiperf_model_size="31B", aiperf_model_quantization="Q8_0"
  )
  def testCheckPrerequisitesRejects31BQ8OnTwoL4(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark.CheckPrerequisites(
          _BenchmarkConfig("g2-standard-24")
      )

  @flagsaver.flagsaver(
      aiperf_model_size="31B",
      aiperf_model_quantization="Q8_0",
      aiperf_gpu_split_mode="none",
  )
  def testCheckPrerequisitesRejectsSingleGpuSplitMode(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark.CheckPrerequisites(_BenchmarkConfig())

  def testRejectsNonG2MachineType(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark._GpuCountFromMachineType("a2-highgpu-1g")

  def testG2GpuCounts(self):
    self.assertEqual(
        aiperf_gpu_benchmark._GpuCountFromMachineType("g2-standard-8"), 1
    )
    self.assertEqual(
        aiperf_gpu_benchmark._GpuCountFromMachineType("g2-standard-24"), 2
    )
    self.assertEqual(
        aiperf_gpu_benchmark._GpuCountFromMachineType("g2-standard-48"), 4
    )

  @flagsaver.flagsaver(aiperf_gpu_split_mode="layer")
  def testEffectiveGpuMemoryUsesAllGpus(self):
    self.assertEqual(aiperf_gpu_benchmark._EffectiveGpuMemoryGb(4), 96.0)

  @flagsaver.flagsaver(aiperf_gpu_split_mode="none")
  def testEffectiveGpuMemoryUsesOneGpuForNoSplit(self):
    self.assertEqual(aiperf_gpu_benchmark._EffectiveGpuMemoryGb(4), 24.0)

  @flagsaver.flagsaver(aiperf_gpu_memory_gb=80)
  def testEffectiveGpuMemoryOverride(self):
    self.assertEqual(aiperf_gpu_benchmark._EffectiveGpuMemoryGb(4), 80.0)

  @flagsaver.flagsaver(aiperf_gpu_driver_version="590.65.06")
  def testDriverPackageUsesMajorVersion(self):
    self.assertEqual(
        aiperf_gpu_benchmark._DriverPackage(),
        "nvidia-headless-590-server",
    )
    self.assertEqual(
        aiperf_gpu_benchmark._DriverUtilsPackage(),
        "nvidia-utils-590-server",
    )
    self.assertEqual(
        aiperf_gpu_benchmark._DriverMajorVersion("590.65.06"), "590"
    )

  @flagsaver.flagsaver(aiperf_gpu_driver_version="580")
  def testDriverPackageDefaultsTo580(self):
    self.assertEqual(
        aiperf_gpu_benchmark._DriverPackage(),
        "nvidia-headless-580-server",
    )
    self.assertEqual(
        aiperf_gpu_benchmark._DriverUtilsPackage(),
        "nvidia-utils-580-server",
    )

  @flagsaver.flagsaver(aiperf_gpu_driver_version="not-a-version")
  def testCheckPrerequisitesRejectsMalformedDriverVersion(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark._ValidateGpuFlags(4)

  def testCudaInstallerFor126(self):
    installer = aiperf_gpu_benchmark._CudaInstaller()
    self.assertIn("cuda-repo-ubuntu2404-12-6", installer["url"])
    self.assertEqual(
        installer["repo_list"],
        "/etc/apt/sources.list.d/cuda-ubuntu2404-12-6-local.list",
    )
    self.assertEqual(installer["toolkit_package"], "cuda-toolkit-12-6")
    self.assertEqual(installer["home"], "/usr/local/cuda-12.6")

  def testAptCommandsForceIpv4AndRetries(self):
    update_command = aiperf_gpu_benchmark._AptUpdateCommand()
    install_command = aiperf_gpu_benchmark._AptInstallCommand("pkg")

    for command in (update_command, install_command):
      self.assertIn("Acquire::ForceIPv4=true", command)
      self.assertIn("Acquire::Retries=3", command)
    self.assertIn("update", update_command)
    self.assertIn("install -y pkg", install_command)

  @flagsaver.flagsaver(
      aiperf_gpu_tensor_split="1,1",
      aiperf_gpu_split_mode="layer",
  )
  def testCheckPrerequisitesRejectsTensorSplitCountMismatch(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark._ValidateGpuFlags(4)

  @flagsaver.flagsaver(aiperf_gpu_main=4)
  def testCheckPrerequisitesRejectsMainGpuOutOfRange(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark._ValidateGpuFlags(4)

  @flagsaver.flagsaver(aiperf_gpu_layers=-1)
  def testCheckPrerequisitesRejectsNegativeGpuLayers(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark._ValidateGpuFlags(4)

  @flagsaver.flagsaver(aiperf_gpu_split_mode="invalid")
  def testCheckPrerequisitesRejectsInvalidSplitMode(self):
    with self.assertRaises(errors.Config.InvalidValue):
      aiperf_gpu_benchmark._ValidateGpuFlags(4)

  @flagsaver.flagsaver(
      aiperf_model_size="26B-A4B",
      aiperf_concurrency=["1", "2"],
      aiperf_gpu_split_mode="layer",
      aiperf_gpu_tensor_split="1,1",
      aiperf_gpu_main=0,
      aiperf_flash_attn=True,
  )
  def testStartServerMultiGpu(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.NumCpusForBenchmark.return_value = 48
    server_vm.RemoteCommand.return_value = ("12345\n", "")

    pid = aiperf_gpu_benchmark._StartServer(
        server_vm, "/opt/pkb/models/model.gguf", 4
    )

    self.assertEqual(pid, 12345)
    args = self._StartServerArgs(server_vm)
    self.assertEqual(args[args.index("-ngl") + 1], "99")
    self.assertEqual(args[args.index("--split-mode") + 1], "layer")
    self.assertEqual(args[args.index("--tensor-split") + 1], "1,1")
    self.assertEqual(args[args.index("--main-gpu") + 1], "0")
    self.assertIn("--flash-attn", args)
    self.assertEqual(args[args.index("--flash-attn") + 1], "on")
    self.assertEqual(args[args.index("--ctx-size") + 1], str(16384 * 2))
    self.assertEqual(args[args.index("--parallel") + 1], "2")

  @flagsaver.flagsaver(
      aiperf_model_size="31B",
      aiperf_concurrency=["1"],
      aiperf_flash_attn=False,
  )
  def testStartServerSingleGpuOmitsMultiGpuFlags(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.NumCpusForBenchmark.return_value = 48
    server_vm.RemoteCommand.return_value = ("12345\n", "")

    pid = aiperf_gpu_benchmark._StartServer(
        server_vm, "/opt/pkb/models/model.gguf", 1
    )

    self.assertEqual(pid, 12345)
    args = self._StartServerArgs(server_vm)
    self.assertEqual(args[args.index("-ngl") + 1], "99")
    self.assertNotIn("--split-mode", args)
    self.assertNotIn("--tensor-split", args)
    self.assertNotIn("--main-gpu", args)
    self.assertNotIn("--flash-attn", args)
    self.assertEqual(args[args.index("--ctx-size") + 1], str(8192))

  def _StartServerArgs(self, server_vm):
    nohup_calls = [
        call.args[0]
        for call in server_vm.RemoteCommand.call_args_list
        if call.args and call.args[0].startswith("nohup ")
    ]
    self.assertTrue(nohup_calls)
    args = shlex.split(nohup_calls[0].split(" > ")[0])
    return args[1:]  # Drop the leading 'nohup'.

  def testVerifyServerUsesGpuAcceptsLogOffload(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)

    def _remote(command, **_):
      if "grep -Ei" in command:
        return ("load_tensors: offloaded 32/33 layers to GPU\n", "")
      return ("", "")

    server_vm.RemoteCommand.side_effect = _remote

    aiperf_gpu_benchmark._VerifyServerUsesGpu(server_vm, 12345)

  def testVerifyServerUsesGpuAcceptsNvidiaSmiProcess(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)

    def _remote(command, **_):
      if "grep -Ei" in command:
        return ("", "")
      if "query-compute-apps" in command:
        return ("12345, /opt/pkb/llama.cpp/build/bin/llama-server, 1024\n", "")
      return ("", "")

    server_vm.RemoteCommand.side_effect = _remote

    aiperf_gpu_benchmark._VerifyServerUsesGpu(server_vm, 12345)

  @mock.patch.object(aiperf_gpu_benchmark.time, "sleep")
  def testVerifyServerUsesGpuFailsWithDiagnostics(self, _):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)

    def _remote(command, **_):
      if "grep -Ei" in command:
        return ("", "")
      if "query-compute-apps" in command:
        return ("", "")
      if "tail -n 200" in command:
        return ("server log tail\n", "")
      if "ps -p 12345" in command:
        return ("12345 llama-server /opt/pkb/llama.cpp\n", "")
      if "pgrep -af llama-server" in command:
        return ("12345 llama-server\n", "")
      if "query-gpu" in command:
        return ("0, NVIDIA L4, 0, 24576\n", "")
      return ("", "")

    server_vm.RemoteCommand.side_effect = _remote

    with self.assertRaises(errors.Benchmarks.PrepareException) as context:
      aiperf_gpu_benchmark._VerifyServerUsesGpu(server_vm, 12345)

    error = str(context.exception)
    self.assertIn("PID 12345 did not use CUDA/GPU", error)
    self.assertIn("GPU compute processes:\n<none>", error)
    self.assertIn("server log tail", error)
    self.assertIn("llama-server /opt/pkb/llama.cpp", error)
    self.assertIn("NVIDIA L4", error)

  def testQueryGpuInfo(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.RemoteCommand.return_value = (
        (
            "NVIDIA L4, 24576, 580.178.04, 8.9\n"
            "NVIDIA L4, 24576, 580.178.04, 8.9\n"
        ),
        "",
    )

    gpus = aiperf_gpu_benchmark._QueryGpuInfo(server_vm)

    self.assertLen(gpus, 2)
    self.assertEqual(gpus[0]["name"], "NVIDIA L4")
    self.assertEqual(gpus[0]["memory_total_mb"], 24576)
    self.assertEqual(gpus[0]["compute_cap"], "8.9")

  def testVerifyGpuRejectsWrongCount(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.RemoteCommand.return_value = (
        "NVIDIA L4, 24576, 580.178.04, 8.9\n",
        "",
    )

    with self.assertRaises(errors.Benchmarks.PrepareException):
      aiperf_gpu_benchmark._VerifyGpu(server_vm, "g2-standard-48")

  def testVerifyGpuRejectsNonL4(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.RemoteCommand.return_value = (
        "NVIDIA A100-SXM4-40GB, 40960, 580.178.04, 8.0\n",
        "",
    )

    with self.assertRaises(errors.Benchmarks.PrepareException):
      aiperf_gpu_benchmark._VerifyGpu(server_vm, "g2-standard-8")

  @flagsaver.flagsaver(aiperf_gpu_driver_version="580")
  def testInstallNvidiaDriverSkipsMatchingVersion(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.TryRemoteCommand.return_value = True
    server_vm.RemoteCommand.return_value = ("580.178.04\n", "")

    aiperf_gpu_benchmark._InstallNvidiaDriver(server_vm)

    commands = [call.args[0] for call in server_vm.RemoteCommand.call_args_list]
    self.assertFalse(
        any(
            all(
                part in command
                for part in ("apt-get", " install -y", "nvidia-headless")
            )
            for command in commands
        )
    )
    self.assertIn("sudo nvidia-smi -pm 1", commands)

  @mock.patch.object(aiperf_gpu_benchmark.time, "sleep")
  def testInstallNvidiaDriverFailsAfterRetry(self, _):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.TryRemoteCommand.return_value = False
    server_vm.RemoteCommand.return_value = ("", "")

    with self.assertRaises(errors.Benchmarks.PrepareException) as context:
      aiperf_gpu_benchmark._InstallNvidiaDriver(server_vm)

    commands = [call.args[0] for call in server_vm.RemoteCommand.call_args_list]
    self.assertTrue(
        any(
            all(
                part in command
                for part in (
                    "apt-get",
                    " install -y",
                    "nvidia-headless-580-server",
                    "nvidia-utils-580-server",
                )
            )
            for command in commands
        )
    )
    self.assertTrue(any("modprobe nvidia" in command for command in commands))
    error = str(context.exception)
    self.assertIn("dkms status", error)
    self.assertIn("nvidia-utils-580-server", error)
    self.assertIn("nvidia-smi is not available", error)
    self.assertIn("command -v nvidia-smi", error)
    self.assertIn("ls -l /dev/nvidia*", error)
    self.assertIn("lsmod | grep nvidia", error)
    self.assertIn("command -v nvidia-modprobe", error)

  def testInstallNvidiaDriverRaisesDiagnosticsOnAptFailure(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.TryRemoteCommand.return_value = False

    def _remote(command, **_):
      if all(
          part in command
          for part in ("apt-get", " install -y", "nvidia-headless")
      ):
        raise errors.VirtualMachine.RemoteCommandError("apt failed")
      return ("diagnostic output", "")

    server_vm.RemoteCommand.side_effect = _remote

    with self.assertRaises(errors.Benchmarks.PrepareException) as context:
      aiperf_gpu_benchmark._InstallNvidiaDriver(server_vm)

    error = str(context.exception)
    self.assertIn("nvidia-headless-580-server", error)
    self.assertIn("nvidia-utils-580-server", error)
    self.assertIn("dkms status", error)
    self.assertIn("diagnostic output", error)

  @mock.patch.object(aiperf_gpu_benchmark.time, "sleep")
  def testInstallNvidiaDriverSucceedsAfterInstall(self, _):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.TryRemoteCommand.side_effect = [False, True]

    def _remote(command, **_):
      if "command -v nvidia-smi" in command:
        return ("/usr/bin/nvidia-smi\n", "")
      return ("", "")

    server_vm.RemoteCommand.side_effect = _remote

    aiperf_gpu_benchmark._InstallNvidiaDriver(server_vm)

    commands = [call.args[0] for call in server_vm.RemoteCommand.call_args_list]
    install_commands = [
        command for command in commands if " install -y" in command
    ]
    self.assertEqual(len(install_commands), 1)
    self.assertIn("nvidia-headless-580-server", install_commands[0])
    self.assertIn("nvidia-utils-580-server", install_commands[0])
    self.assertIn("sudo nvidia-smi -pm 1", commands)

  def testInstallCudaSkipsMatchingVersion(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.RemoteCommand.return_value = (
        "Cuda compilation tools, release 12.6, V12.6.0\n",
        "",
    )

    aiperf_gpu_benchmark._InstallCudaToolkit(server_vm)

    commands = [call.args[0] for call in server_vm.RemoteCommand.call_args_list]
    self.assertFalse(any("wget" in command for command in commands))
    self.assertTrue(any("/usr/local/cuda-12.6" in c for c in commands))

  def testInstallCudaScopesUpdateToCudaRepo(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    nvcc_calls = 0

    def _remote(command, **_):
      nonlocal nvcc_calls
      if "nvcc --version" in command:
        nvcc_calls += 1
        if nvcc_calls == 1:
          return ("", "")
        return ("Cuda compilation tools, release 12.6, V12.6.0\n", "")
      return ("", "")

    server_vm.RemoteCommand.side_effect = _remote

    aiperf_gpu_benchmark._InstallCudaToolkit(server_vm)

    commands = [call.args[0] for call in server_vm.RemoteCommand.call_args_list]
    update_commands = [
        command
        for command in commands
        if "apt-get" in command and " update" in command
    ]
    self.assertEqual(len(update_commands), 1)
    update_command = update_commands[0]
    self.assertIn("Acquire::ForceIPv4=true", update_command)
    self.assertIn("Acquire::Retries=3", update_command)
    self.assertIn(
        "Dir::Etc::sourcelist="
        "/etc/apt/sources.list.d/cuda-ubuntu2404-12-6-local.list",
        update_command,
    )
    self.assertIn("Dir::Etc::sourceparts=-", update_command)
    self.assertIn("APT::Get::List-Cleanup=0", update_command)
    self.assertNotIn("sources.list.d/*", update_command)

  def testInstallCudaWrapsUpdateFailure(self):
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)

    def _remote(command, **_):
      if "nvcc --version" in command:
        return ("", "")
      if "apt-get" in command and " update" in command:
        raise errors.VirtualMachine.RemoteCommandError("update failed")
      return ("", "")

    server_vm.RemoteCommand.side_effect = _remote

    with self.assertRaises(errors.Benchmarks.PrepareException) as context:
      aiperf_gpu_benchmark._InstallCudaToolkit(server_vm)

    error = str(context.exception)
    self.assertIn("Failed to refresh the CUDA apt repository", error)
    self.assertIn("cuda-repo-ubuntu2404-12-6-local", error)
    self.assertIn("cuda-ubuntu2404-12-6-local.list", error)
    self.assertIn("update failed", error)

  def testGetCommonMetadata(self):
    client_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    client_vm.machine_type = "c3-highcpu-4"
    server_vm = mock.create_autospec(virtual_machine.BaseVirtualMachine)
    server_vm.machine_type = "g2-standard-48"
    gpu_info = [
        {
            "name": "NVIDIA L4",
            "memory_total_mb": 24576,
            "driver_version": "580.178.04",
            "compute_cap": "8.9",
        },
        {
            "name": "NVIDIA L4",
            "memory_total_mb": 24576,
            "driver_version": "580.178.04",
            "compute_cap": "8.9",
        },
    ]

    metadata = aiperf_gpu_benchmark.GetCommonMetadata(
        client_vm, server_vm, gpu_info
    )

    self.assertEqual(metadata["gpu_count"], 2)
    self.assertEqual(metadata["gpu_memory_total_mb"], 49152)
    self.assertEqual(metadata["nvidia_driver_version"], "580.178.04")
    self.assertEqual(metadata["cuda_version"], "12.6")
    self.assertEqual(metadata["llama_cpp_backend"], "cuda")
    self.assertEqual(metadata["gpu_layers"], 99)
    self.assertEqual(metadata["gpu_split_mode"], "layer")
    self.assertFalse(metadata["flash_attn"])


if __name__ == "__main__":
  absltest.main()
