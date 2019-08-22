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

"""Runs plain Iperf.

Docs:
http://iperf.fr/

Runs Iperf to collect network throughput.
"""


#TODO change from iperf to iperf 3
#TODO do something like this instead iperf3 -c 10.128.0.4 -t 15 -V -l 100 -M 1500 -b 1000000000000

import logging
import re
import os

from perfkitbenchmarker import configs
from perfkitbenchmarker import flags
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util
from perfkitbenchmarker import data

flags.DEFINE_integer('pps_sending_thread_count', 1,
                     'Number of connections to make to the '
                     'server for sending traffic.',
                     lower_bound=1)
# flags.DEFINE_integer('iperf_runtime_in_seconds', 60,
#                      'Number of seconds to run iperf.',
#                      lower_bound=1)
# flags.DEFINE_integer('iperf_timeout', None,
#                      'Number of seconds to wait in '
#                      'addition to iperf runtime before '
#                      'killing iperf client command.',
#                      lower_bound=1)

flags.DEFINE_string('pps_target_bandwidth', '1G',
                    'target pps Bandwidth')

flags.DEFINE_integer('pps_packet_size', 10,
                     'packet size in bytes')

flags.DEFINE_integer('pps_mss_size', 1500,
                     'iperf TCP maximum segment size in bytes. This is '
                     'usually the MTU - 40 bytes for the TCP/IP Header.'
                     'It is set to 1500 by default')

FLAGS = flags.FLAGS

REMOTE_SCRIPTS_DIR = 'pps_scripts'
REMOTE_SCRIPT = 'packetcount'

BENCHMARK_NAME = 'pps'
BENCHMARK_CONFIG = """
pps:
  description: Run iperf to find packets per second
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
"""

IPERF_PORT = 20000
IPERF_RETRIES = 5


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):
  """Install iperf and start the server on all machines.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  vms = benchmark_spec.vms
  if len(vms) != 2:
    raise ValueError(
        'pps benchmark requires exactly two machines, found {0}'.format(len(
            vms)))

  for vm in vms:
    vm.Install('iperf')
    if vm_util.ShouldRunOnExternalIpAddress():
      vm.AllowPort(IPERF_PORT)
    stdout, _ = vm.RemoteCommand(('nohup iperf --server -u --port %s &> /dev/null'
                                  '& echo $!') % IPERF_PORT)
    # TODO store this in a better place once we have a better place
    vm.iperf_server_pid = stdout.strip()


  # path = data.ResourcePath(os.path.join(REMOTE_SCRIPTS_DIR, REMOTE_SCRIPT))
  # logging.info('Uploading %s to %s', path, vms[0])
  # vms[0].PushFile(path, REMOTE_SCRIPT)
  # vms[0].RemoteCommand('sudo chmod 777 %s' % REMOTE_SCRIPT)

  # vms[1].PushFile(path, REMOTE_SCRIPT)
  # vms[1].RemoteCommand('sudo chmod 777 %s' % REMOTE_SCRIPT)

@vm_util.Retry(max_retries=IPERF_RETRIES)
def _RunIperf(sending_vm, receiving_vm, receiving_ip_address, ip_type):
  """Run iperf using sending 'vm' to connect to 'ip_address'.

  Args:
    sending_vm: The VM sending traffic.
    receiving_vm: The VM receiving traffic.
    receiving_ip_address: The IP address of the iperf server (ie the receiver).
    ip_type: The IP type of 'ip_address' (e.g. 'internal', 'external')
  Returns:
    A Sample.
  """
  #iperf3 -c 10.128.0.4 -t 20 -V -u  -b 0 -l 100 -M 89
  iperf_cmd = ('iperf -c %s --port %s -u -b %s -t 60 -e -M %s -P %s -l %s ' %
               (receiving_ip_address,
                IPERF_PORT,
                FLAGS.pps_target_bandwidth,
                FLAGS.pps_mss_size, 
                FLAGS.pps_sending_thread_count,
                FLAGS.pps_packet_size))

  #TODO change back to iperf2
  #iperf -c <hostname> -u -b 1G -l 2K --isochronous --ipg 1

  # iperf_cmd = ('iperf --client %s --port %s --format m --time %s -P %s' %
  #              (receiving_ip_address, IPERF_PORT,
  #               FLAGS.iperf_runtime_in_seconds,
  #               FLAGS.iperf_sending_thread_count))

  # if FLAGS.iperf_tcp_window:
  #   iperf_cmd = ('iperf --client %s --port %s --format m --time %s -P %s -w %s' %
  #              (receiving_ip_address, IPERF_PORT,
  #               FLAGS.iperf_runtime_in_seconds,
  #               FLAGS.iperf_sending_thread_count,
  #               FLAGS.iperf_tcp_window))
  # the additional time on top of the iperf runtime is to account for the
  # time it takes for the iperf process to start and exit
  timeout_buffer = FLAGS.iperf_timeout or 30 + FLAGS.pps_sending_thread_count
  std_out, _ = sending_vm.RemoteCommand(iperf_cmd, should_log=True,
                                       timeout= 60 +
                                       timeout_buffer)

  print(std_out)

  # Example output from iperf that needs to be parsed
# [  4]  10.00-11.00  sec  35.9 MBytes   301 Mbits/sec  375970  
# [  4]  11.00-12.00  sec  34.0 MBytes   286 Mbits/sec  356960  
# [  4]  12.00-13.00  sec  31.2 MBytes   262 Mbits/sec  326910  
# [  4]  13.00-14.00  sec  32.1 MBytes   269 Mbits/sec  336660  
# [  4]  14.00-15.00  sec  35.6 MBytes   299 Mbits/sec  373540  
# [  4]  15.00-16.00  sec  30.2 MBytes   253 Mbits/sec  316500  
# [  4]  16.00-17.00  sec  32.0 MBytes   268 Mbits/sec  335550  
# [  4]  17.00-18.00  sec  35.6 MBytes   298 Mbits/sec  373100  
# [  4]  18.00-19.00  sec  35.8 MBytes   300 Mbits/sec  375030  
# [  4]  19.00-20.00  sec  34.2 MBytes   287 Mbits/sec  358610  
# - - - - - - - - - - - - - - - - - - - - - - - - -
# Test Complete. Summary Results:
# [ ID] Interval           Transfer     Bandwidth       Jitter    Lost/Total Datagrams
# [  4]   0.00-20.00  sec   669 MBytes   280 Mbits/sec  0.001 ms  1922613/7009880 (27%)  
# [  4] Sent 7009880 datagrams
# CPU Utilization: local/sender 98.9% (7.8%u/91.0%s), remote/receiver 73.8% (8.8%u/65.1%s)

# ------------------------------------------------------------
# Client connecting to 172.17.0.7, UDP port 20000 with pid 4001
# Sending 16 byte datagrams, IPG target: 0.12 us (kalman adjust)
# UDP buffer size:  208 KByte (default)
# ------------------------------------------------------------
# [  3] local 172.17.0.6 port 46392 connected with 172.17.0.7 port 20000
# [ ID] Interval        Transfer     Bandwidth      Write/Err  PPS
# [  3] 0.00-60.00 sec   169 MBytes  23.6 Mbits/sec  11042999/0   184049 pps
# [  3] Sent 11042999 datagrams
# [  3] Server Report:
# [  3]  0.0-120.1 sec   168 MBytes  11.8 Mbits/sec   0.000 ms  596/11042999 (0.0054%)
#         interval       transfer    bandwidth        jitter    lost/total

  # next_line = False
  # for line in std_out.splitlines():
  #   print(line)
  #   match = re.search('Lost/Total Datagrams', line)
  #   if match:
  #     next_line = True
  #   else:
  #     next_line = False
  #     metrics = line.split()
  #     print(metrics)
  #     lost_total_datagrams = metrics[8].split('/')
  #     print(lost_total_datagrams)

  num_threads = FLAGS.pps_sending_thread_count
  lost_datagrams = [0] * num_threads
  total_datagrams = [0] * num_threads
  datagrams_per_second = [0] * num_threads
  server_report = False
  thread_num = 0

  for line in std_out.splitlines():
    print(line)
    match = re.search('Server Report', line)
    if match:
      server_report = True
    elif server_report == True:
      metrics = line.split()
      print(metrics)
      lost_total_datagrams = metrics[10].split('/')
      lost_datagrams[thread_num] = int(lost_total_datagrams[0])
      total_datagrams[thread_num] = int(lost_total_datagrams[1])
      print(lost_total_datagrams)
      datagrams_per_second[thread_num] = (total_datagrams[thread_num] - lost_datagrams[thread_num]) / 60
      thread_num += 1
      server_report = False

  sum_datagrams = sum(datagrams_per_second)
  print(sum_datagrams)

  # lost_datagrams = 0
  # total_datagrams = 0
  # test_complete=False
  # for line in std_out.splitlines():
  #   print(line)
  #   match = re.search('Test Complete', line)
  #   if match:
  #     test_complete = True
  #   elif test_complete == True:
  #     match = re.search('SUM', line)
  #     if match:
  #       metrics = line.split()
  #       print(metrics)
  #       lost_total_datagrams = metrics[9].split('/')
  #       lost_datagrams = lost_total_datagrams[0]
  #       total_datagrams = lost_total_datagrams[1]
  #       print(lost_total_datagrams)


  # datagrams_per_second = (total_datagrams - lost_datagrams) / 60


  # stdout2 = stdout
  # match = re.search('TCP window size: (.*) MByte ', stdout)
  # if match:
  #   actual_window_size = match.group(1)
  #   actual_window_size = float(actual_window_size)
  # else:
  #   match = re.search('TCP window size: .* MByte ', stdout)
  #   if match:
  #     actual_window_size = match.group(1)
  #     actual_window_size = float(actual_window_size)
  #   else:
  #     actual_window_size = 0

  # if FLAGS.iperf_tcp_window:
  #   match = re.search('WARNING: requested (.*) MByte\)', stdout)
  #   if match:
  #     requested_window_size = match.group(1)
  #     requested_window_size = float(requested_window_size)
  #   else:
  #     requested_window_size = actual_window_size
  # else:
  #   requested_window_size = 0


  # thread_values = re.findall(r'\[SUM].*\s+(\d+\.?\d*).Mbits/sec', stdout)
  # if not thread_values:
  #   # If there is no sum you have try and figure out an estimate
  #   # which happens when threads start at different times.  The code
  #   # below will tend to overestimate a bit.
  #   thread_values = re.findall('\[.*\d+\].*\s+(\d+\.?\d*).Mbits/sec', stdout)

  #   if len(thread_values) != FLAGS.iperf_sending_thread_count:
  #     raise ValueError('Only %s out of %s iperf threads reported a'
  #                      ' throughput value.' %
  #                      (len(thread_values), FLAGS.iperf_sending_thread_count))
  # total_throughput = 0.0
  # for value in thread_values:
  #   total_throughput += float(value)
  metadata = {
      # The meta data defining the environment
      'receiving_machine_type': receiving_vm.machine_type,
      'receiving_zone': receiving_vm.zone,
      'sending_machine_type': sending_vm.machine_type,
      'sending_thread_count': FLAGS.iperf_sending_thread_count,
      'sending_zone': sending_vm.zone,
      'runtime_in_seconds': FLAGS.iperf_runtime_in_seconds,
      'ip_type': ip_type,
      'total_datagrams': total_datagrams,
      'lost_datagrams': lost_datagrams,
      'pps_target_bandwidth': FLAGS.pps_target_bandwidth,
      'MSS': FLAGS.pps_mss_size,
      'packet_size': FLAGS.pps_packet_size
  }
  return sample.Sample('Datagram Throughput', sum_datagrams, 'datagrams/sec', metadata)

def Run(benchmark_spec):
  """Run iperf on the target vm.
  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  Returns:
    A list of sample.Sample objects.
  """
  vms = benchmark_spec.vms
  results = []
  logging.info('Iperf Results:')
  # Send traffic in both directions
  for sending_vm, receiving_vm in vms, reversed(vms):
    # Send using external IP addresses
    if vm_util.ShouldRunOnExternalIpAddress():
      results.append(_RunIperf(sending_vm,
                               receiving_vm,
                               receiving_vm.ip_address,
                               'external'))
    # Send using internal IP addresses
    if vm_util.ShouldRunOnInternalIpAddress(sending_vm,
                                            receiving_vm):
      results.append(_RunIperf(sending_vm,
                               receiving_vm,
                               receiving_vm.internal_ip,
                               'internal'))
  return results
def Cleanup(benchmark_spec):
  """Cleanup iperf on the target vm (by uninstalling).
  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  vms = benchmark_spec.vms
  for vm in vms:
    vm.RemoteCommand('kill -9 ' + vm.iperf_server_pid, ignore_failure=True)
