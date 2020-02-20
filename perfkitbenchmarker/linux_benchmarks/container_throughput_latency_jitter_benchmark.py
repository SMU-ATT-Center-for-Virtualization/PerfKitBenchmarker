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

"""Runs netperf between containers.

docs:
http://www.netperf.org/svn/netperf2/tags/netperf-2.4.5/doc/netperf.html#TCP_005fRR
manpage: http://manpages.ubuntu.com/manpages/maverick/man1/netperf.1.html

Runs TCP_STREAM benchmark from netperf between two containers.
"""

from perfkitbenchmarker import configs
from perfkitbenchmarker import flags
from perfkitbenchmarker.linux_benchmarks import netperf_benchmark

FLAGS = flags.FLAGS

BENCHMARK_NAME = 'container_throughput_latency_jitter'
BENCHMARK_CONFIG = """
container_throughput_latency_jitter:
  description: Run netperf between containers.
  container_specs:
    throughput_latency_jitter:
      image: throughput_latency_jitter
      cpus: 2
      memory: 4GiB
  container_registry: {}
  container_cluster:
    vm_count: 2
    vm_spec:
      AWS:
        zone: us-east-1a
        machine_type: c5.xlarge
      Azure:
        zone: westus
        machine_type: Standard_D3_v2
      GCP:
        machine_type: n1-standard-4
        zone: us-west1-a

"""


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):
  """Start the netserver container.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  cluster = benchmark_spec.container_cluster
  cluster.DeployContainer('throughput-latency-jitter', benchmark_spec.container_specs['throughput_latency_jitter'])


def Run(benchmark_spec):
  """Run netperf TCP_STREAM between containers.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    A list of sample.Sample objects.
  """
  samples = []
  cluster = benchmark_spec.container_cluster
  print(cluster.containers)
  container_0 = cluster.containers['throughput-latency-jitter'][0]
  spec = benchmark_spec.container_specs['throughput_latency_jitter']
  print("SPECS")
  print(benchmark_spec.container_specs)
  spec.command = ['netperf',
                  '-t', 'TCP_STREAM',
                  '-H', container_0.ip_address,
                  '-l', '100',
                  '--',
                  '-o', netperf_benchmark.OUTPUT_SELECTOR]
  cluster.DeployContainer('throughput-latency-jitter', benchmark_spec.container_specs['throughput_latency_jitter'])
  container_1 = cluster.containers['throughput_latency_jitter'][1]
  container_1.WaitForExit()
  throughput_sample, _, _ = netperf_benchmark.ParseNetperfOutput(
      container_1.GetLogs(), {}, 'TCP_STREAM', False)
  samples.append(throughput_sample)

  spec.command = ['iperf', '-c', container_0.ip_address]
  container_2 = cluster.DeployContainer('throughput-latency-jitter', spec)
  container_2.WaitForExit()
  print(container_2.GetLogs())

  return samples


def Cleanup(unused_benchmark_spec):
  """Cleanup netperf.

  Args:
    unused_benchmark_spec: The benchmark specification. Contains all data that
        is required to run the benchmark.
  """
  pass
