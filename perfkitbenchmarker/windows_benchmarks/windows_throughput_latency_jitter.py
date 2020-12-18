# Copyright 2018 PerfKitBenchmarker Authors. All rights reserved.
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

"""Run iperf3 between two VMs."""

from perfkitbenchmarker import configs
from absl import flags
from perfkitbenchmarker import vm_util

from perfkitbenchmarker.windows_benchmarks import iperf3_benchmark
from perfkitbenchmarker.windows_benchmarks import ntttcp_benchmark
from perfkitbenchmarker.windows_benchmarks import psping_benchmark

FLAGS = flags.FLAGS

BENCHMARK_NAME = 'windows_throughput_latency_jitter'
BENCHMARK_CONFIG = """
windows_throughput_latency_jitter:
  description: Run ntttcp between two VMs.
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
"""


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):
  psping_benchmark.Prepare(benchmark_spec)
  iperf3_benchmark.Prepare(benchmark_spec)
  ntttcp_benchmark.Prepare(benchmark_spec)


def Run(benchmark_spec):
  """Measure UDP bandwidth between two VMs.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    A list of sample.Sample objects with the benchmark results.
  """

  vms = benchmark_spec.vms
  results = []

  psping_results = []
  ntttcp_results = []
  iperf3_results = []


  psping_results = psping_benchmark.Run(benchmark_spec)
  for sample in psping_results:
    print("SAMPLE")
    print(type(sample))
    print(sample)
    sample.metadata['benchmark_name'] = 'psping'

  ntttcp_results = ntttcp_benchmark.Run(benchmark_spec)
  for sample in ntttcp_results:
    print("SAMPLE NTTTCP")
    print(type(sample))
    print(sample)
    sample.metadata['benchmark_name'] = 'ntttcp'

  iperf3_results = iperf3_benchmark.Run(benchmark_spec)
  for sample in iperf3_results:
    print("SAMPLE")
    print(type(sample))
    print(sample)
    sample.metadata['benchmark_name'] = 'iperf3'


  results = results + psping_results + ntttcp_results + iperf3_results

  return results


def Cleanup(unused_benchmark_spec):
  pass

