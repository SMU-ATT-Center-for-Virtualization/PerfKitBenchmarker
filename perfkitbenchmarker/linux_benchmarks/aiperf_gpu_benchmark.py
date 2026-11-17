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
"""Benchmark for GPU-based LLM inference using llama.cpp CUDA and AIPerf.

The benchmark provisions a client VM running AIPerf and a G2 server VM with one
or more L4 GPUs. It supports Gemma 4 26B-A4B and 31B with Q4_0 or Q8_0
quantization. One model size is selected per run.
"""

# pylint: disable=protected-access

import logging
import posixpath
import re
import shlex
import time
from typing import Any

from absl import flags
from perfkitbenchmarker import benchmark_spec as bm_spec
from perfkitbenchmarker import configs
from perfkitbenchmarker import errors
from perfkitbenchmarker import sample
from perfkitbenchmarker import virtual_machine
from perfkitbenchmarker.linux_benchmarks import (
    aiperf_cpu_benchmark as aiperf_cpu,
)
from perfkitbenchmarker.linux_packages import llama_cpp

BENCHMARK_NAME = "aiperf_gpu"
BENCHMARK_CONFIG = """
aiperf_gpu:
  description: >
    GPU-based LLM inference benchmark using llama.cpp CUDA and AIPerf.
    Supports Gemma 4 26B-A4B and 31B on G2 VMs with one or more L4 GPUs.
  vm_groups:
    client:
      os_type: ubuntu2404
      vm_spec:
        GCP:
          machine_type: c3-highcpu-4
          zone: asia-northeast1-b
          image: null
      vm_count: 1
    server:
      os_type: ubuntu2404
      vm_spec:
        GCP:
          machine_type: g2-standard-48
          zone: asia-northeast1-b
          image: null
          boot_disk_size: 128
      vm_count: 1
  flags:
    llama_cpp_backend: cuda
    aiperf_gpu_driver_version: '580'
    aiperf_gpu_cuda_version: '12.6'
"""

FLAGS = flags.FLAGS

_GPU_LAYERS = flags.DEFINE_integer(
    "aiperf_gpu_layers",
    99,
    "Number of llama-server layers to offload to the GPU.",
)
_GPU_SPLIT_MODE = flags.DEFINE_enum(
    "aiperf_gpu_split_mode",
    "layer",
    ["none", "layer", "row"],
    "llama-server multi-GPU split mode.",
)
_GPU_TENSOR_SPLIT = flags.DEFINE_string(
    "aiperf_gpu_tensor_split",
    None,
    "Optional comma-separated llama-server tensor split, one value per GPU.",
)
_GPU_MAIN = flags.DEFINE_integer(
    "aiperf_gpu_main",
    None,
    "Optional main GPU index passed to llama-server.",
)
_FLASH_ATTN = flags.DEFINE_bool(
    "aiperf_flash_attn",
    False,
    "Whether to enable llama.cpp flash attention on the GPU server.",
)
_GPU_MEMORY_GB = flags.DEFINE_integer(
    "aiperf_gpu_memory_gb",
    None,
    "Optional override for total GPU memory in GiB used by the memory guard.",
)
_DRIVER_VERSION = flags.DEFINE_string(
    "aiperf_gpu_driver_version",
    "580",
    "NVIDIA driver major version; installs matching headless/utils packages.",
)
_CUDA_VERSION = flags.DEFINE_enum(
    "aiperf_gpu_cuda_version",
    "12.6",
    ["12.6"],
    "CUDA toolkit version to install on the GPU server.",
)

_SUPPORTED_MODELS = ("26B-A4B", "31B")
_L4_GPU_MEMORY_GB = 24
_G2_GPU_COUNTS = {
    "g2-standard-4": 1,
    "g2-standard-8": 1,
    "g2-standard-12": 1,
    "g2-standard-16": 1,
    "g2-standard-24": 2,
    "g2-standard-32": 1,
    "g2-standard-48": 4,
    "g2-standard-96": 8,
}
_CUDA_INSTALLERS = {
    "12.6": {
        "url": (
            "https://developer.download.nvidia.com/compute/cuda/12.6.0/"
            "local_installers/"
            "cuda-repo-ubuntu2404-12-6-local_12.6.0-560.28.03-1_amd64.deb"
        ),
        "repo_dir": "/var/cuda-repo-ubuntu2404-12-6-local",
        "repo_list": "/etc/apt/sources.list.d/cuda-ubuntu2404-12-6-local.list",
        "toolkit_package": "cuda-toolkit-12-6",
        "home": "/usr/local/cuda-12.6",
    },
}
_DRIVER_PACKAGE_TEMPLATE = "nvidia-headless-{version}-server"
_DRIVER_UTILS_PACKAGE_TEMPLATE = "nvidia-utils-{version}-server"
_APT_OPTIONS = "-o Acquire::ForceIPv4=true -o Acquire::Retries=3"
_GPU_OFFLOAD_LOG_PATTERN = (
    r"offloaded [0-9]+/[0-9]+ layers to GPU"
    r"|offloading [0-9]+ repeating layers to GPU"
    r"|loaded CUDA backend"
    r"|ggml_cuda_init: found"
    r"|using device CUDA"
)
_GPU_VERIFY_TIMEOUT_SECONDS = 30
_GPU_VERIFY_POLL_SECONDS = 2
_INSTALL_TIMEOUT = 60 * 60
_APT_TIMEOUT = 60 * 30


def GetConfig(user_config: dict[Any, Any]) -> dict[Any, Any]:
  """Loads and returns the benchmark configuration."""
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def CheckPrerequisites(benchmark_config) -> None:
  """Validates the benchmark flags and GPU configuration before provisioning."""
  _ValidateModelSize()
  aiperf_cpu.CheckPrerequisites(benchmark_config)

  machine_type = _ServerMachineType(benchmark_config)
  gpu_count = _GpuCountFromMachineType(machine_type)
  _ValidateGpuFlags(gpu_count)

  profile = aiperf_cpu.GetModelProfile()
  ctx_per_slot = aiperf_cpu._EffectiveServerCtxPerSlot(profile)
  parallel = aiperf_cpu._ServerParallel()
  estimated_gb = aiperf_cpu._EstimateServerMemoryGb(
      profile, ctx_per_slot, parallel
  )
  gpu_memory_gb = _EffectiveGpuMemoryGb(gpu_count)
  memory_limit_gb = 0.9 * gpu_memory_gb
  if estimated_gb > memory_limit_gb:
    raise errors.Config.InvalidValue(
        f"Estimated GPU memory ({estimated_gb:.1f} GB) exceeds 90% of"
        f" available GPU memory ({gpu_memory_gb:.0f} GB) for Gemma 4"
        f" {profile.size} {aiperf_cpu._EffectiveQuantization(profile)} with"
        f" {ctx_per_slot} context tokens per slot, {parallel} parallel slots,"
        f" and split mode {_GPU_SPLIT_MODE.value}. Reduce"
        " aiperf_server_ctx_size, lower the concurrency sweep, use Q4_0, or"
        " select a larger G2 machine type."
    )


def _ValidateModelSize() -> None:
  """Rejects model sizes that are not yet supported by the GPU benchmark."""
  if aiperf_cpu._MODEL_SIZE.value not in _SUPPORTED_MODELS:
    raise errors.Config.InvalidValue(
        f"aiperf_gpu currently supports only {', '.join(_SUPPORTED_MODELS)}."
        f" Got --aiperf_model_size={aiperf_cpu._MODEL_SIZE.value}."
    )


def _ServerMachineType(benchmark_config) -> str:
  """Returns the configured server machine type."""
  try:
    machine_type = benchmark_config.vm_groups["server"].vm_spec.machine_type
  except (AttributeError, KeyError, TypeError) as e:
    raise errors.Config.InvalidValue(
        "aiperf_gpu requires a server VM group with a machine type."
    ) from e
  if not machine_type:
    raise errors.Config.InvalidValue(
        "aiperf_gpu requires an explicit server machine type."
    )
  return machine_type


def _GpuCountFromMachineType(machine_type: str) -> int:
  """Returns the L4 GPU count for a G2 machine type."""
  if machine_type not in _G2_GPU_COUNTS:
    raise errors.Config.InvalidValue(
        "aiperf_gpu currently supports only the G2 machine family."
        f" Got server machine type {machine_type!r}."
    )
  return _G2_GPU_COUNTS[machine_type]


def _EffectiveGpuMemoryGb(gpu_count: int) -> float:
  """Returns the GPU memory available to llama-server."""
  if _GPU_MEMORY_GB.value is not None:
    return float(_GPU_MEMORY_GB.value)
  if _GPU_SPLIT_MODE.value == "none":
    return float(_L4_GPU_MEMORY_GB)
  return float(gpu_count * _L4_GPU_MEMORY_GB)


def _ValidateGpuFlags(gpu_count: int) -> None:
  """Validates GPU-specific flags."""
  if _GPU_SPLIT_MODE.value not in ("none", "layer", "row"):
    raise errors.Config.InvalidValue(
        f"Unsupported aiperf_gpu_split_mode: {_GPU_SPLIT_MODE.value!r}."
    )
  if not re.fullmatch(r"\d+(\.\d+){0,2}", _DRIVER_VERSION.value):
    raise errors.Config.InvalidValue(
        "aiperf_gpu_driver_version must be a major version or dotted release,"
        " for example 580 or 580.178.04."
    )
  if _CUDA_VERSION.value not in _CUDA_INSTALLERS:
    raise errors.Config.InvalidValue(
        f"Unsupported aiperf_gpu_cuda_version: {_CUDA_VERSION.value!r}."
    )
  if _GPU_MEMORY_GB.value is not None and _GPU_MEMORY_GB.value <= 0:
    raise errors.Config.InvalidValue("aiperf_gpu_memory_gb must be positive.")
  if _GPU_LAYERS.value < 0:
    raise errors.Config.InvalidValue("aiperf_gpu_layers must be non-negative.")
  if _GPU_MAIN.value is not None:
    if _GPU_MAIN.value < 0:
      raise errors.Config.InvalidValue("aiperf_gpu_main must be non-negative.")
    if _GPU_MAIN.value >= gpu_count:
      raise errors.Config.InvalidValue(
          f"aiperf_gpu_main ({_GPU_MAIN.value}) must be less than the GPU"
          f" count ({gpu_count})."
      )
  if _GPU_TENSOR_SPLIT.value:
    parts = [part.strip() for part in _GPU_TENSOR_SPLIT.value.split(",")]
    if len(parts) != gpu_count:
      raise errors.Config.InvalidValue(
          "aiperf_gpu_tensor_split must contain one value per GPU."
          f" Got {len(parts)} values for {gpu_count} GPUs."
      )
    for part in parts:
      try:
        value = float(part)
      except ValueError as e:
        raise errors.Config.InvalidValue(
            f"Invalid aiperf_gpu_tensor_split value: {part!r}."
        ) from e
      if value < 0:
        raise errors.Config.InvalidValue(
            "aiperf_gpu_tensor_split values must be non-negative."
        )


def _DriverMajorVersion(version: str | None = None) -> str:
  """Returns the NVIDIA driver major version."""
  return (version or _DRIVER_VERSION.value).split(".", 1)[0]


def _DriverPackage() -> str:
  """Returns the Ubuntu NVIDIA headless driver package name."""
  return _DRIVER_PACKAGE_TEMPLATE.format(version=_DriverMajorVersion())


def _DriverUtilsPackage() -> str:
  """Returns the Ubuntu NVIDIA server utilities package name."""
  return _DRIVER_UTILS_PACKAGE_TEMPLATE.format(version=_DriverMajorVersion())


def _CudaInstaller() -> dict[str, str]:
  """Returns the CUDA installer metadata for the selected version."""
  return _CUDA_INSTALLERS[_CUDA_VERSION.value]


def Prepare(benchmark_spec: bm_spec.BenchmarkSpec) -> None:
  """Installs GPU packages, builds llama.cpp CUDA, and starts the server."""
  client_vm, server_vm = aiperf_cpu._GetVms(benchmark_spec)
  client_vm.Install("curl")
  client_vm.Install("aiperf")

  _InstallPrerequisites(server_vm)
  _InstallNvidiaDriver(server_vm)
  _InstallCudaToolkit(server_vm)

  machine_type = _ServerMachineType(benchmark_spec.config)
  gpu_info = _VerifyGpu(server_vm, machine_type)
  benchmark_spec.aiperf_gpu_info = gpu_info

  server_vm.Install("llama_cpp")
  model_path = aiperf_cpu._DownloadModel(server_vm)
  benchmark_spec.aiperf_dataset_path = aiperf_cpu.PrepareDataset(client_vm)

  server_vm.AllowPort(
      aiperf_cpu._SERVER_PORT.value,
      source_range=[client_vm.internal_ip + "/32"],
  )
  server_pid = _StartServer(server_vm, model_path, len(gpu_info))
  aiperf_cpu._WaitForServer(
      client_vm,
      server_vm,
      server_vm.internal_ip,
      aiperf_cpu._SERVER_PORT.value,
  )
  _VerifyServerUsesGpu(server_vm, server_pid)


def _AptUpdateCommand() -> str:
  """Returns an apt-get update command that prefers IPv4."""
  return f"sudo apt-get {_APT_OPTIONS} update"


def _AptInstallCommand(package_list: str) -> str:
  """Returns an apt-get install command that prefers IPv4."""
  return (
      f"sudo DEBIAN_FRONTEND=noninteractive apt-get {_APT_OPTIONS}"
      f" install -y {package_list}"
  )


def _InstallPrerequisites(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> None:
  """Installs the packages required to build llama.cpp and install NVIDIA."""
  packages = [
      "build-essential",
      "cmake",
      "git",
      "curl",
      "wget",
      "dkms",
      "pciutils",
      "libcurl4-openssl-dev",
      "libssl-dev",
      "pkg-config",
  ]
  package_list = " ".join([
      *packages,
      '"linux-headers-$(uname -r)"',
  ])
  server_vm.RemoteCommand(_AptUpdateCommand(), timeout=_APT_TIMEOUT)
  server_vm.RemoteCommand(
      _AptInstallCommand(package_list), timeout=_APT_TIMEOUT
  )


def _GetInstalledDriverVersion(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> str:
  """Returns the installed NVIDIA driver version, or an empty string."""
  stdout, _ = server_vm.RemoteCommand(
      "sudo nvidia-smi --query-gpu=driver_version --format=csv,noheader",
      ignore_failure=True,
  )
  lines = [line.strip() for line in stdout.splitlines() if line.strip()]
  return lines[0] if lines else ""


def _InstallNvidiaDriver(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> None:
  """Installs the configured NVIDIA headless driver and utilities."""
  requested_major = _DriverMajorVersion()
  if server_vm.TryRemoteCommand("sudo nvidia-smi >/dev/null 2>&1"):
    installed_version = _GetInstalledDriverVersion(server_vm)
    if _DriverMajorVersion(installed_version) == requested_major:
      logging.info("NVIDIA driver %s is already installed.", installed_version)
      server_vm.RemoteCommand("sudo nvidia-smi -pm 1", ignore_failure=True)
      return

  driver_packages = (_DriverPackage(), _DriverUtilsPackage())
  package_list = " ".join(shlex.quote(package) for package in driver_packages)
  install_command = _AptInstallCommand(package_list)
  try:
    server_vm.RemoteCommand(_AptUpdateCommand(), timeout=_APT_TIMEOUT)
    server_vm.RemoteCommand(install_command, timeout=_INSTALL_TIMEOUT)
  except errors.VirtualMachine.RemoteCommandError as e:
    diagnostics = _DriverInstallDiagnostics(server_vm, driver_packages)
    raise errors.Benchmarks.PrepareException(
        f"Failed to install {package_list} on the GPU server."
        f" Driver installation diagnostics:\n{diagnostics}"
    ) from e

  if not _WaitForNvidiaSmi(server_vm):
    diagnostics = _DriverInstallDiagnostics(server_vm, driver_packages)
    if not _NvidiaSmiPath(server_vm):
      raise errors.Benchmarks.PrepareException(
          f"{_DriverUtilsPackage()} was installed but nvidia-smi is not"
          " available. The package must provide /usr/bin/nvidia-smi."
          f" Driver installation diagnostics:\n{diagnostics}"
      )
    raise errors.Benchmarks.PrepareException(
        f"{package_list} was installed but nvidia-smi is not usable."
        f" Driver installation diagnostics:\n{diagnostics}"
    )
  server_vm.RemoteCommand("sudo nvidia-smi -pm 1", ignore_failure=True)


def _DriverInstallDiagnostics(
    server_vm: virtual_machine.BaseVirtualMachine,
    driver_packages: tuple[str, ...],
) -> str:
  """Collects package, DKMS, and kernel diagnostics for a driver failure."""
  package_list = " ".join(shlex.quote(package) for package in driver_packages)
  commands = [
      ("kernel", "uname -r"),
      (
          "installed packages",
          "dpkg -l | grep -E 'nvidia|linux-headers'",
      ),
      ("dkms status", "dkms status"),
      (
          "dkms make logs",
          (
              "sudo find /var/lib/dkms -name make.log -o -name build.log"
              " | tail -n 5 | xargs -r -I{} sh -c"
              " 'echo === {}; sudo tail -n 100 {}'"
          ),
      ),
      ("kernel log", "sudo journalctl -k | tail -n 100"),
      ("nvidia-smi path", "command -v nvidia-smi || true"),
      ("nvidia-smi output", "sudo nvidia-smi"),
      ("nvidia device nodes", "ls -l /dev/nvidia* 2>&1 || true"),
      ("nvidia modules", "lsmod | grep nvidia || true"),
      ("nvidia-modprobe", "command -v nvidia-modprobe || true"),
      ("apt policy", f"apt-cache policy {package_list}"),
  ]
  sections = []
  for label, command in commands:
    stdout, stderr = server_vm.RemoteCommand(command, ignore_failure=True)
    sections.append(
        f"--- {label} ---\n"
        f"command: {command}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )
  return "\n\n".join(sections)


def _NvidiaSmiPath(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> str:
  """Returns the nvidia-smi path, or an empty string if it is missing."""
  stdout, _ = server_vm.RemoteCommand(
      "command -v nvidia-smi || true", ignore_failure=True
  )
  return stdout.strip()


def _WaitForNvidiaSmi(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> bool:
  """Best-effort module load and retry before failing driver setup."""
  for attempt in range(2):
    server_vm.RemoteCommand("sudo modprobe nvidia || true", ignore_failure=True)
    server_vm.RemoteCommand(
        "sudo modprobe nvidia_uvm || true", ignore_failure=True
    )
    if _NvidiaSmiPath(server_vm) and server_vm.TryRemoteCommand(
        "sudo nvidia-smi >/dev/null 2>&1"
    ):
      return True
    if attempt == 0:
      time.sleep(10)
  return False


def _GetInstalledCudaVersion(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> str:
  """Returns the installed CUDA toolkit version, or an empty string."""
  installer = _CudaInstaller()
  stdout, _ = server_vm.RemoteCommand(
      f"{installer['home']}/bin/nvcc --version",
      ignore_failure=True,
  )
  match = re.search(r"release (\d+\.\d+)", stdout)
  return match.group(1) if match else ""


def _InstallCudaToolkit(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> None:
  """Installs the configured CUDA toolkit from the NVIDIA apt repository."""
  installer = _CudaInstaller()
  if _GetInstalledCudaVersion(server_vm) == _CUDA_VERSION.value:
    logging.info("CUDA %s is already installed.", _CUDA_VERSION.value)
    server_vm.RemoteCommand(
        f"sudo ln -sfn {installer['home']} /usr/local/cuda",
        ignore_failure=True,
    )
    return

  deb_path = "/tmp/cuda-repo.deb"
  server_vm.RemoteCommand(
      f"wget --tries=10 {shlex.quote(installer['url'])}"
      f" -O {shlex.quote(deb_path)}",
      timeout=_INSTALL_TIMEOUT,
  )
  server_vm.RemoteCommand(
      f"sudo dpkg -i {shlex.quote(deb_path)}",
      timeout=_INSTALL_TIMEOUT,
  )
  server_vm.RemoteCommand(
      f"sudo cp {installer['repo_dir']}/cuda-*-keyring.gpg"
      " /usr/share/keyrings/",
      timeout=_INSTALL_TIMEOUT,
  )
  cuda_update_command = (
      f"sudo apt-get {_APT_OPTIONS}"
      f" -o Dir::Etc::sourcelist={shlex.quote(installer['repo_list'])}"
      " -o Dir::Etc::sourceparts=-"
      " -o APT::Get::List-Cleanup=0"
      " update"
  )
  try:
    server_vm.RemoteCommand(cuda_update_command, timeout=_APT_TIMEOUT)
  except errors.VirtualMachine.RemoteCommandError as e:
    raise errors.Benchmarks.PrepareException(
        "Failed to refresh the CUDA apt repository after installing"
        f" {installer['repo_dir']}. Check IPv4 connectivity and the CUDA"
        f" source list {installer['repo_list']}. Error: {e}"
    ) from e
  server_vm.RemoteCommand(
      _AptInstallCommand(shlex.quote(installer["toolkit_package"])),
      timeout=_INSTALL_TIMEOUT,
  )
  server_vm.RemoteCommand(
      f"sudo ln -sfn {installer['home']} /usr/local/cuda",
      ignore_failure=True,
  )
  if _GetInstalledCudaVersion(server_vm) != _CUDA_VERSION.value:
    raise errors.Benchmarks.PrepareException(
        f"CUDA {_CUDA_VERSION.value} was not installed successfully."
    )


def _QueryGpuInfo(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> list[dict[str, Any]]:
  """Returns GPU information from nvidia-smi."""
  query_command = " ".join([
      "sudo",
      "nvidia-smi",
      "--query-gpu=name,memory.total,driver_version,compute_cap",
      "--format=csv,noheader,nounits",
  ])
  stdout, _ = server_vm.RemoteCommand(query_command, timeout=120)
  gpus = []
  for line in stdout.splitlines():
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 4:
      continue
    gpus.append({
        "name": parts[0],
        "memory_total_mb": int(float(parts[1])),
        "driver_version": parts[2],
        "compute_cap": parts[3],
    })
  if not gpus:
    raise errors.Benchmarks.PrepareException(
        "nvidia-smi did not report any NVIDIA GPUs."
    )
  return gpus


def _VerifyGpu(
    server_vm: virtual_machine.BaseVirtualMachine,
    machine_type: str,
) -> list[dict[str, Any]]:
  """Verifies the expected L4 GPU layout is present."""
  expected_count = _GpuCountFromMachineType(machine_type)
  gpus = _QueryGpuInfo(server_vm)
  if len(gpus) != expected_count:
    raise errors.Benchmarks.PrepareException(
        f"Expected {expected_count} GPUs for {machine_type}, but nvidia-smi"
        f" reported {len(gpus)}."
    )
  for gpu in gpus:
    if "L4" not in gpu["name"]:
      raise errors.Benchmarks.PrepareException(
          f"aiperf_gpu requires L4 GPUs. Found {gpu['name']!r}."
      )
    if not gpu["compute_cap"].startswith("8.9"):
      raise errors.Benchmarks.PrepareException(
          f"Expected L4 compute capability 8.9, found {gpu['compute_cap']}."
      )
    if _DriverMajorVersion(gpu["driver_version"]) != _DriverMajorVersion():
      logging.warning(
          "Installed NVIDIA driver %s differs from requested major version %s.",
          gpu["driver_version"],
          _DriverMajorVersion(),
      )
  return gpus


def _StartServer(
    server_vm: virtual_machine.BaseVirtualMachine,
    model_path: str,
    gpu_count: int,
) -> int:
  """Starts llama-server with CUDA offload and optional multi-GPU flags."""
  profile = aiperf_cpu.GetModelProfile()
  parallel = aiperf_cpu._ServerParallel()
  ctx_per_slot = aiperf_cpu._EffectiveServerCtxPerSlot(profile)
  args = [
      llama_cpp.LLAMA_SERVER_BIN,
      "-m",
      model_path,
      "--alias",
      aiperf_cpu._EffectiveModelAlias(profile),
      "--host",
      "0.0.0.0",
      "--port",
      str(aiperf_cpu._SERVER_PORT.value),
      "--threads",
      str(aiperf_cpu._ServerThreads(server_vm)),
      "--ctx-size",
      str(ctx_per_slot * parallel),
      "--parallel",
      str(parallel),
      "-ngl",
      str(_GPU_LAYERS.value),
      "--no-webui",
      "--jinja",
  ]
  if gpu_count > 1:
    args += ["--split-mode", _GPU_SPLIT_MODE.value]
    if _GPU_TENSOR_SPLIT.value:
      args += ["--tensor-split", _GPU_TENSOR_SPLIT.value]
    if _GPU_MAIN.value is not None:
      args += ["--main-gpu", str(_GPU_MAIN.value)]
  if _FLASH_ATTN.value:
    args += ["--flash-attn", "on"]

  server_vm.RemoteCommand(
      "pkill -x llama-server || true",
      ignore_failure=True,
  )
  server_vm.RemoteCommand("rm -f /tmp/llama-server.log", ignore_failure=True)
  stdout, _ = server_vm.RemoteCommand(
      f"nohup {shlex.join(args)} > /tmp/llama-server.log 2>&1 & echo $!"
  )
  try:
    return int(stdout.strip().splitlines()[-1])
  except (IndexError, ValueError) as e:
    raise errors.Benchmarks.PrepareException(
        f"Could not determine the llama-server PID from output: {stdout!r}"
    ) from e


def _GpuOffloadLogLines(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> str:
  """Returns matching positive GPU-offload lines from the server log."""
  command = (
      f"grep -Ei {shlex.quote(_GPU_OFFLOAD_LOG_PATTERN)}"
      " /tmp/llama-server.log | head -n 20"
  )
  stdout, _ = server_vm.RemoteCommand(command, ignore_failure=True)
  return stdout.strip()


def _GpuComputeProcesses(
    server_vm: virtual_machine.BaseVirtualMachine,
) -> list[str]:
  """Returns nvidia-smi compute processes."""
  command = " ".join([
      "sudo nvidia-smi",
      "--query-compute-apps=pid,process_name,used_memory",
      "--format=csv,noheader,nounits",
  ])
  stdout, _ = server_vm.RemoteCommand(command, ignore_failure=True)
  return [line.strip() for line in stdout.splitlines() if line.strip()]


def _PidUsesGpu(gpu_processes: list[str], server_pid: int) -> bool:
  """Returns whether the given PID appears in the GPU compute process list."""
  pid = str(server_pid)
  for process in gpu_processes:
    fields = [field.strip() for field in process.split(",")]
    if fields and fields[0] == pid:
      return True
  return False


def _VerifyServerUsesGpu(
    server_vm: virtual_machine.BaseVirtualMachine,
    server_pid: int,
) -> None:
  """Fails if llama-server did not use CUDA/GPU."""
  attempts = _GPU_VERIFY_TIMEOUT_SECONDS // _GPU_VERIFY_POLL_SECONDS
  for attempt in range(attempts):
    offload_lines = _GpuOffloadLogLines(server_vm)
    if offload_lines:
      logging.info("llama-server log confirms GPU offload:\n%s", offload_lines)
      return
    gpu_processes = _GpuComputeProcesses(server_vm)
    if _PidUsesGpu(gpu_processes, server_pid):
      logging.info(
          "nvidia-smi confirms llama-server PID %d is using a GPU.",
          server_pid,
      )
      return
    if attempt + 1 < attempts:
      time.sleep(_GPU_VERIFY_POLL_SECONDS)

  log_tail, _ = server_vm.RemoteCommand(
      "tail -n 200 /tmp/llama-server.log", ignore_failure=True
  )
  gpu_processes = _GpuComputeProcesses(server_vm)
  process, _ = server_vm.RemoteCommand(
      f"ps -p {server_pid} -o pid,comm,args", ignore_failure=True
  )
  pgrep, _ = server_vm.RemoteCommand(
      "pgrep -af llama-server", ignore_failure=True
  )
  gpu_summary_command = " ".join([
      "sudo nvidia-smi",
      "--query-gpu=index,name,memory.used,memory.total",
      "--format=csv",
  ])
  gpu_summary, _ = server_vm.RemoteCommand(
      gpu_summary_command, ignore_failure=True
  )
  process_list = "\n".join(gpu_processes) or "<none>"
  raise errors.Benchmarks.PrepareException(
      f"llama-server PID {server_pid} did not use CUDA/GPU."
      f"\nGPU compute processes:\n{process_list}"
      f"\n\nProcess details:\n{process}"
      f"\n\nllama-server processes:\n{pgrep}"
      f"\n\nGPU summary:\n{gpu_summary}"
      f"\n\nllama-server log tail:\n{log_tail}"
  )


def _GetGpuInfo(
    benchmark_spec: bm_spec.BenchmarkSpec,
    server_vm: virtual_machine.BaseVirtualMachine,
) -> list[dict[str, Any]]:
  """Returns GPU information collected during prepare, or queries it again."""
  gpu_info = getattr(benchmark_spec, "aiperf_gpu_info", None)
  if gpu_info is None:
    gpu_info = _QueryGpuInfo(server_vm)
  return gpu_info


def GetCommonMetadata(
    client_vm: virtual_machine.BaseVirtualMachine,
    server_vm: virtual_machine.BaseVirtualMachine,
    gpu_info: list[dict[str, Any]],
) -> dict[str, Any]:
  """Returns metadata shared by every GPU benchmark sample."""
  metadata = aiperf_cpu.GetCommonMetadata(client_vm, server_vm)
  metadata.update({
      "gpu_count": len(gpu_info),
      "gpu_names": ",".join(gpu["name"] for gpu in gpu_info),
      "gpu_memory_total_mb": sum(gpu["memory_total_mb"] for gpu in gpu_info),
      "nvidia_driver_version": gpu_info[0]["driver_version"],
      "cuda_version": _CUDA_VERSION.value,
      "llama_cpp_backend": "cuda",
      "gpu_layers": _GPU_LAYERS.value,
      "gpu_split_mode": _GPU_SPLIT_MODE.value if len(gpu_info) > 1 else "none",
      "flash_attn": _FLASH_ATTN.value,
  })
  if _GPU_TENSOR_SPLIT.value:
    metadata["gpu_tensor_split"] = _GPU_TENSOR_SPLIT.value
  if _GPU_MAIN.value is not None:
    metadata["main_gpu"] = _GPU_MAIN.value
  return metadata


def Run(benchmark_spec: bm_spec.BenchmarkSpec) -> list[sample.Sample]:
  """Runs AIPerf for every configured sweep point."""
  client_vm, server_vm = aiperf_cpu._GetVms(benchmark_spec)
  url = f"http://{server_vm.internal_ip}:{aiperf_cpu._SERVER_PORT.value}"
  points = aiperf_cpu.GetSweepPoints()
  dataset_path = getattr(benchmark_spec, "aiperf_dataset_path", None)
  gpu_info = _GetGpuInfo(benchmark_spec, server_vm)

  metadata = GetCommonMetadata(client_vm, server_vm, gpu_info)
  profile = aiperf_cpu.GetModelProfile()
  parallel = aiperf_cpu._ServerParallel()
  ctx_per_slot = aiperf_cpu._EffectiveServerCtxPerSlot(profile)
  metadata.update({
      "model_size": profile.size,
      "model_quantization": aiperf_cpu._EffectiveQuantization(profile),
      "model_max_context": profile.max_context,
      "model_repo": aiperf_cpu._EffectiveModelRepo(profile),
      "model_file": aiperf_cpu._EffectiveModelFile(profile),
      "model_alias": aiperf_cpu._EffectiveModelAlias(profile),
      "model_tokenizer": aiperf_cpu._EffectiveTokenizer(profile),
      "llama_cpp_version": llama_cpp.LLAMA_CPP_VERSION.value,
      "server_threads": aiperf_cpu._ServerThreads(server_vm),
      "server_ctx_per_slot": ctx_per_slot,
      "server_ctx_total": ctx_per_slot * parallel,
      "server_parallel": parallel,
      "server_estimated_memory_gb": aiperf_cpu._EstimateServerMemoryGb(
          profile, ctx_per_slot, parallel
      ),
  })
  if aiperf_cpu._DATASET.value == "agentic_code":
    metadata["agentic_max_isl"] = aiperf_cpu._EffectiveAgenticMaxIsl(profile)
    metadata["agentic_max_osl"] = aiperf_cpu._EffectiveAgenticMaxOsl(profile)

  samples = []
  for point_index, point in enumerate(points):
    artifact_dir = posixpath.join(aiperf_cpu._ArtifactRoot(), point.label)
    try:
      samples.extend(
          aiperf_cpu.RunProfilePoint(
              client_vm,
              url,
              point,
              artifact_dir,
              dataset_path,
              metadata,
              point_index,
          )
      )
    except errors.Benchmarks.RunError:
      if not aiperf_cpu._CONTINUE_ON_ERROR.value:
        raise
      logging.exception("AIPerf point %s failed; continuing.", point.label)

  if not samples:
    raise errors.Benchmarks.RunError("No AIPerf samples were collected.")
  return samples


def Cleanup(benchmark_spec: bm_spec.BenchmarkSpec) -> None:
  """Stops the server and removes generated artifacts."""
  aiperf_cpu.Cleanup(benchmark_spec)
