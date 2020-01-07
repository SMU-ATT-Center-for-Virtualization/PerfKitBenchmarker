# Copyright 2019 PerfKitBenchmarker Authors. All rights reserved.
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

"""Runs plain netperf aggregate script runemomniaggdemo.sh to test packets
per second

docs:
https://hewlettpackard.github.io/netperf/doc/netperf.html
manpage: http://manpages.ubuntu.com/manpages/maverick/man1/netperf.1.html

Runs UDP_RR in script between one source machine and two target machines
to test packets per second

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import logging
import os
import re
from perfkitbenchmarker import configs
from perfkitbenchmarker import data
from perfkitbenchmarker import flags
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_packages import netperf

FLAGS = flags.FLAGS

BENCHMARK_NAME = 'netperf_aggregate'
BENCHMARK_CONFIG = """
netperf_aggregate:
  description: test packets per second performance
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
    vm_3:
      vm_spec: *default_single_core
"""

TRANSACTIONS_PER_SECOND = 'transactions_per_second'

# Command ports are even (id*2), data ports are odd (id*2 + 1)
PORT_START = 12865

REMOTE_SCRIPTS_DIR = 'netperf_test_scripts'
REMOTE_SCRIPT = 'runemomniaggdemo.sh'


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def PrepareNetperfAggregate(vm):
  """Installs netperf on a single vm."""

# INSTALL
# Ensure gcc, make, automake, texinfo and python-rrdtool are installed
# Bring a netperf source tree to the systems
# cd to the top of the tree
# run ./autogen.sh
# ./configure --enable-burst --enable-demo --enable-histogram
# make  # sudo make install if you prefer
  vm.Install('texinfo')
  vm.Install('python_rrdtool')
  vm.Install('netperf')

  if vm.IS_REBOOTABLE:
    vm.ApplySysctlPersistent({
        'net.ipv4.tcp_keepalive_time': 60,
        'net.ipv4.tcp_keepalive_intvl': 60,
    })

  PORT_END = PORT_START

  if vm_util.ShouldRunOnExternalIpAddress():
    vm.AllowPort(PORT_START, PORT_END)

  netserver_cmd = ('{netserver_path} -p {port_start}').format(
      port_start=PORT_START,
      netserver_path=netperf.NETSERVER_PATH)
  vm.RemoteCommand(netserver_cmd)

  # SETUP
  # push runemomniaggdemo.sh to server
  # On the system to be the one under test,
  # cd to  doc/examples/ in the netperf source tree
  # Ensure that runemomniaggdemo.sh and
  # find_max_burst.sh have the execute bit set
  # chmod +x runemomniaggdemo.sh find_max_burst.sh
  path = data.ResourcePath(os.path.join(REMOTE_SCRIPTS_DIR, REMOTE_SCRIPT))
  remote_path = netperf.NETPERF_EXAMPLE_DIR + REMOTE_SCRIPT
  logging.info('Uploading %s to %s', path, vm)
  vm.PushFile(source_path=path, remote_path=remote_path)
  vm.RemoteCommand('chmod +x %s' % (remote_path))


def Prepare(benchmark_spec):
  """Install netperf on the target vm.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  vms = benchmark_spec.vms
  vms = vms[:3]
  vm_util.RunThreaded(PrepareNetperfAggregate, vms)


# FIXME: change this
def _SetupHostFirewall(benchmark_spec):
  """Set up host firewall to allow incoming traffic.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  client_vm = benchmark_spec.vms[0]
  server_vm = benchmark_spec.vms[1]

  ip_addrs = [client_vm.internal_ip]
  if vm_util.ShouldRunOnExternalIpAddress():
    ip_addrs.append(client_vm.ip_address)

  logging.info('setting up host firewall on %s running %s for client at %s',
               server_vm.name, server_vm.image, ip_addrs)
  cmd = 'sudo iptables -A INPUT -p %s -s %s -j ACCEPT'
  for protocol in 'tcp', 'udp':
    for ip_addr in ip_addrs:
      server_vm.RemoteHostCommand(cmd % (protocol, ip_addr))


def ParseNetperfAggregateOutput(stdout, metadata):
  """Parses the stdout of a single netperf process.

  Args:
    stdout: the stdout of the netperf process
    metadata: metadata for any sample.Sample objects we create

  Returns:
    A tuple containing (throughput_sample, latency_samples, latency_histogram)
  """
  # Don't modify the metadata dict that was passed in

  logging.info("Parsing netperf aggregate output")
  metadata = metadata.copy()
  aggregate_samples = []

  stdout_ascii = stdout.encode("ascii")

  for line in stdout_ascii.splitlines():
    print(line)
    match = re.search('peak interval', line)
    if match:
      line_split = line.split()
      print(line_split)
      metric = line_split[0] + ' ' + line_split[6]
      value = float(line_split[5])
      unit = line_split[6]
      print(metric)
      print(value)
      aggregate_samples.append(sample.Sample(
          metric, value, unit, metadata))

  return aggregate_samples


def RunNetperfAggregate(vm, server1_ip, server2_ip):
  """Spawns netperf on a remote VM, parses results.

  Args:
    vm: The VM that the netperf TCP_RR benchmark will be run upon.
    benchmark_name: The netperf benchmark to run, see the documentation.
    server_ip: A machine that is running netserver.
    num_streams: The number of netperf client threads to run.

  Returns:
    A sample.Sample object with the result.
  """

  # setup remote hosts file
  vm.RemoteCommand("cd %s && echo 'REMOTE_HOSTS[0]=%s' > remote_hosts && "
                   "echo 'REMOTE_HOSTS[1]=%s' >> remote_hosts && "
                   "echo 'NUM_REMOTE_HOSTS=2' >> remote_hosts"
                   % (netperf.NETPERF_EXAMPLE_DIR, server1_ip, server2_ip))

  vm.RemoteCommand('cd %s && export PATH=$PATH:.'
                   % (netperf.NETPERF_EXAMPLE_DIR))

  stdout, stderr = vm.RemoteCommand("cd %s && export PATH=$PATH:. && chmod "
                                    "+x runemomniaggdemo.sh && "
                                    "./runemomniaggdemo.sh"
                                    % (netperf.NETPERF_EXAMPLE_DIR),
                                    ignore_failure=True, should_log=True,
                                    login_shell=False, timeout=1200)

  # print out netperf_tps.log
  stdout_1, stderr_1 = vm.RemoteCommand("cd %s && cat netperf_tps.log" %
                                        (netperf.NETPERF_EXAMPLE_DIR),
                                        ignore_failure=True, should_log=True,
                                        login_shell=False, timeout=1200)

  logging.info(stdout_1)
  logging.info(stderr_1)

  # do post processing step
  proc_stdout, proc_stderr = vm.RemoteCommand("cd %s && ./post_proc.py"
                                              "--intervals netperf_tps.log"
                                              % (netperf.NETPERF_EXAMPLE_DIR),
                                              ignore_failure=True)

  # Metadata to attach to samples
  metadata = {'number_of_hosts': 3}

  samples = ParseNetperfAggregateOutput(proc_stdout, metadata)

  return samples


def Run(benchmark_spec):
  """Run netperf TCP_RR on the target vm.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    A list of sample.Sample objects.
  """

  vms = benchmark_spec.vms

  results = []

  # run once with each VM as the client VM and others as the server
  for i in range(0, len(vms)):

    # set client and server vms
    client_vm = vms[i]
    server_vms = []
    for x in range(0, len(vms)):
      if x != i:
        server_vms.append(vms[x])

    logging.info('netperf running on %s', client_vm)

    metadata = {
        'sending_zone': client_vm.zone,
        'sending_machine_type': client_vm.machine_type,
        'receiving_zone_1': server_vms[0].zone,
        'receiving_machine_type_1': server_vms[0].machine_type,
        'receiving_zone_2': server_vms[1].zone,
        'receiving_machine_type_2': server_vms[1].machine_type
    }

    if vm_util.ShouldRunOnExternalIpAddress():
      external_ip_results = RunNetperfAggregate(client_vm,
                                                server_vms[0].ip_address,
                                                server_vms[1].ip_address)
      for external_ip_result in external_ip_results:
        external_ip_result.metadata['ip_type'] = 'external'
        external_ip_result.metadata.update(metadata)
      results.extend(external_ip_results)

    if (vm_util.ShouldRunOnInternalIpAddress(client_vm, server_vms[0]) and
        vm_util.ShouldRunOnInternalIpAddress(client_vm, server_vms[1])):

      internal_ip_results = RunNetperfAggregate(client_vm,
                                                server_vms[0].internal_ip,
                                                server_vms[1].internal_ip)

      for internal_ip_result in internal_ip_results:
        internal_ip_result.metadata.update(metadata)
        internal_ip_result.metadata['ip_type'] = 'internal'
      results.extend(internal_ip_results)

  return results


def Cleanup(benchmark_spec):
  """Cleanup netperf on the target vm (by uninstalling).

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  vms = benchmark_spec.vms
  vms[1].RemoteCommand('sudo killall netserver')
  vms[0].RemoteCommand('sudo rm -rf %s' % REMOTE_SCRIPT)
