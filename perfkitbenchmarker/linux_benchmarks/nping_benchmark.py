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
from perfkitbenchmarker import configs
from absl import flags
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util
from perfkitbenchmarker import flag_util
import re

flags.DEFINE_boolean('nping_also_run_using_external_ip', False,
                     'If set to True, the ping command will also be executed '
                     'using the external ips of the vms.')

flags.DEFINE_integer('nping_port', 40000,
                     'port to use for nping')

flag_util.DEFINE_integerlist('nping_count', flag_util.IntegerList([100]),
                         'Number of packets to send with ping',
                          module_name=__name__)

flags.DEFINE_boolean('nping_service_already_exits', False,
                     'Set to true if you want to probe a port with an existing service')

flags.DEFINE_boolean('nping_pre_run', False,
                     'Run a throwaway run before the measured run.')

flag_util.DEFINE_integerlist('nping_interval_time_ms',
                             flag_util.IntegerList([10]),
                             'time between nping probes in milliseconds',
                              module_name=__name__)


FLAGS = flags.FLAGS


BENCHMARK_NAME = 'nping'
BENCHMARK_CONFIG = """
nping:
  description: Benchmarks ping latency over internal IP addresses
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
"""

METRICS = ('Max Latency', 'Min Latency', 'Average Latency')


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):  # pylint: disable=unused-argument
  """Install ping on the target vm.
  Checks that there are exactly two vms specified.
  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  vms = benchmark_spec.vms
  if len(vms) != 2:
    raise ValueError(
        'nping benchmark requires exactly two machines, found {0}'
        .format(len(vms)))
  for vm in vms:
    vm.Install('nmap')
    vm.AllowPort(FLAGS.nping_port)

    if not FLAGS.nping_service_already_exits:
      vm.Install('iperf')
      stdout, _ = vm.RemoteCommand(f'nohup iperf --server --port {FLAGS.nping_port}'
                                     ' &> /dev/null & echo $!')
      vm.iperf_tcp_server_pid = stdout.strip()

      #TODO store this in a better place once we have a better place
      vm.iperf_server_pid = stdout.strip()


def Run(benchmark_spec):
  """Run ping on the target vm.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    A list of sample.Sample objects.
  """
  vms = benchmark_spec.vms
  results = []

  for nping_count in FLAGS.nping_count:
    for interval_time in FLAGS.nping_interval_time_ms:
      for sending_vm, receiving_vm in vms, reversed(vms):
        if vm_util.ShouldRunOnExternalIpAddress():
          ip_type = vm_util.IpAddressMetadata.EXTERNAL
          results = results + _RunNPing(sending_vm,
                                     receiving_vm,
                                     receiving_vm.ip_address,
                                     'external',
                                     nping_count,
                                     interval_time)

        if vm_util.ShouldRunOnInternalIpAddress(sending_vm, receiving_vm):
          ip_type = vm_util.IpAddressMetadata.INTERNAL
          results = results + _RunNPing(sending_vm,
                                     receiving_vm,
                                     receiving_vm.ip_address,
                                     ip_type,
                                     nping_count,
                                     interval_time)



  return results


def _RunNPing(sending_vm, receiving_vm, receiving_ip, ip_type, nping_count, interval_time):
  """Run ping using 'sending_vm' to connect to 'receiving_ip'.

  Args:
    sending_vm: The VM issuing the ping request.
    receiving_vm: The VM receiving the ping.  Needed for metadata.
    receiving_ip: The IP address to be pinged.
    ip_type: The type of 'receiving_ip' (either 'internal' or 'external')
  Returns:
    A list of samples, with one sample for each metric.
  """#
  # if not sending_vm.IsReachable(receiving_vm):
  #   logging.warn('%s is not reachable from %s', receiving_vm, sending_vm)
  #   return []
  if FLAGS.nping_pre_run:
    ping_cmd = f'nping --delay {interval_time}ms -c10 -p {FLAGS.nping_port} {receiving_ip}'
    sending_vm.RemoteCommand(ping_cmd, should_log=True)

  logging.info('nping results (ip_type = %s):', ip_type)
  ping_cmd = f'nping --delay {interval_time}ms -c {nping_count} -p {FLAGS.nping_port} {receiving_ip}'

  stdout, _ = sending_vm.RemoteCommand(ping_cmd, should_log=True)
  stats = re.findall('([0-9]*\\.[0-9]*)', stdout.splitlines()[-3])
  assert len(stats) == len(METRICS), stats
  results = []
  metadata = {'ip_type': ip_type,
              'receiving_zone': receiving_vm.zone,
              'sending_zone': sending_vm.zone,
              'interval_time_ms': interval_time,
              'transaction_count': nping_count}
  for i, metric in enumerate(METRICS):
    results.append(sample.Sample(metric, float(stats[i]), 'ms', metadata))
  return results


def Cleanup(benchmark_spec):
  """Cleanup ping on the target vm (by uninstalling).

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  if not FLAGS.nping_service_already_exits:
    for vm in benchmark_spec.vms:
      vm.RemoteCommand(
        f'kill -9 {vm.iperf_tcp_server_pid}', ignore_failure=True)
