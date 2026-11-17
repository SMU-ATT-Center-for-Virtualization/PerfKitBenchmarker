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
"""Benchmark for CPU-based LLM inference using llama.cpp and AIPerf.

The benchmark provisions a client VM running AIPerf and a server VM running
llama.cpp with a Gemma 4 model. One model size is selected per run via
--aiperf_model_size, and AIPerf is run once per concurrency or request-rate
point so a single PKB run can evaluate a range of load levels.
"""

import dataclasses
import json
import logging
import os
import posixpath
import shlex
from typing import Any

from absl import flags
from perfkitbenchmarker import benchmark_spec as bm_spec
from perfkitbenchmarker import configs
from perfkitbenchmarker import errors
from perfkitbenchmarker import sample
from perfkitbenchmarker import virtual_machine
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_packages import aiperf
from perfkitbenchmarker.linux_packages import llama_cpp

BENCHMARK_NAME = "aiperf_cpu"
BENCHMARK_CONFIG = """
aiperf_cpu:
  description: >
    CPU-based LLM inference benchmark using llama.cpp as the server and
    AIPerf as the load generator. Supports concurrency/request-rate sweeps
    and synthetic or agentic-coding workloads.
  vm_groups:
    client:
      os_type: ubuntu2404
      vm_spec:
        GCP:
          machine_type: c3-highcpu-4
          zone: us-central1-b
          image: null
      vm_count: 1
    server:
      os_type: ubuntu2404
      vm_spec:
        GCP:
          machine_type: c3-standard-16
          zone: us-central1-b
          image: null
          boot_disk_size: 128
      vm_count: 1
"""

FLAGS = flags.FLAGS

_SWEEP = flags.DEFINE_enum(
    "aiperf_sweep",
    "concurrency",
    ["concurrency", "request_rate"],
    "Load dimension to sweep across AIPerf profile runs.",
)
_CONCURRENCY = flags.DEFINE_list(
    "aiperf_concurrency",
    ["1", "2", "4", "8"],
    "Comma-separated concurrency values to sweep.",
)
_REQUEST_RATE = flags.DEFINE_list(
    "aiperf_request_rate",
    [],
    "Comma-separated request-rate values to sweep, in requests per second.",
)
_REQUEST_COUNT = flags.DEFINE_integer(
    "aiperf_request_count",
    100,
    "Number of requests to send per sweep point.",
)
_BENCHMARK_DURATION = flags.DEFINE_integer(
    "aiperf_benchmark_duration",
    None,
    "Optional benchmark duration in seconds per sweep point.",
)
_WARMUP_REQUESTS = flags.DEFINE_integer(
    "aiperf_warmup_requests",
    10,
    "Number of warmup requests per sweep point.",
)
_RANDOM_SEED = flags.DEFINE_integer(
    "aiperf_random_seed",
    42,
    "Random seed passed to AIPerf.",
)
_CONTINUE_ON_ERROR = flags.DEFINE_bool(
    "aiperf_continue_on_error",
    False,
    "Whether to continue sweeping when an AIPerf profile fails.",
)
_TIMEOUT = flags.DEFINE_integer(
    "aiperf_timeout",
    3600,
    "Timeout in seconds for each AIPerf profile run.",
)

_DATASET = flags.DEFINE_enum(
    "aiperf_dataset",
    "synthetic",
    ["synthetic", "agentic_code"],
    "Dataset to use for the benchmark.",
)
_INPUT_TOKENS_MEAN = flags.DEFINE_integer(
    "aiperf_input_tokens_mean",
    None,
    "Mean synthetic input sequence length in tokens.",
)
_OUTPUT_TOKENS_MEAN = flags.DEFINE_integer(
    "aiperf_output_tokens_mean",
    None,
    "Mean synthetic output sequence length in tokens.",
)
_AGENTIC_NUM_SESSIONS = flags.DEFINE_integer(
    "aiperf_agentic_num_sessions",
    100,
    "Number of sessions to synthesize for the agentic_code dataset.",
)
_AGENTIC_SEED = flags.DEFINE_integer(
    "aiperf_agentic_seed",
    42,
    "Random seed for the agentic_code dataset synthesizer.",
)
_AGENTIC_MAX_ISL = flags.DEFINE_integer(
    "aiperf_agentic_max_isl",
    None,
    "Optional maximum input sequence length for agentic_code sessions.",
)
_AGENTIC_MAX_OSL = flags.DEFINE_integer(
    "aiperf_agentic_max_osl",
    None,
    "Optional maximum output sequence length for agentic_code sessions.",
)
_FIXED_SCHEDULE = flags.DEFINE_bool(
    "aiperf_fixed_schedule",
    False,
    "Replay traces using recorded timestamps. Requires a single point.",
)
_EXTRA_ARGS = flags.DEFINE_multi_string(
    "aiperf_extra_args",
    [],
    "Extra arguments to pass directly to the AIPerf profile command.",
)

_ENDPOINT_TYPE = flags.DEFINE_string(
    "aiperf_endpoint_type",
    "chat",
    "AIPerf endpoint type, for example chat or completions.",
)
_STREAMING = flags.DEFINE_bool(
    "aiperf_streaming",
    True,
    "Whether to request streaming responses from the server.",
)
_API_KEY = flags.DEFINE_string(
    "aiperf_api_key",
    None,
    "Optional API key passed to AIPerf via --api-key.",
)
_HF_TOKEN = flags.DEFINE_string(
    "aiperf_hf_token",
    None,
    "Optional Hugging Face token used when downloading the tokenizer.",
)

_MODEL_SIZE = flags.DEFINE_enum(
    "aiperf_model_size",
    "E2B",
    ["E2B", "E4B", "12B", "26B-A4B", "31B"],
    "Gemma 4 model size to serve. One size is supported per benchmark run.",
)
_MODEL_QUANTIZATION = flags.DEFINE_enum(
    "aiperf_model_quantization",
    "Q4_0",
    ["Q4_0", "Q8_0"],
    "GGUF quantization to download and serve.",
)
_MODEL_REPO = flags.DEFINE_string(
    "aiperf_model_repo",
    "ggml-org/gemma-4-E2B-it-GGUF",
    "Hugging Face GGUF repository. Overrides --aiperf_model_size.",
)
_MODEL_FILE = flags.DEFINE_string(
    "aiperf_model_file",
    "gemma-4-E2B-it-Q4_0.gguf",
    "GGUF filename. Overrides model size and quantization flags.",
)
_MODEL_URL = flags.DEFINE_string(
    "aiperf_model_url",
    None,
    "Optional explicit model download URL. Overrides the repository and file.",
)
_MODEL_ALIAS = flags.DEFINE_string(
    "aiperf_model_alias",
    "gemma-4-E2B-it",
    "llama-server model alias used by AIPerf. Overrides model size.",
)
_TOKENIZER = flags.DEFINE_string(
    "aiperf_tokenizer",
    "google/gemma-4-E2B-it",
    "AIPerf tokenizer identifier or local path. Overrides model size.",
)
_MODEL_DIR = flags.DEFINE_string(
    "aiperf_model_dir",
    "/opt/pkb/models",
    "Directory on the server VM in which to store the GGUF model.",
)

_SERVER_PORT = flags.DEFINE_integer(
    "aiperf_server_port",
    8080,
    "Port on which llama-server listens.",
)
_SERVER_THREADS = flags.DEFINE_integer(
    "aiperf_server_threads",
    None,
    "Number of llama-server inference threads. Defaults to the VM CPU count.",
)
_SERVER_CTX_SIZE = flags.DEFINE_integer(
    "aiperf_server_ctx_size",
    16384,
    "llama-server context size per parallel slot.",
)
_SERVER_PARALLEL = flags.DEFINE_integer(
    "aiperf_server_parallel",
    None,
    "llama-server parallel slots. Defaults to max concurrency.",
)
_SERVER_STARTUP_TIMEOUT = flags.DEFINE_integer(
    "aiperf_server_startup_timeout",
    300,
    "Startup timeout in seconds. Defaults per model size.",
)
_SERVER_MEMORY_GB = flags.DEFINE_integer(
    "aiperf_server_memory_gb",
    64,
    "Approximate server memory in GiB used for validation.",
)

_ARTIFACT_ROOT = "/tmp/aiperf_artifacts"
_DATASET_ROOT = "/tmp/aiperf_datasets"
_DATASET_TIMEOUT = 60 * 30
_STAT_KEYS = (
    "avg",
    "min",
    "max",
    "p1",
    "p5",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "std",
)


@dataclasses.dataclass(frozen=True)
class SweepPoint:
  """A single load point in an AIPerf sweep."""

  dimension: str
  value: float
  label: str


@dataclasses.dataclass(frozen=True)
class ModelProfile:
  """Model metadata and CPU-friendly defaults for one Gemma 4 size."""

  size: str
  repo: str
  gguf_stem: str
  alias: str
  tokenizer: str
  max_context: int
  default_ctx_per_slot: int
  default_concurrency: tuple[str, ...]
  default_agentic_max_isl: int
  default_agentic_max_osl: int
  startup_timeout_s: int
  quant_sizes_gb: dict[str, float]
  kv_bytes_per_token: int


# KV cache bytes per token, including keys and values at two bytes per
# element, computed from each model's layer count, KV head count, and head
# dimension.
MODEL_PROFILES: dict[str, ModelProfile] = {
    "E2B": ModelProfile(
        size="E2B",
        repo="ggml-org/gemma-4-E2B-it-GGUF",
        gguf_stem="gemma-4-E2B-it",
        alias="gemma-4-E2B-it",
        tokenizer="google/gemma-4-E2B-it",
        max_context=131072,
        default_ctx_per_slot=16384,
        default_concurrency=("1", "2", "4", "8"),
        default_agentic_max_isl=15360,
        default_agentic_max_osl=256,
        startup_timeout_s=300,
        quant_sizes_gb={"Q4_0": 2.84, "Q8_0": 4.97},
        kv_bytes_per_token=35840,
    ),
    "E4B": ModelProfile(
        size="E4B",
        repo="ggml-org/gemma-4-E4B-it-GGUF",
        gguf_stem="gemma-4-E4B-it",
        alias="gemma-4-E4B-it",
        tokenizer="google/gemma-4-E4B-it",
        max_context=131072,
        default_ctx_per_slot=16384,
        default_concurrency=("1", "2", "4", "8"),
        default_agentic_max_isl=15360,
        default_agentic_max_osl=256,
        startup_timeout_s=300,
        quant_sizes_gb={"Q4_0": 4.59, "Q8_0": 8.03},
        kv_bytes_per_token=86016,
    ),
    "12B": ModelProfile(
        size="12B",
        repo="ggml-org/gemma-4-12B-it-GGUF",
        gguf_stem="gemma-4-12B-it",
        alias="gemma-4-12B-it",
        tokenizer="google/gemma-4-12B-it",
        max_context=262144,
        default_ctx_per_slot=16384,
        default_concurrency=("1", "2", "4"),
        default_agentic_max_isl=15360,
        default_agentic_max_osl=256,
        startup_timeout_s=600,
        quant_sizes_gb={"Q4_0": 7.22, "Q8_0": 12.67},
        kv_bytes_per_token=393216,
    ),
    "26B-A4B": ModelProfile(
        size="26B-A4B",
        repo="ggml-org/gemma-4-26B-A4B-it-GGUF",
        gguf_stem="gemma-4-26B-A4B-it",
        alias="gemma-4-26B-A4B-it",
        tokenizer="google/gemma-4-26B-A4B-it",
        max_context=262144,
        default_ctx_per_slot=16384,
        default_concurrency=("1", "2", "4"),
        default_agentic_max_isl=15360,
        default_agentic_max_osl=256,
        startup_timeout_s=600,
        quant_sizes_gb={"Q4_0": 14.62, "Q8_0": 26.86},
        kv_bytes_per_token=245760,
    ),
    "31B": ModelProfile(
        size="31B",
        repo="ggml-org/gemma-4-31B-it-GGUF",
        gguf_stem="gemma-4-31B-it",
        alias="gemma-4-31B-it",
        tokenizer="google/gemma-4-31B-it",
        max_context=262144,
        default_ctx_per_slot=8192,
        default_concurrency=("1", "2"),
        default_agentic_max_isl=7168,
        default_agentic_max_osl=128,
        startup_timeout_s=900,
        quant_sizes_gb={"Q4_0": 17.99, "Q8_0": 32.64},
        kv_bytes_per_token=983040,
    ),
}


def GetModelProfile() -> ModelProfile:
  """Returns the selected model profile."""
  return MODEL_PROFILES[_MODEL_SIZE.value]


def _FlagIsSet(flag_name: str) -> bool:
  """Returns whether a flag was explicitly set, including via flagsaver."""
  flag = FLAGS[flag_name]
  return flag.present or flag.value != flag.default


def _EffectiveModelRepo(profile: ModelProfile) -> str:
  """Returns the model repository, honoring an explicit override."""
  if _FlagIsSet("aiperf_model_repo"):
    return _MODEL_REPO.value
  return profile.repo


def _EffectiveQuantization(profile: ModelProfile) -> str:
  """Returns the selected quantization, inferring it from an explicit file."""
  del profile
  if _FlagIsSet("aiperf_model_quantization"):
    return _MODEL_QUANTIZATION.value
  if _FlagIsSet("aiperf_model_file"):
    for quantization in ("Q8_0", "Q4_0"):
      if _MODEL_FILE.value.endswith(f"{quantization}.gguf"):
        return quantization
  return _MODEL_QUANTIZATION.value


def _EffectiveModelFile(profile: ModelProfile) -> str:
  """Returns the GGUF filename, honoring an explicit override."""
  if _FlagIsSet("aiperf_model_file"):
    return _MODEL_FILE.value
  return f"{profile.gguf_stem}-{_EffectiveQuantization(profile)}.gguf"


def _EffectiveModelAlias(profile: ModelProfile) -> str:
  """Returns the llama-server model alias, honoring an explicit override."""
  if _FlagIsSet("aiperf_model_alias"):
    return _MODEL_ALIAS.value
  return profile.alias


def _EffectiveTokenizer(profile: ModelProfile) -> str:
  """Returns the AIPerf tokenizer, honoring an explicit override."""
  if _FlagIsSet("aiperf_tokenizer"):
    return _TOKENIZER.value
  return profile.tokenizer


def _EffectiveConcurrency(profile: ModelProfile) -> list[str]:
  """Returns the concurrency sweep, honoring an explicit override."""
  if _FlagIsSet("aiperf_concurrency"):
    return _CONCURRENCY.value
  return list(profile.default_concurrency)


def _EffectiveServerCtxPerSlot(profile: ModelProfile) -> int:
  """Returns the llama-server context per slot."""
  if _FlagIsSet("aiperf_server_ctx_size"):
    return _SERVER_CTX_SIZE.value
  return profile.default_ctx_per_slot


def _EffectiveServerStartupTimeout(profile: ModelProfile) -> int:
  """Returns the llama-server startup timeout in seconds."""
  if _FlagIsSet("aiperf_server_startup_timeout"):
    return _SERVER_STARTUP_TIMEOUT.value
  return profile.startup_timeout_s


def _EffectiveAgenticMaxIsl(profile: ModelProfile) -> int | None:
  """Returns the effective agentic maximum input sequence length."""
  if _FlagIsSet("aiperf_agentic_max_isl"):
    return _AGENTIC_MAX_ISL.value
  return profile.default_agentic_max_isl


def _EffectiveAgenticMaxOsl(profile: ModelProfile) -> int | None:
  """Returns the effective agentic maximum output sequence length."""
  if _FlagIsSet("aiperf_agentic_max_osl"):
    return _AGENTIC_MAX_OSL.value
  return profile.default_agentic_max_osl


def _EstimateServerMemoryGb(
    profile: ModelProfile,
    ctx_per_slot: int,
    parallel: int,
) -> float:
  """Estimates the server memory needed for the model and KV cache."""
  quantization = _EffectiveQuantization(profile)
  model_gb = profile.quant_sizes_gb[quantization]
  kv_gb = ctx_per_slot * parallel * profile.kv_bytes_per_token / 1e9
  return model_gb + kv_gb + 2.0


def _EffectiveServerParallel(profile: ModelProfile) -> int:
  """Returns the number of llama-server parallel slots."""
  if _SERVER_PARALLEL.value is not None:
    return _SERVER_PARALLEL.value
  if _SWEEP.value == "concurrency":
    return int(max(point.value for point in GetSweepPoints()))
  return int(max(float(value) for value in _EffectiveConcurrency(profile)))


def GetConfig(user_config: dict[Any, Any]) -> dict[Any, Any]:
  """Loads and returns the benchmark configuration."""
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def GetSweepPoints() -> list[SweepPoint]:
  """Returns the load points to evaluate in this benchmark run."""
  if _SWEEP.value == "request_rate":
    raw_points = _REQUEST_RATE.value
  else:
    raw_points = _EffectiveConcurrency(GetModelProfile())

  if not raw_points:
    raise errors.Config.InvalidValue(
        f"aiperf_{_SWEEP.value} must contain at least one value."
    )

  points = []
  for raw_point in raw_points:
    try:
      value = float(raw_point)
    except ValueError as e:
      raise errors.Config.InvalidValue(
          f"Invalid {_SWEEP.value} value: {raw_point!r}"
      ) from e
    if value <= 0:
      raise errors.Config.InvalidValue(
          f"{_SWEEP.value} values must be positive. Got {raw_point!r}."
      )
    if _SWEEP.value == "concurrency" and not value.is_integer():
      raise errors.Config.InvalidValue(
          f"Concurrency values must be integers. Got {raw_point!r}."
      )
    prefix = "c" if _SWEEP.value == "concurrency" else "r"
    points.append(
        SweepPoint(
            dimension=_SWEEP.value,
            value=value,
            label=prefix + _FormatValue(value),
        )
    )
  return points


def _FormatValue(value: float) -> str:
  """Formats a sweep value for use in an artifact path."""
  if value.is_integer():
    return str(int(value))
  return str(value).replace(".", "_")


def _CliValue(value: float) -> str:
  """Formats a sweep value for the AIPerf command line."""
  if value.is_integer():
    return str(int(value))
  return str(value)


def CheckPrerequisites(benchmark_config) -> None:
  """Validates the benchmark flags before provisioning."""
  del benchmark_config
  points = GetSweepPoints()
  if _FIXED_SCHEDULE.value:
    if _DATASET.value != "agentic_code":
      raise errors.Config.InvalidValue(
          "aiperf_fixed_schedule is only supported with"
          " --aiperf_dataset=agentic_code."
      )
    if len(points) > 1:
      raise errors.Config.InvalidValue(
          "aiperf_fixed_schedule only supports a single sweep point. Use"
          " --aiperf_concurrency=<value> instead of a list, or leave"
          " aiperf_fixed_schedule disabled."
      )
  if _REQUEST_COUNT.value <= 0 and _BENCHMARK_DURATION.value is None:
    raise errors.Config.InvalidValue(
        "aiperf_request_count must be positive when"
        " aiperf_benchmark_duration is not set."
    )
  if _BENCHMARK_DURATION.value is not None and _BENCHMARK_DURATION.value <= 0:
    raise errors.Config.InvalidValue(
        "aiperf_benchmark_duration must be positive."
    )
  if _AGENTIC_NUM_SESSIONS.value <= 0:
    raise errors.Config.InvalidValue(
        "aiperf_agentic_num_sessions must be positive."
    )
  if _WARMUP_REQUESTS.value < 0:
    raise errors.Config.InvalidValue(
        "aiperf_warmup_requests must be non-negative."
    )
  if not 0 < _SERVER_PORT.value < 65536:
    raise errors.Config.InvalidValue(
        "aiperf_server_port must be between 1 and 65535."
    )
  if _SERVER_THREADS.value is not None and _SERVER_THREADS.value <= 0:
    raise errors.Config.InvalidValue("aiperf_server_threads must be positive.")
  profile = GetModelProfile()
  ctx_per_slot = _EffectiveServerCtxPerSlot(profile)
  if ctx_per_slot <= 0:
    raise errors.Config.InvalidValue("aiperf_server_ctx_size must be positive.")
  if ctx_per_slot > profile.max_context:
    raise errors.Config.InvalidValue(
        f"aiperf_server_ctx_size ({ctx_per_slot}) exceeds the maximum context"
        f" for Gemma 4 {profile.size} ({profile.max_context})."
    )
  if _SERVER_PARALLEL.value is not None and _SERVER_PARALLEL.value <= 0:
    raise errors.Config.InvalidValue("aiperf_server_parallel must be positive.")
  if _EffectiveServerStartupTimeout(profile) <= 0:
    raise errors.Config.InvalidValue(
        "aiperf_server_startup_timeout must be positive."
    )
  if _SERVER_MEMORY_GB.value <= 0:
    raise errors.Config.InvalidValue(
        "aiperf_server_memory_gb must be positive."
    )

  if _DATASET.value == "agentic_code":
    max_isl = _EffectiveAgenticMaxIsl(profile)
    if max_isl is not None:
      if max_isl > profile.max_context:
        raise errors.Config.InvalidValue(
            f"aiperf_agentic_max_isl ({max_isl}) exceeds the maximum context"
            f" for Gemma 4 {profile.size} ({profile.max_context})."
        )
      if max_isl + 1024 > ctx_per_slot:
        raise errors.Config.InvalidValue(
            f"aiperf_agentic_max_isl ({max_isl}) does not fit in the"
            f" configured context per slot ({ctx_per_slot}). Increase"
            " aiperf_server_ctx_size or lower aiperf_agentic_max_isl."
        )
    max_osl = _EffectiveAgenticMaxOsl(profile)
    if max_osl is not None and max_osl > ctx_per_slot:
      raise errors.Config.InvalidValue(
          f"aiperf_agentic_max_osl ({max_osl}) does not fit in the"
          f" configured context per slot ({ctx_per_slot})."
      )
  elif _INPUT_TOKENS_MEAN.value is not None:
    total_tokens = _INPUT_TOKENS_MEAN.value + (_OUTPUT_TOKENS_MEAN.value or 0)
    if total_tokens > profile.max_context:
      raise errors.Config.InvalidValue(
          f"Synthetic input plus output tokens ({total_tokens}) exceeds the"
          f" maximum context for Gemma 4 {profile.size}"
          f" ({profile.max_context})."
      )
    if total_tokens + 1024 > ctx_per_slot:
      raise errors.Config.InvalidValue(
          f"Synthetic input plus output tokens ({total_tokens}) do not fit in"
          f" the configured context per slot ({ctx_per_slot})."
      )

  parallel = _EffectiveServerParallel(profile)
  estimated_gb = _EstimateServerMemoryGb(profile, ctx_per_slot, parallel)
  memory_limit_gb = 0.9 * _SERVER_MEMORY_GB.value
  if estimated_gb > memory_limit_gb:
    raise errors.Config.InvalidValue(
        f"Estimated server memory ({estimated_gb:.1f} GB) exceeds 90% of"
        f" aiperf_server_memory_gb ({_SERVER_MEMORY_GB.value} GB) for"
        f" Gemma 4 {profile.size}"
        f" {_EffectiveQuantization(profile)} with {ctx_per_slot} context"
        f" tokens per slot and {parallel} parallel slots. Reduce"
        " aiperf_server_ctx_size, lower the concurrency sweep, or use Q4_0."
    )


def _GetVms(
    benchmark_spec: bm_spec.BenchmarkSpec,
) -> tuple[
    virtual_machine.BaseVirtualMachine,
    virtual_machine.BaseVirtualMachine,
]:
  """Returns the client and server VMs."""
  vm_groups = benchmark_spec.vm_groups
  if not vm_groups or "client" not in vm_groups or "server" not in vm_groups:
    raise errors.Benchmarks.PrepareException(
        "aiperf_cpu requires client and server VM groups."
    )
  return vm_groups["client"][0], vm_groups["server"][0]


def Prepare(benchmark_spec: bm_spec.BenchmarkSpec) -> None:
  """Installs AIPerf and llama.cpp, then starts the inference server."""
  client_vm, server_vm = _GetVms(benchmark_spec)
  client_vm.Install("curl")
  client_vm.Install("aiperf")
  server_vm.Install("llama_cpp")

  model_path = _DownloadModel(server_vm)
  benchmark_spec.aiperf_dataset_path = PrepareDataset(client_vm)

  server_vm.AllowPort(
      _SERVER_PORT.value,
      source_range=[client_vm.internal_ip + "/32"],
  )
  _StartServer(server_vm, model_path)
  _WaitForServer(
      client_vm,
      server_vm,
      server_vm.internal_ip,
      _SERVER_PORT.value,
  )


def _DownloadModel(server_vm: virtual_machine.BaseVirtualMachine) -> str:
  """Downloads the GGUF model on the server VM if it is not already present."""
  profile = GetModelProfile()
  model_file = _EffectiveModelFile(profile)
  model_path = posixpath.join(_MODEL_DIR.value, model_file)
  if server_vm.TryRemoteCommand(f"sudo test -s {shlex.quote(model_path)}"):
    return model_path

  model_url = _MODEL_URL.value or (
      f"https://huggingface.co/{_EffectiveModelRepo(profile)}/resolve/main/"
      f"{model_file}"
  )
  temp_path = model_path + ".tmp"
  server_vm.RemoteCommand(f"sudo mkdir -p {shlex.quote(_MODEL_DIR.value)}")
  server_vm.RemoteCommand(
      "sudo curl -L --fail --retry 5 --retry-delay 5"
      f" -o {shlex.quote(temp_path)} {shlex.quote(model_url)}"
      f" && sudo mv {shlex.quote(temp_path)} {shlex.quote(model_path)}",
      timeout=7200,
  )
  return model_path


def _StartServer(
    server_vm: virtual_machine.BaseVirtualMachine,
    model_path: str,
) -> None:
  """Starts llama-server in the background."""
  profile = GetModelProfile()
  parallel = _EffectiveServerParallel(profile)
  ctx_per_slot = _EffectiveServerCtxPerSlot(profile)
  args = [
      llama_cpp.LLAMA_SERVER_BIN,
      "-m",
      model_path,
      "--alias",
      _EffectiveModelAlias(profile),
      "--host",
      "0.0.0.0",
      "--port",
      str(_SERVER_PORT.value),
      "--threads",
      str(_ServerThreads(server_vm)),
      "--ctx-size",
      str(ctx_per_slot * parallel),
      "--parallel",
      str(parallel),
      "--no-webui",
      "--jinja",
  ]
  server_vm.RemoteCommand(
      "pkill -x llama-server || true",
      ignore_failure=True,
  )
  server_vm.RemoteCommand("rm -f /tmp/llama-server.log", ignore_failure=True)
  server_vm.RemoteCommand(
      f"nohup {shlex.join(args)} > /tmp/llama-server.log 2>&1 & echo $!"
  )


def _ServerThreads(server_vm: virtual_machine.BaseVirtualMachine) -> int:
  """Returns the number of llama-server inference threads."""
  return _SERVER_THREADS.value or server_vm.NumCpusForBenchmark()


def _ServerParallel() -> int:
  """Returns the number of llama-server parallel slots."""
  return _EffectiveServerParallel(GetModelProfile())


def _WaitForServer(
    client_vm: virtual_machine.BaseVirtualMachine,
    server_vm: virtual_machine.BaseVirtualMachine,
    server_ip: str,
    port: int,
) -> None:
  """Waits for llama-server to become healthy, as seen from the client."""
  timeout = _EffectiveServerStartupTimeout(GetModelProfile())
  command = (
      f'timeout {timeout} bash -c "until curl -sf'
      f' http://{server_ip}:{port}/health; do sleep 2; done"'
  )
  try:
    client_vm.RemoteCommand(command, timeout=timeout + 60)
  except (
      errors.VmUtil.IssueCommandTimeoutError,
      errors.VirtualMachine.RemoteCommandError,
  ) as e:
    try:
      log, _ = server_vm.RemoteCommand(
          "tail -n 50 /tmp/llama-server.log",
          ignore_failure=True,
      )
    except errors.Error:
      log = "<unable to read llama-server log>"
    raise errors.Benchmarks.PrepareException(
        f"llama-server did not become healthy at {server_ip}:{port}."
        f" Server log:\n{log}"
    ) from e


def PrepareDataset(
    client_vm: virtual_machine.BaseVirtualMachine,
) -> str | None:
  """Prepares the configured AIPerf dataset on the client VM.

  Args:
    client_vm: The VM on which AIPerf is installed.

  Returns:
    The remote path to the generated dataset, or None for synthetic datasets.
  """
  client_vm.RemoteCommand(f"rm -rf {_DATASET_ROOT}")
  if _DATASET.value == "synthetic":
    return None

  client_vm.RemoteCommand(f"mkdir -p {_DATASET_ROOT}")
  profile = GetModelProfile()
  args = [
      aiperf.AIPERF_BIN,
      "synthesize",
      "agentic-code",
      "--num-sessions",
      str(_AGENTIC_NUM_SESSIONS.value),
      "--seed",
      str(_AGENTIC_SEED.value),
      "--output",
      _DATASET_ROOT,
  ]
  max_isl = _EffectiveAgenticMaxIsl(profile)
  max_osl = _EffectiveAgenticMaxOsl(profile)
  if max_isl is not None:
    args += ["--max-isl", str(max_isl)]
  if max_osl is not None:
    args += ["--max-osl", str(max_osl)]
  client_vm.RemoteCommand(shlex.join(args), timeout=_DATASET_TIMEOUT)

  stdout, _ = client_vm.RemoteCommand(
      f"find {_DATASET_ROOT} -name dataset.jsonl -type f | sort | tail -n 1"
  )
  dataset_path = stdout.strip()
  if not dataset_path:
    raise errors.Benchmarks.PrepareException(
        "AIPerf did not generate an agentic_code dataset.jsonl."
    )
  return dataset_path


def CleanupDataset(client_vm: virtual_machine.BaseVirtualMachine) -> None:
  """Removes generated AIPerf datasets from the client VM."""
  client_vm.RemoteCommand(f"rm -rf {_DATASET_ROOT}", ignore_failure=True)


def GetCommonMetadata(
    client_vm: virtual_machine.BaseVirtualMachine,
    server_vm: virtual_machine.BaseVirtualMachine,
) -> dict[str, Any]:
  """Returns metadata shared by every sample in this benchmark."""
  return {
      "client_machine_type": getattr(client_vm, "machine_type", ""),
      "server_machine_type": getattr(server_vm, "machine_type", ""),
      "aiperf_version": aiperf.AIPERF_VERSION.value,
  }


def BuildProfileCommand(
    url: str,
    point: SweepPoint,
    artifact_dir: str,
    dataset_path: str | None = None,
    model: str | None = None,
    tokenizer: str | None = None,
) -> str:
  """Builds the AIPerf profile command for a single sweep point."""
  profile = GetModelProfile()
  model = model or _EffectiveModelAlias(profile)
  tokenizer = tokenizer or _EffectiveTokenizer(profile)
  args = [
      aiperf.AIPERF_BIN,
      "profile",
      "--url",
      url,
      "--model",
      model,
      "--endpoint-type",
      _ENDPOINT_TYPE.value,
      "--ui-type",
      "none",
      "--export-level",
      "summary",
      "--artifact-dir",
      artifact_dir,
      "--warmup-request-count",
      str(_WARMUP_REQUESTS.value),
      "--random-seed",
      str(_RANDOM_SEED.value),
  ]
  if _REQUEST_COUNT.value > 0:
    args += ["--request-count", str(_REQUEST_COUNT.value)]
  if _STREAMING.value:
    args.append("--streaming")
  if tokenizer:
    args += ["--tokenizer", tokenizer]
  if _BENCHMARK_DURATION.value is not None:
    args += ["--benchmark-duration", str(_BENCHMARK_DURATION.value)]
  if point.dimension == "concurrency":
    args += ["--concurrency", _CliValue(point.value)]
  else:
    args += ["--request-rate", _CliValue(point.value)]

  if _DATASET.value == "synthetic":
    if _INPUT_TOKENS_MEAN.value is not None:
      args += ["--isl", str(_INPUT_TOKENS_MEAN.value)]
    if _OUTPUT_TOKENS_MEAN.value is not None:
      args += ["--osl", str(_OUTPUT_TOKENS_MEAN.value)]
  else:
    if not dataset_path:
      raise errors.Benchmarks.RunError(
          "An agentic_code dataset is required for the selected dataset."
      )
    args += [
        "--input-file",
        dataset_path,
        "--custom-dataset-type",
        "mooncake_trace",
    ]
    if _FIXED_SCHEDULE.value:
      args.append("--fixed-schedule")
    else:
      args += ["--no-fixed-schedule", "--ignore-trace-delays"]

  if _API_KEY.value:
    args += ["--api-key", _API_KEY.value]
  args.extend(_EXTRA_ARGS.value)

  command = shlex.join(args)
  if _HF_TOKEN.value:
    command = f"HF_TOKEN={shlex.quote(_HF_TOKEN.value)} {command}"
  return command


def _RedactCommand(command: str) -> str:
  """Removes secrets from a command before logging it."""
  for secret in (_API_KEY.value, _HF_TOKEN.value):
    if secret:
      command = command.replace(secret, "<redacted>")
  return command


def RunProfilePoint(
    client_vm: virtual_machine.BaseVirtualMachine,
    url: str,
    point: SweepPoint,
    artifact_dir: str,
    dataset_path: str | None,
    metadata: dict[str, Any],
    point_index: int = 0,
    model: str | None = None,
    tokenizer: str | None = None,
) -> list[sample.Sample]:
  """Runs one AIPerf profile and returns its samples."""
  client_vm.RemoteCommand(f"rm -rf {shlex.quote(artifact_dir)}")
  command = BuildProfileCommand(
      url,
      point,
      artifact_dir,
      dataset_path,
      model=model,
      tokenizer=tokenizer,
  )
  logging.info(
      "Running AIPerf point %s: %s",
      point.label,
      _RedactCommand(command),
  )
  try:
    client_vm.RemoteCommand(command, timeout=_TIMEOUT.value)
  except (
      errors.VmUtil.IssueCommandTimeoutError,
      errors.VirtualMachine.RemoteCommandError,
  ) as e:
    raise errors.Benchmarks.RunError(
        f"AIPerf profile failed for point {point.label}."
    ) from e

  remote_export_path = _FindExportFile(client_vm, artifact_dir)
  local_export_path = os.path.join(
      vm_util.GetTempDir(),
      f'aiperf_{FLAGS.run_uri or "run"}_{point.label}.json',
  )
  client_vm.PullFile(local_export_path, remote_export_path)
  with open(local_export_path, encoding="utf-8") as f:
    export_data = json.load(f)

  point_metadata = dict(metadata)
  point_metadata.update({
      "aiperf_url": url,
      "dataset": _DATASET.value,
      "fixed_schedule": _FIXED_SCHEDULE.value,
      "request_count": _REQUEST_COUNT.value,
      "sweep_dimension": point.dimension,
      "point_index": point_index,
      "point_label": point.label,
      point.dimension: point.value,
  })
  if _BENCHMARK_DURATION.value is not None:
    point_metadata["benchmark_duration"] = _BENCHMARK_DURATION.value
  return ParseExport(export_data, point_metadata)


def _FindExportFile(
    client_vm: virtual_machine.BaseVirtualMachine,
    artifact_dir: str,
) -> str:
  """Finds the AIPerf aggregate export beneath an artifact directory."""
  stdout, _ = client_vm.RemoteCommand(
      f"find {shlex.quote(artifact_dir)}"
      " -name profile_export_aiperf.json -type f | head -n 1"
  )
  export_path = stdout.strip()
  if not export_path:
    raise errors.Benchmarks.RunError(
        f"No profile_export_aiperf.json found under {artifact_dir}."
    )
  return export_path


def ParseExport(
    data: dict[str, Any],
    metadata: dict[str, Any],
) -> list[sample.Sample]:
  """Converts an AIPerf aggregate export into PKB samples."""
  base_metadata = dict(metadata)
  if data.get("schema_version") is not None:
    base_metadata["aiperf_schema_version"] = str(data["schema_version"])
  if data.get("benchmark_id") is not None:
    base_metadata["aiperf_benchmark_id"] = str(data["benchmark_id"])

  samples = []
  for metric, raw_block in data.items():
    if not isinstance(raw_block, dict) or "unit" not in raw_block:
      continue
    metric_metadata = dict(base_metadata)
    metric_metadata["aiperf_metric"] = metric
    if raw_block.get("count") is not None:
      metric_metadata["aiperf_metric_count"] = raw_block["count"]
    if raw_block.get("sum") is not None:
      metric_metadata["aiperf_metric_sum"] = raw_block["sum"]

    for stat in _STAT_KEYS:
      value = raw_block.get(stat)
      if value is None:
        continue
      stat_metadata = dict(metric_metadata)
      stat_metadata["aiperf_stat"] = stat
      samples.append(
          sample.Sample(
              f"{metric}_{stat}",
              value,
              raw_block["unit"],
              stat_metadata,
          )
      )

  error_metadata = dict(base_metadata)
  error_metadata["aiperf_metric"] = "error_count"
  samples.append(
      sample.Sample(
          "error_count",
          _GetErrorCount(data),
          "count",
          error_metadata,
      )
  )
  return samples


def _GetErrorCount(data: dict[str, Any]) -> int:
  """Returns the total number of errors reported by AIPerf."""
  error_summary = data.get("error_summary") or []
  if isinstance(error_summary, list):
    return sum(
        int(item.get("count", 0))
        for item in error_summary
        if isinstance(item, dict)
    )
  if isinstance(error_summary, dict):
    return sum(
        int(value)
        for value in error_summary.values()
        if isinstance(value, (int, float))
    )
  return 0


def Run(benchmark_spec: bm_spec.BenchmarkSpec) -> list[sample.Sample]:
  """Runs AIPerf for every configured sweep point."""
  client_vm, server_vm = _GetVms(benchmark_spec)
  url = f"http://{server_vm.internal_ip}:{_SERVER_PORT.value}"
  points = GetSweepPoints()
  dataset_path = getattr(benchmark_spec, "aiperf_dataset_path", None)

  metadata = GetCommonMetadata(client_vm, server_vm)
  profile = GetModelProfile()
  parallel = _ServerParallel()
  ctx_per_slot = _EffectiveServerCtxPerSlot(profile)
  metadata.update({
      "model_size": profile.size,
      "model_quantization": _EffectiveQuantization(profile),
      "model_max_context": profile.max_context,
      "model_repo": _EffectiveModelRepo(profile),
      "model_file": _EffectiveModelFile(profile),
      "model_alias": _EffectiveModelAlias(profile),
      "model_tokenizer": _EffectiveTokenizer(profile),
      "llama_cpp_version": llama_cpp.LLAMA_CPP_VERSION.value,
      "server_threads": _ServerThreads(server_vm),
      "server_ctx_per_slot": ctx_per_slot,
      "server_ctx_total": ctx_per_slot * parallel,
      "server_parallel": parallel,
      "server_estimated_memory_gb": _EstimateServerMemoryGb(
          profile, ctx_per_slot, parallel
      ),
  })
  if _DATASET.value == "agentic_code":
    metadata["agentic_max_isl"] = _EffectiveAgenticMaxIsl(profile)
    metadata["agentic_max_osl"] = _EffectiveAgenticMaxOsl(profile)

  samples = []
  for point_index, point in enumerate(points):
    artifact_dir = posixpath.join(_ArtifactRoot(), point.label)
    try:
      samples.extend(
          RunProfilePoint(
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
      if not _CONTINUE_ON_ERROR.value:
        raise
      logging.exception("AIPerf point %s failed; continuing.", point.label)

  if not samples:
    raise errors.Benchmarks.RunError("No AIPerf samples were collected.")
  return samples


def _ArtifactRoot() -> str:
  """Returns the remote directory for this run's AIPerf artifacts."""
  return posixpath.join(_ARTIFACT_ROOT, FLAGS.run_uri or "run")


def Cleanup(benchmark_spec: bm_spec.BenchmarkSpec) -> None:
  """Stops the server and removes generated artifacts."""
  try:
    client_vm, server_vm = _GetVms(benchmark_spec)
  except errors.Benchmarks.PrepareException:
    logging.exception("Unable to resolve VMs during cleanup.")
    return

  server_vm.RemoteCommand(
      "pkill -x llama-server || true",
      ignore_failure=True,
  )
  CleanupDataset(client_vm)
  client_vm.RemoteCommand(
      f"rm -rf {shlex.quote(_ArtifactRoot())}",
      ignore_failure=True,
  )
