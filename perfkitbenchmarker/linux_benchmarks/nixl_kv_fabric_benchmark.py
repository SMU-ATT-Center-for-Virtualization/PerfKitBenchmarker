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
"""Benchmarks NIXL DRAM-to-DRAM KV transfer over UCX/TCP with ASIO.

The benchmark uses KVBench only to resolve realistic Llama 3.1 KV payload
sizes. NIXLBench performs the actual transfers with ASIO coordination.
"""

import logging
from typing import Any

from perfkitbenchmarker import background_tasks
from perfkitbenchmarker import benchmark_spec as bm_spec
from perfkitbenchmarker import configs
from perfkitbenchmarker import errors
from perfkitbenchmarker import sample
from perfkitbenchmarker.linux_benchmarks import nixl_kv_fabric_common
from perfkitbenchmarker.linux_packages import nixl
from perfkitbenchmarker.linux_packages import nixlbench
from perfkitbenchmarker.linux_packages import ucx

BENCHMARK_NAME = 'nixl_kv_fabric'
BENCHMARK_CONFIG = """
nixl_kv_fabric:
  description: >
    Measures NIXL DRAM-to-DRAM KV transfer over UCX/TCP using NIXLBench,
    with ASIO coordination and KVBench-derived Llama 3.1 payload sizes.
  vm_groups:
    workers:
      os_type: ubuntu2404
      vm_spec:
        GCP:
          machine_type: c3-highcpu-22
          zone: us-central1-b
          image: null
          boot_disk_size: 100
      vm_count: 2
"""


def GetConfig(user_config: dict[Any, Any]) -> dict[Any, Any]:
  """Loads the benchmark config and applies the worker count."""
  config = configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)
  config['vm_groups']['workers']['vm_count'] = (
      nixl_kv_fabric_common.WORKER_COUNT.value
  )
  return config


def CheckPrerequisites(benchmark_config) -> None:
  nixl_kv_fabric_common.CheckPrerequisites(benchmark_config)


def _GetWorkers(benchmark_spec: bm_spec.BenchmarkSpec) -> list[Any]:
  """Returns the NIXL worker VMs."""
  groups = benchmark_spec.vm_groups
  if not groups or 'workers' not in groups:
    raise errors.Benchmarks.PrepareException(
        'nixl_kv_fabric requires a workers VM group.'
    )
  return groups['workers']


def _PrepareWorker(vm: Any) -> None:
  """Installs and verifies NIXL, NIXLBench, and KVBench."""
  vm.Install('nixlbench')
  vm.Install('kvbench')
  vm.RemoteCommand(f'mkdir -p {nixl_kv_fabric_common.REMOTE_DATA_DIR}')
  vm.PushDataFile(
      nixl_kv_fabric_common.MODEL_FILES[
          nixl_kv_fabric_common.MODEL.value
      ],
      remote_path=nixl_kv_fabric_common.REMOTE_DATA_DIR + '/',
  )
  vm.PushDataFile(
      nixl_kv_fabric_common.MODEL_CONFIG_FILES[
          nixl_kv_fabric_common.MODEL_CONFIG.value
      ],
      remote_path=nixl_kv_fabric_common.REMOTE_DATA_DIR + '/',
  )
  vm.RemoteCommand(f'test -x {nixlbench.NixlbenchBin()}')
  vm.RemoteCommand(
      'test -f '
      f'{nixl_kv_fabric_common.DataFile(nixl_kv_fabric_common.MODEL_FILES[
          nixl_kv_fabric_common.MODEL.value
      ])}'
  )
  vm.RemoteCommand(
      'test -f '
      f'{nixl_kv_fabric_common.DataFile(
          nixl_kv_fabric_common.MODEL_CONFIG_FILES[
              nixl_kv_fabric_common.MODEL_CONFIG.value
          ])}'
  )


def Prepare(benchmark_spec: bm_spec.BenchmarkSpec) -> None:
  """Prepares both NIXL workers."""
  workers = _GetWorkers(benchmark_spec)
  if len(workers) != 2:
    raise errors.Benchmarks.PrepareException(
        f'nixl_kv_fabric requires exactly 2 workers, found {len(workers)}.'
    )
  # The firewall is network-scoped in GCE. Create one rule covering both
  # worker IPs instead of racing two concurrent AllowPort calls that would
  # produce the same firewall rule name.
  workers[0].AllowPort(
      nixl_kv_fabric_common.ASIO_PORT.value,
      source_range=[vm.internal_ip + '/32' for vm in workers],
  )
  background_tasks.RunThreaded(_PrepareWorker, workers)


def GetCommonMetadata(workers: list[Any]) -> dict[str, Any]:
  """Returns metadata common to every sample."""
  return {
      'worker_machine_type': getattr(workers[0], 'machine_type', ''),
      'nixl_version': nixl.NIXL_VERSION.value,
      'ucx_version': ucx.UCX_VERSION.value,
      'nixl_worker_count': nixl_kv_fabric_common.WORKER_COUNT.value,
      'nixl_worker_internal_ips': ','.join(vm.internal_ip for vm in workers),
  }


def Run(benchmark_spec: bm_spec.BenchmarkSpec) -> list[sample.Sample]:
  """Runs KVBench-derived NIXLBench cases over ASIO."""
  workers = _GetWorkers(benchmark_spec)
  if len(workers) != 2:
    raise errors.Benchmarks.RunError(
        f'nixl_kv_fabric requires exactly 2 workers, found {len(workers)}.'
    )
  model_path = nixl_kv_fabric_common.DataFile(
      nixl_kv_fabric_common.MODEL_FILES[nixl_kv_fabric_common.MODEL.value]
  )
  config_path = nixl_kv_fabric_common.DataFile(
      nixl_kv_fabric_common.MODEL_CONFIG_FILES[
          nixl_kv_fabric_common.MODEL_CONFIG.value
      ]
  )
  cases = nixl_kv_fabric_common.GetKvBenchCases(
      workers[0], model_path, config_path
  )
  samples = nixl_kv_fabric_common.RunCases(
      workers[0],
      workers[1],
      cases,
      GetCommonMetadata(workers),
  )
  if not samples:
    raise errors.Benchmarks.RunError('No NIXLBench samples were collected.')
  return samples


def Cleanup(benchmark_spec: bm_spec.BenchmarkSpec) -> None:
  """Stops NIXLBench and removes temporary artifacts."""
  try:
    workers = _GetWorkers(benchmark_spec)
  except errors.Benchmarks.PrepareException:
    logging.exception('Unable to resolve VMs during cleanup.')
    return
  for vm in workers:
    vm.RemoteCommand('pkill -x nixlbench || true', ignore_failure=True)
    vm.RemoteCommand(
        f'rm -rf {nixl_kv_fabric_common.ARTIFACT_ROOT.value} '
        f'{nixl_kv_fabric_common.REMOTE_DATA_DIR}',
        ignore_failure=True,
    )
