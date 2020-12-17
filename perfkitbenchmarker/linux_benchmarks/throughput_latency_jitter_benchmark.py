# Copyright 2014 PerfKitBenchmarker Authors. All rights reserved.
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

"""Runs ping.

This benchmark runs ping using the internal, and optionally external, ips of
vms in the same zone.
"""

import logging
from perfkitbenchmarker import vm_util
from perfkitbenchmarker import configs
from absl import flags
from perfkitbenchmarker import sample
from perfkitbenchmarker.linux_benchmarks import netperf_benchmark
from perfkitbenchmarker.linux_benchmarks import iperf_benchmark
from perfkitbenchmarker.linux_benchmarks import ping_benchmark
from perfkitbenchmarker.linux_benchmarks import nping_benchmark
import re


# flags.DEFINE_boolean('ping_also_run_using_external_ip', False,
#                      'If set to True, the ping command will also be executed '
#                      'using the external ips of the vms.')


#TODO change these flags to be specific to this benchmark
#also uses flags
#FLAGS.iperf_sending_thread_count
#FLAGS.iperf_runtime_in_seconds

FLAGS = flags.FLAGS


BENCHMARK_NAME = 'throughput_latency_jitter'
BENCHMARK_CONFIG = """
throughput_latency_jitter:
  description: Run iperf
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
"""

METRICS = ('Min Latency', 'Average Latency', 'Max Latency', 'Latency Std Dev')

flags.DEFINE_boolean('use_nping', False,
                     'If set to True, nping will be used instead of standard ping')

flags.DEFINE_boolean('do_ping', True,
                     'If set to True, does ping or nping if that flag is set')

flags.DEFINE_boolean('skip_prepare', False,
                     'If true, skips prepare phase. Assumes it is already prepared')

def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):  # pylint: disable=unused-argument
  """Install ping on the target vm.
  Checks that there are exactly two vms specified.
  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  if FLAGS.do_ping:
    if FLAGS.use_nping:
      nping_benchmark.Prepare(benchmark_spec)
    else:
      ping_benchmark.Prepare(benchmark_spec)

  netperf_benchmark.Prepare(benchmark_spec)
  iperf_benchmark.Prepare(benchmark_spec)

  vms = benchmark_spec.vms
  for vm in vms:
    vm.AllowPort(10,60000)

def Run(benchmark_spec):
  """Run ping on the target vm.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    A list of sample.Sample objects.
  """

  # name = raw_input("continue? ")

  vms = benchmark_spec.vms

  results = []
  
  ping_results = []

  if FLAGS.do_ping:
    if FLAGS.use_nping:
      ping_results = nping_benchmark.Run(benchmark_spec)

      for sample in ping_results:
        print("SAMPLE")
        print(type(sample))
        print(sample)
        sample.metadata['benchmark_name'] = 'nping'
        # if FLAGS.gcp_network_tier:
        #   sample.metadata['network_tier'] = FLAGS.gcp_network_tier

    else:
      ping_results = ping_benchmark.Run(benchmark_spec)

      for sample in ping_results:
        print("SAMPLE")
        print(type(sample))
        print(sample)
        sample.metadata['benchmark_name'] = 'ping'
        # if FLAGS.gcp_network_tier:
        #   sample.metadata['network_tier'] = FLAGS.gcp_network_tier

  iperf_results = iperf_benchmark.Run(benchmark_spec)
  for sample in iperf_results:
    print("SAMPLE")
    print(type(sample))
    print(sample)
    sample.metadata['benchmark_name'] = 'iperf'
    # if FLAGS.gcp_network_tier:
    #   sample.metadata['network_tier'] = FLAGS.gcp_network_tier

  #iperf_benchmark.Cleanup(benchmark_spec)

  #ping_results = ping_benchmark.Run(benchmark_spec)
  #iperf_results = iperf_benchmark.Run(benchmark_spec)
  netperf_results = netperf_benchmark.Run(benchmark_spec)
  print("NETPERF RESULTS")
  print(type(netperf_results))
  for sample in netperf_results:
    print("SAMPLE")
    print(type(sample))
    print(sample)
    sample.metadata['benchmark_name'] = 'netperf'
    # if FLAGS.gcp_network_tier:
    #   sample.metadata['network_tier'] = FLAGS.gcp_network_tier

  results = results + ping_results + iperf_results + netperf_results

  return results


def _PrimingRunIperf(sending_vm, receiving_vm, receiving_ip_address, ip_type):
  iperf_cmd = ('iperf --client %s --port %s --format m --time %s -P %s' %
               (receiving_ip_address, IPERF_PORT,
                FLAGS.iperf_runtime_in_seconds,
                FLAGS.iperf_sending_thread_count))
  # the additional time on top of the iperf runtime is to account for the
  # time it takes for the iperf process to start and exit
  timeout_buffer = FLAGS.iperf_timeout or 30 + FLAGS.iperf_sending_thread_count
  stdout, _ = sending_vm.RemoteCommand(iperf_cmd, should_log=True,
                                       timeout=FLAGS.iperf_runtime_in_seconds +
                                       timeout_buffer)  


def Cleanup(benchmark_spec):  # pylint: disable=unused-argument
  """Cleanup ping on the target vm (by uninstalling).

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  if FLAGS.use_nping:
    nping_benchmark.Cleanup(benchmark_spec)
  else:
    ping_benchmark.Cleanup(benchmark_spec)
  iperf_benchmark.Cleanup(benchmark_spec)
  netperf_benchmark.Cleanup(benchmark_spec)
