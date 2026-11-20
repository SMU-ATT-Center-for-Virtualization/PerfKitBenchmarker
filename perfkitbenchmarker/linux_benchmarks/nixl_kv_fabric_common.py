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
"""Shared NIXL DRAM-to-DRAM KV fabric benchmark helpers."""

import dataclasses
import json
import logging
import posixpath
import re
import shlex
import time
from typing import Any

from absl import flags
from perfkitbenchmarker import errors
from perfkitbenchmarker import sample
from perfkitbenchmarker.linux_packages import kvbench
from perfkitbenchmarker.linux_packages import nixl
from perfkitbenchmarker.linux_packages import nixlbench
from perfkitbenchmarker.linux_packages import ucx

FLAGS = flags.FLAGS

WORKER_COUNT = flags.DEFINE_integer(
    'nixl_worker_count',
    2,
    'Number of NIXL workers. The initial ASIO implementation requires two.',
)
ASIO_PORT = flags.DEFINE_integer(
    'nixl_asio_port', 23456, 'TCP port used by NIXLBench ASIO coordination.'
)
STARTUP_DELAY = flags.DEFINE_integer(
    'nixl_startup_delay_seconds',
    10,
    'Seconds to wait after starting the ASIO target before the initiator.',
)

MODEL = flags.DEFINE_string(
    'nixl_kvbench_model',
    'llama_3_1_8b',
    'KVBench model architecture to use.',
)
MODEL_CONFIG = flags.DEFINE_string(
    'nixl_kvbench_model_config',
    'block-tp1-pp1',
    'KVBench model configuration to use.',
)
ISL_TOKENS = flags.DEFINE_list(
    'nixl_kvbench_isl_tokens',
    ['4096', '8192', '16384', '32768'],
    'Comma-separated input sequence lengths, in tokens.',
)
MAX_TOKENS = flags.DEFINE_integer(
    'nixl_kvbench_max_tokens',
    32768,
    'Maximum allowed input sequence length, in tokens.',
)
PAGE_SIZE = flags.DEFINE_integer(
    'nixl_kvbench_page_size', 16, 'KVBench page size in tokens.'
)
NUM_REQUESTS = flags.DEFINE_integer(
    'nixl_kvbench_num_requests', 10, 'KVBench number of requests.'
)
TP = flags.DEFINE_integer('nixl_kvbench_tp', 1, 'KVBench tensor parallelism.')
PP = flags.DEFINE_integer(
    'nixl_kvbench_pp', 1, 'KVBench pipeline parallelism.'
)
ACCESS_PATTERN = flags.DEFINE_enum(
    'nixl_kvbench_access_pattern',
    'block',
    ['block'],
    'KVBench KV access pattern.',
)

NUM_ITER = flags.DEFINE_integer(
    'nixl_num_iter', 1000, 'NIXLBench measured iterations.'
)
WARMUP_ITER = flags.DEFINE_integer(
    'nixl_warmup_iter', 100, 'NIXLBench warmup iterations.'
)
NUM_THREADS = flags.DEFINE_integer(
    'nixl_num_threads', 1, 'NIXLBench worker threads.'
)
PROGRESS_THREADS = flags.DEFINE_integer(
    'nixl_progress_threads', 0, 'NIXLBench progress threads.'
)
TOTAL_BUFFER_SIZE = flags.DEFINE_string(
    'nixl_total_buffer_size',
    'auto',
    'NIXLBench total buffer size. "auto" uses the KVBench-resolved size '
    'multiplied by nixl_num_threads.',
)
OP_TYPES = flags.DEFINE_list(
    'nixl_op_types',
    ['READ', 'WRITE'],
    'Comma-separated NIXLBench operation types.',
)
CHECK_CONSISTENCY = flags.DEFINE_bool(
    'nixl_check_consistency',
    False,
    'Whether NIXLBench should validate transferred data.',
)
CONTINUE_ON_ERROR = flags.DEFINE_bool(
    'nixl_continue_on_error',
    False,
    'Whether to continue after a failing case.',
)
ARTIFACT_ROOT = flags.DEFINE_string(
    'nixl_artifact_root',
    '/tmp/nixl_kv_fabric',
    'Remote directory for NIXL benchmark artifacts.',
)

_SIZE_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([KMGT]?i?B?)\s*$', re.IGNORECASE)
_SIZE_UNITS = {
    '': 1,
    'B': 1,
    'K': 1024,
    'KB': 1024,
    'KIB': 1024,
    'M': 1024**2,
    'MB': 1024**2,
    'MIB': 1024**2,
    'G': 1024**3,
    'GB': 1024**3,
    'GIB': 1024**3,
    'T': 1024**4,
    'TB': 1024**4,
    'TIB': 1024**4,
}

MODEL_FILES = {
    'llama_3_1_8b': 'nixl/model_llama_3_1_8b.yaml',
}
MODEL_CONFIG_FILES = {
    'block-tp1-pp1': 'nixl/block-tp1-pp1.yaml',
}
REMOTE_DATA_DIR = '/tmp/nixl_kv_fabric_data'
CASE_TIMEOUT = 60 * 60 * 2

# NIXLBench output header to (sample name, unit).
_NIXL_COLUMNS = {
    'block_size_b': ('block_size_bytes', 'bytes'),
    'batch_size': ('batch_size', 'count'),
    'b_w_gb_sec': ('nixl_bandwidth_gbps', 'GB/s'),
    'aggregate_b_w_gb_sec': (
        'nixl_aggregate_bandwidth_gbps',
        'GB/s',
    ),
    'network_util_pct': ('nixl_network_utilization_pct', '%'),
    'avg_lat_us': ('nixl_avg_latency_us', 'us'),
    'avg_prep_us': ('nixl_avg_prepare_us', 'us'),
    'p99_prep_us': ('nixl_p99_prepare_us', 'us'),
    'avg_post_us': ('nixl_avg_post_us', 'us'),
    'p99_post_us': ('nixl_p99_post_us', 'us'),
    'avg_tx_us': ('nixl_avg_transfer_us', 'us'),
    'p99_tx_us': ('nixl_p99_transfer_us', 'us'),
}


@dataclasses.dataclass(frozen=True)
class KvBenchCase:
  """A KVBench-derived NIXLBench payload."""

  label: str
  model: str
  model_config: str
  isl_tokens: int
  page_size: int
  access_pattern: str
  num_requests: int
  tp: int
  pp: int
  resolved_block_size_bytes: int
  resolved_batch_size: int
  resolved_io_size_bytes: int
  resolved_total_buffer_size_bytes: int


def ParseSize(value: str | int | float) -> int:
  """Parses a byte size such as 4KiB, 1MiB, 8GiB, or 1024."""
  if isinstance(value, (int, float)):
    return int(value)
  match = _SIZE_RE.match(value)
  if not match:
    raise errors.Config.InvalidValue(f'Invalid byte size: {value!r}')
  number = float(match.group(1))
  unit = match.group(2).upper()
  if unit not in _SIZE_UNITS:
    raise errors.Config.InvalidValue(f'Unknown byte-size unit: {value!r}')
  return int(number * _SIZE_UNITS[unit])


def CheckPrerequisites(benchmark_config) -> None:
  """Validates NIXL benchmark flags before provisioning."""
  del benchmark_config
  if WORKER_COUNT.value != 2:
    raise errors.Config.InvalidValue(
        'nixl_kv_fabric ASIO currently requires --nixl_worker_count=2.'
    )
  if not 0 < ASIO_PORT.value < 65536:
    raise errors.Config.InvalidValue(
        'nixl_asio_port must be between 1 and 65535.'
    )
  if STARTUP_DELAY.value < 0:
    raise errors.Config.InvalidValue(
        'nixl_startup_delay_seconds must be non-negative.'
    )
  if MAX_TOKENS.value <= 0:
    raise errors.Config.InvalidValue(
        'nixl_kvbench_max_tokens must be positive.'
    )
  if PAGE_SIZE.value <= 0:
    raise errors.Config.InvalidValue(
        'nixl_kvbench_page_size must be positive.'
    )
  if NUM_REQUESTS.value <= 0:
    raise errors.Config.InvalidValue(
        'nixl_kvbench_num_requests must be positive.'
    )
  if TP.value <= 0 or PP.value <= 0:
    raise errors.Config.InvalidValue(
        'nixl_kvbench_tp and nixl_kvbench_pp must be positive.'
    )
  if MODEL.value not in MODEL_FILES:
    raise errors.Config.InvalidValue(
        f'Unsupported nixl_kvbench_model: {MODEL.value!r}'
    )
  if MODEL_CONFIG.value not in MODEL_CONFIG_FILES:
    raise errors.Config.InvalidValue(
        'Unsupported nixl_kvbench_model_config:'
        f' {MODEL_CONFIG.value!r}'
    )
  if NUM_ITER.value <= 0 or WARMUP_ITER.value < 0:
    raise errors.Config.InvalidValue(
        'nixl_num_iter must be positive and nixl_warmup_iter non-negative.'
    )
  if NUM_THREADS.value <= 0 or PROGRESS_THREADS.value < 0:
    raise errors.Config.InvalidValue(
        'nixl_num_threads must be positive and nixl_progress_threads'
        ' non-negative.'
    )
  for raw_token in ISL_TOKENS.value:
    try:
      tokens = int(raw_token)
    except ValueError as e:
      raise errors.Config.InvalidValue(
          f'Invalid input sequence length: {raw_token!r}'
      ) from e
    if tokens <= 0:
      raise errors.Config.InvalidValue(
          'nixl_kvbench_isl_tokens must contain positive values.'
      )
    if tokens > MAX_TOKENS.value:
      raise errors.Config.InvalidValue(
          f'Input sequence length {tokens} exceeds'
          f' nixl_kvbench_max_tokens ({MAX_TOKENS.value}).'
      )
  for op_type in OP_TYPES.value:
    if op_type.upper() not in ('READ', 'WRITE'):
      raise errors.Config.InvalidValue(
          f'Unsupported nixl_op_types value: {op_type!r}'
      )
  if TOTAL_BUFFER_SIZE.value.lower() != 'auto':
    ParseSize(TOTAL_BUFFER_SIZE.value)


def DataFile(name: str) -> str:
  """Returns a remote data path for a vendored NIXL/KVBench file."""
  return posixpath.join(REMOTE_DATA_DIR, posixpath.basename(name))


def _BuildKvBenchCommand(
    subcommand: str,
    model_path: str,
    model_config_path: str,
    isl_tokens: int,
) -> str:
  """Builds a minimal KVBench command."""
  args = [
      kvbench.KVBENCH_BIN,
      'main.py',
      subcommand,
      '--model',
      model_path,
      '--model_config',
      model_config_path,
      '--isl',
      str(isl_tokens),
      '--page_size',
      str(PAGE_SIZE.value),
      '--num_requests',
      str(NUM_REQUESTS.value),
      '--tp',
      str(TP.value),
      '--pp',
      str(PP.value),
  ]
  if subcommand == 'plan':
    args += [
        '--backend',
        'UCX',
        '--source',
        'memory',
        '--destination',
        'memory',
        '--initiator_seg_type',
        'DRAM',
        '--target_seg_type',
        'DRAM',
        '--format',
        'json',
    ]
  return f'cd {kvbench.KVBENCH_DIR} && {shlex.join(args)}'


def _LoadFirstJson(stdout: str) -> Any:
  """Loads the first JSON object or array from stdout."""
  try:
    return json.loads(stdout)
  except json.JSONDecodeError:
    pass
  starts = [i for i in (stdout.find('{'), stdout.find('[')) if i >= 0]
  if not starts:
    raise errors.Benchmarks.RunError(
        f'KVBench did not produce JSON output:\n{stdout}'
    )
  start = min(starts)
  decoder = json.JSONDecoder()
  try:
    value, _ = decoder.raw_decode(stdout[start:])
    return value
  except json.JSONDecodeError as e:
    raise errors.Benchmarks.RunError(
        f'Unable to parse KVBench JSON output:\n{stdout}'
    ) from e


def _FindValue(data: Any, keys: tuple[str, ...]) -> Any:
  """Recursively finds the first matching key in a JSON value."""
  if isinstance(data, dict):
    for key in keys:
      if key in data:
        return data[key]
    for value in data.values():
      found = _FindValue(value, keys)
      if found is not None:
        return found
  elif isinstance(data, list):
    for value in data:
      found = _FindValue(value, keys)
      if found is not None:
        return found
  return None


def _FindCommand(data: Any) -> str | None:
  """Finds a generated NIXLBench command in KVBench plan output."""
  if isinstance(data, str) and 'nixlbench' in data:
    return data
  if isinstance(data, dict):
    for key in ('command', 'nixlbench_command', 'cmd'):
      value = data.get(key)
      if isinstance(value, str) and 'nixlbench' in value:
        return value
    for value in data.values():
      found = _FindCommand(value)
      if found:
        return found
  elif isinstance(data, list):
    for value in data:
      found = _FindCommand(value)
      if found:
        return found
  return None


def _ParseCommandSize(command: str, flag: str) -> int | None:
  """Parses a size flag from a generated NIXLBench command."""
  match = re.search(rf'{re.escape(flag)}\s+(\S+)', command)
  if not match:
    return None
  return ParseSize(match.group(1))


def ParseKvBenchPlan(stdout: str) -> tuple[int, int, int, int]:
  """Parses block, batch, I/O, and buffer sizes from KVBench plan output."""
  data = _LoadFirstJson(stdout)
  block = _FindValue(
      data,
      ('block_size', 'block_size_bytes', 'max_block_size'),
  )
  batch = _FindValue(
      data,
      ('batch_size', 'batch_size_bytes', 'max_batch_size'),
  )
  io_size = _FindValue(data, ('io_size', 'io_size_bytes', 'total_io_size'))
  total_buffer_size = _FindValue(
      data, ('total_buffer_size', 'total_buffer_size_bytes')
  )
  command = _FindCommand(data)
  if isinstance(block, str):
    block = ParseSize(block)
  if isinstance(batch, str):
    batch = ParseSize(batch)
  if isinstance(io_size, str):
    io_size = ParseSize(io_size)
  if isinstance(total_buffer_size, str):
    total_buffer_size = ParseSize(total_buffer_size)
  if block is None and command:
    block = _ParseCommandSize(command, '--max_block_size')
  if batch is None and command:
    batch = _ParseCommandSize(command, '--max_batch_size')
  if block is None or batch is None:
    raise errors.Benchmarks.RunError(
        'KVBench plan output did not contain resolved block and batch sizes.'
        f'\nOutput:\n{stdout}'
    )
  block = int(block)
  batch = int(batch)
  io_size = int(io_size) if io_size is not None else block * batch
  total_buffer_size = (
      int(total_buffer_size)
      if total_buffer_size is not None
      else block * batch
  )
  if block <= 0 or batch <= 0 or io_size <= 0 or total_buffer_size <= 0:
    raise errors.Benchmarks.RunError(
        'KVBench plan produced non-positive resolved sizes.'
    )
  return block, batch, io_size, total_buffer_size


def GetKvBenchCases(
    worker_vm: Any,
    model_path: str,
    model_config_path: str,
) -> list[KvBenchCase]:
  """Resolves KVBench payloads for the configured token list."""
  cases = []
  for raw_token in ISL_TOKENS.value:
    isl_tokens = int(raw_token)
    label = f'isl{isl_tokens}'
    kvcache_cmd = _BuildKvBenchCommand(
        'kvcache', model_path, model_config_path, isl_tokens
    )
    kvcache_output, _ = worker_vm.RemoteCommand(kvcache_cmd, timeout=600)
    logging.info('KVBench kvcache output:\n%s', kvcache_output)
    plan_cmd = _BuildKvBenchCommand(
        'plan', model_path, model_config_path, isl_tokens
    )
    plan_output, _ = worker_vm.RemoteCommand(plan_cmd, timeout=600)
    block, batch, io_size, total_buffer_size = ParseKvBenchPlan(plan_output)
    cases.append(
        KvBenchCase(
            label=label,
            model=MODEL.value,
            model_config=MODEL_CONFIG.value,
            isl_tokens=isl_tokens,
            page_size=PAGE_SIZE.value,
            access_pattern=ACCESS_PATTERN.value,
            num_requests=NUM_REQUESTS.value,
            tp=TP.value,
            pp=PP.value,
            resolved_block_size_bytes=block,
            resolved_batch_size=batch,
            resolved_io_size_bytes=io_size,
            resolved_total_buffer_size_bytes=total_buffer_size,
        )
    )
  return cases


def _NixlEnvironment() -> str:
  """Returns the shell environment for NIXLBench."""
  return (
      f'{ucx.Environment()} {nixl.Environment()} '
      f'{nixlbench.Environment()} '
      'export UCX_TLS=tcp,self,sm; '
      'export NIXL_LOG_LEVEL=INFO; '
      f'export PATH={posixpath.dirname(nixlbench.NixlbenchBin())}:$PATH;'
  )


def TotalBufferSize(case: KvBenchCase) -> int:
  """Returns the configured or KVBench-resolved total buffer size.

  NIXLBench divides the total buffer evenly across worker threads, and each
  thread needs enough space for one full block/batch transfer. The minimum
  total buffer therefore scales with nixl_num_threads.
  """
  num_threads = NUM_THREADS.value
  required_buffer_size = case.resolved_total_buffer_size_bytes * num_threads
  if TOTAL_BUFFER_SIZE.value.lower() == 'auto':
    return required_buffer_size
  total_buffer_size = ParseSize(TOTAL_BUFFER_SIZE.value)
  if total_buffer_size < required_buffer_size:
    raise errors.Config.InvalidValue(
        'nixl_total_buffer_size must be at least '
        f'{required_buffer_size} bytes for {num_threads} threads.'
    )
  return total_buffer_size


def BuildNixlBenchCommand(
    case: KvBenchCase,
    op_type: str,
    asio_address: str,
) -> str:
  """Builds a NIXLBench ASIO command for one case."""
  total_buffer_size = TotalBufferSize(case)
  args = [
      nixlbench.NixlbenchBin(),
      '--runtime_type',
      'ASIO',
      '--asio_address',
      asio_address,
      '--asio_port',
      str(ASIO_PORT.value),
      '--backend',
      'UCX',
      '--scheme',
      'pairwise',
      '--initiator_seg_type',
      'DRAM',
      '--target_seg_type',
      'DRAM',
      '--op_type',
      op_type.upper(),
      '--start_block_size',
      str(case.resolved_block_size_bytes),
      '--max_block_size',
      str(case.resolved_block_size_bytes),
      '--start_batch_size',
      str(case.resolved_batch_size),
      '--max_batch_size',
      str(case.resolved_batch_size),
      '--total_buffer_size',
      str(total_buffer_size),
      '--num_iter',
      str(NUM_ITER.value),
      '--warmup_iter',
      str(WARMUP_ITER.value),
      '--num_threads',
      str(NUM_THREADS.value),
      '--progress_threads',
      str(PROGRESS_THREADS.value),
  ]
  if PROGRESS_THREADS.value > 0:
    args.append('--enable_pt')
  if CHECK_CONSISTENCY.value:
    args.append('--check_consistency')
  return shlex.join(args)


def _NormalizeHeader(value: str) -> str:
  """Normalizes a NIXLBench table header for lookup."""
  return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def ParseNixlBenchOutput(
    stdout: str,
    metadata: dict[str, Any],
) -> list[sample.Sample]:
  """Parses the NIXLBench results table into PKB samples."""
  lines = stdout.splitlines()
  header_index = None
  columns = []
  for index, line in enumerate(lines):
    if 'Block Size' in line and 'Batch Size' in line and 'B/W' in line:
      parts = re.split(r'\s{2,}', line.strip())
      if len(parts) < 3:
        parts = line.split()
      columns = [_NormalizeHeader(part) for part in parts]
      header_index = index
      break
  if header_index is None:
    return []

  rows = []
  for line in lines[header_index + 1 :]:
    stripped = line.strip()
    if not stripped or set(stripped) <= {'-', ' '}:
      continue
    parts = re.split(r'\s{2,}', stripped)
    if len(parts) != len(columns):
      parts = stripped.split()
    if len(parts) != len(columns):
      continue
    if not re.match(r'^\d', parts[0]):
      continue
    rows.append(parts)

  samples = []
  for parts in rows:
    row_metadata = dict(metadata)
    row_values: dict[str, float | int] = {}
    for column, value in zip(columns, parts):
      if column not in _NIXL_COLUMNS:
        continue
      sample_name, unit = _NIXL_COLUMNS[column]
      try:
        numeric_value: float | int = (
            int(value) if unit in ('bytes', 'count') else float(value)
        )
      except ValueError:
        continue
      row_values[sample_name] = numeric_value
      row_metadata[sample_name] = numeric_value
    if 'block_size_bytes' in row_values:
      row_metadata['nixl_block_size_bytes'] = row_values['block_size_bytes']
    if 'batch_size' in row_values:
      row_metadata['nixl_batch_size'] = row_values['batch_size']
    for sample_name, value in row_values.items():
      if sample_name in ('block_size_bytes', 'batch_size'):
        continue
      _, unit = next(
          (name, unit)
          for name, unit in _NIXL_COLUMNS.values()
          if name == sample_name
      )
      samples.append(sample.Sample(sample_name, value, unit, row_metadata))
  return samples


def RunAsioCase(
    target_vm: Any,
    initiator_vm: Any,
    case: KvBenchCase,
    op_type: str,
    metadata: dict[str, Any],
) -> list[sample.Sample]:
  """Runs one NIXLBench ASIO target/initiator pair."""
  artifact_dir = posixpath.join(
      ARTIFACT_ROOT.value,
      FLAGS.run_uri or 'run',
      f'{case.label}_{op_type.lower()}',
  )
  target_log = posixpath.join(artifact_dir, 'target.log')
  initiator_log = posixpath.join(artifact_dir, 'initiator.log')
  target_vm.RemoteCommand(f'mkdir -p {artifact_dir}')
  initiator_vm.RemoteCommand(f'mkdir -p {artifact_dir}')

  command = BuildNixlBenchCommand(
      case,
      op_type,
      target_vm.internal_ip,
  )
  environment = _NixlEnvironment()
  target_pid = None
  initiator_stdout = ''
  initiator_error = None
  try:
    stdout, _ = target_vm.RemoteCommand(
        f'{environment} nohup {command} > {target_log} 2>&1 & echo $!'
    )
    target_pid = stdout.strip().splitlines()[-1]
    time.sleep(STARTUP_DELAY.value)
    try:
      initiator_stdout, _ = initiator_vm.RemoteCommand(
          f'{environment} {command} > {initiator_log} 2>&1; '
          f'status=$?; cat {initiator_log}; exit $status',
          timeout=CASE_TIMEOUT,
      )
    except (
        errors.VirtualMachine.RemoteCommandError,
        errors.VmUtil.IssueCommandTimeoutError,
    ) as e:
      initiator_error = e
      initiator_stdout, _ = initiator_vm.RemoteCommand(
          f'cat {initiator_log}', ignore_failure=True
      )
  finally:
    if target_pid:
      target_vm.RemoteCommand(
          f'kill {shlex.quote(target_pid)} || true', ignore_failure=True
      )
      time.sleep(1)

  case_metadata = dict(metadata)
  case_metadata.update({
      'nixl_case_label': case.label,
      'nixl_case_source': 'kvbench',
      'nixl_transport': 'tcp',
      'nixl_coordination': 'asio',
      'nixl_scheme': 'pairwise',
      'nixl_op_type': op_type.upper(),
      'nixl_block_size_bytes': case.resolved_block_size_bytes,
      'nixl_batch_size': case.resolved_batch_size,
      'nixl_total_buffer_size_bytes': TotalBufferSize(case),
      'nixl_num_iter': NUM_ITER.value,
      'nixl_warmup_iter': WARMUP_ITER.value,
      'nixl_num_threads': NUM_THREADS.value,
      'nixl_progress_threads': PROGRESS_THREADS.value,
      'nixl_worker_count': WORKER_COUNT.value,
      'kvbench_model': case.model,
      'kvbench_model_config': case.model_config,
      'kvbench_isl_tokens': case.isl_tokens,
      'kvbench_page_size': case.page_size,
      'kvbench_access_pattern': case.access_pattern,
      'kvbench_num_requests': case.num_requests,
      'kvbench_tp': case.tp,
      'kvbench_pp': case.pp,
      'kvbench_resolved_block_size_bytes': case.resolved_block_size_bytes,
      'kvbench_resolved_batch_size': case.resolved_batch_size,
      'kvbench_resolved_io_size_bytes': case.resolved_io_size_bytes,
      'kvbench_resolved_total_buffer_size_bytes': (
          case.resolved_total_buffer_size_bytes
      ),
  })
  samples = ParseNixlBenchOutput(initiator_stdout, case_metadata)
  if samples:
    for item in samples:
      item.metadata['nixl_result_source'] = 'initiator'
    return samples

  target_output, _ = target_vm.RemoteCommand(
      f'cat {target_log}', ignore_failure=True
  )
  samples = ParseNixlBenchOutput(target_output, case_metadata)
  if samples:
    for item in samples:
      item.metadata['nixl_result_source'] = 'target'
    return samples

  error = errors.Benchmarks.RunError(
      'NIXLBench did not produce parseable results for'
      f' {case.label}/{op_type}.\n'
      f'Initiator output:\n{initiator_stdout}\n'
      f'Target output:\n{target_output}'
  )
  if initiator_error is not None:
    raise error from initiator_error
  raise error


def RunCases(
    target_vm: Any,
    initiator_vm: Any,
    cases: list[KvBenchCase],
    metadata: dict[str, Any],
) -> list[sample.Sample]:
  """Runs all configured NIXLBench cases and operation types."""
  samples = []
  for case in cases:
    for op_type in OP_TYPES.value:
      try:
        samples.extend(
            RunAsioCase(
                target_vm,
                initiator_vm,
                case,
                op_type.upper(),
                metadata,
            )
        )
      except errors.Benchmarks.RunError:
        if not CONTINUE_ON_ERROR.value:
          raise
        logging.exception(
            'NIXLBench case %s/%s failed; continuing.',
            case.label,
            op_type,
        )
  return samples
