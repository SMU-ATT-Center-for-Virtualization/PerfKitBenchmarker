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

import logging
import re
import decimal

from perfkitbenchmarker import configs
from perfkitbenchmarker import flags
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util
from perfkitbenchmarker import flag_util

flag_util.DEFINE_integerlist('iperf_sending_thread_count', flag_util.IntegerList([1]),
                             'server for sending traffic. Iperf'
                             'will run once for each value in the list', 
                             module_name=__name__)
flags.DEFINE_integer('iperf_runtime_in_seconds', 60,
                     'Number of seconds to run iperf.',
                     lower_bound=1)
flags.DEFINE_integer('iperf_timeout', None,
                     'Number of seconds to wait in '
                     'addition to iperf runtime before '
                     'killing iperf client command.',
                     lower_bound=1)

flags.DEFINE_integer('iperf_interval', 
                    0,
                    'This will set how long the intervals of the scan will be.')

FLAGS = flags.FLAGS

BENCHMARK_NAME = 'iperf'
BENCHMARK_CONFIG = """
iperf:
  description: Run iperf
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
        'iperf benchmark requires exactly two machines, found {0}'.format(len(
            vms)))

  for vm in vms:
    if not FLAGS.skip_prepare:
      vm.Install('iperf')
    if vm_util.ShouldRunOnExternalIpAddress():
      vm.AllowPort(IPERF_PORT)
    stdout, _ = vm.RemoteCommand(('nohup iperf --server --port %s &> /dev/null'
                                  '& echo $!') % IPERF_PORT)
    # TODO store this in a better place once we have a better place
    vm.iperf_server_pid = stdout.strip()


@vm_util.Retry(max_retries=IPERF_RETRIES)
def _RunIperf(sending_vm, receiving_vm, receiving_ip_address, thread_count, ip_type):
  """Run iperf using sending 'vm' to connect to 'ip_address'.

  Args:
    sending_vm: The VM sending traffic.
    receiving_vm: The VM receiving traffic.
    receiving_ip_address: The IP address of the iperf server (ie the receiver).
    ip_type: The IP type of 'ip_address' (e.g. 'internal', 'external')
  Returns:
    A Sample.
  """
  print("Iperf command that will be run:")
  print('iperf -e --client %s --port %s --format m --time %s -P %s --interval %s' %
               (receiving_ip_address, IPERF_PORT,
                FLAGS.iperf_runtime_in_seconds,
                thread_count,
                FLAGS.iperf_interval))
  iperf_cmd = ('iperf -e --client %s --port %s --format m --time %s --interval %s' %
               (receiving_ip_address, IPERF_PORT,
                FLAGS.iperf_runtime_in_seconds,
                FLAGS.iperf_interval))
  # iperf_cmd = ('iperf -e --client %s --port %s --format m --time %s -P %s -i %s' %
  #              (receiving_ip_address, IPERF_PORT,
  #               FLAGS.iperf_runtime_in_seconds,
  #               thread_count,
  #               FLAGS.iperf_interval))
  # iperf_cmd = ('iperf -e --client %s --port %s --format m --time %s -P %s' %
  #              (receiving_ip_address, IPERF_PORT,
  #               FLAGS.iperf_runtime_in_seconds,
  #               thread_count))
  # the additional time on top of the iperf runtime is to account for the
  # time it takes for the iperf process to start and exit
  timeout_buffer = FLAGS.iperf_timeout or 30 + thread_count
  stdout, _ = sending_vm.RemoteCommand(iperf_cmd, should_log=True,
                                       timeout=FLAGS.iperf_runtime_in_seconds +
                                       timeout_buffer)
  import sys
  print("Python Version")
  print(sys.version)
  print("version info")
  print(sys.version_info)
  print("OUTPUT")
  multi_thread = re.findall('\[SUM\]\s+\d+\.\d+-\d+\.\d+\s\w+\s+\d+\s\w+\s+\d+\s\w+\/\w+\s+\d+\/\d+\s+\d+\s+', stdout)
  print("MultiThread: {}".format(bool(multi_thread)))
  window_size = re.findall('TCP window size: \d+\.\d+ \S+', stdout)
  #Write Buffer
  buffer_size_re = re.findall('Write buffer size: \d+\.\d+ \S+', stdout)
  #print(f"Find Buffer: {buffer_size}")
  buffer_size = re.findall('\d+\.\d+', str(buffer_size_re))
  print("Buffer Size Num: {}".format(float(buffer_size[0])))
  buffer_size_measurement = re.findall('\d+\.\d+ (\S+)', buffer_size_re[0])
  print("Buffer Size Unit: {}".format(buffer_size_measurement[0]))


  #print(f"type of window_size: {type(window_size)}")
  #print(f"Window_size: {window_size[0]}")
  #This finds the actual window size
  window_size_num = (re.findall('\d+\.\d+', str(window_size)))
  window_size_num = float(window_size_num[0])
  #print(f"type of window_size: {type(str(window_size_num))}")
  print("TCP Window_size: {}".format(window_size_num))
  #print(f"test: {str(window_size)[0]}")
  window_size_measurement = re.findall('\d+\.\d+ (\S+)', window_size[0])
  #print(f"test: {str(window_size)[0]}")
  #This is the Measurement unit for  the window size
  window_size_measurement = window_size_measurement[0]
  print("TCP Window measurement unit: {}".format(window_size_measurement))
  if multi_thread:
    #Write and Err
    write_err = re.findall('\d+ Mbits\/sec\s+(\d+\/\d+)', str(multi_thread))
    #print(f"write: {str(write_err)[0]}")
    write_re = re.findall('\d+', str(write_err))
    write = float(write_re[0])
    print("Write: {}".format(write))
    err = float(write_re[1])
    print("Err: {}".format(err))

    # Retry
    retry_re = re.findall('\d+ Mbits\/sec\s+ \d+\/\d+\s+(\d+)', str(multi_thread))
    retry = float(retry_re[0])
    print("Retry: {}".format(retry))

    # Cwnd
    cwnd_rtt = re.findall('\d+ Mbits\/sec\s+ \d+\/\d+\s+\d+\s+(-*\d+\w+\-*/\d+\s+\w+)', stdout)
    #print("cwnd_rtt all: {}".format(cwnd_rtt))
    #print(cwnd_rtt)
    rtt = 0
    for i in cwnd_rtt:
      rtt_part = re.findall('\/(-*\d+)', i)
      #print("rtt_part: {}".format(rtt_part))
      rtt = rtt + float(rtt_part[0])
    #calculating average
    rtt = round(decimal.Decimal(rtt) / len(cwnd_rtt), 2)
    
    cwnd_re = re.findall('-*\d+\s*', cwnd_rtt[0])
    #print(cwnd_rtt)
    cwnd = float(cwnd_re[0])
    print("Cwnd: {}".format(cwnd))
    cwnd_unit_re = re.findall('-*\d+\s*(\w+)', cwnd_rtt[0])
    #print("cwnd_unit: {}".format(cwnd_unit_re))
    cwnd_unit = cwnd_unit_re[0]
    print("Cwnd Unit: {}".format(cwnd_unit))
    #print("RTT ALL: {}".format(cwnd_re))
    #rtt = float(cwnd_re[1])
    print("RTT: {}".format(rtt))
    rtt_unit = cwnd_unit_re[1]
    print("RTT Unit: {}".format(cwnd_unit_re[1]))
    # Netpwr
    netpwr_re = re.findall('\d+ Mbits\/sec\s+ \d+\/\d+\s+\d+\s+-*\d+\w+\/\d+\s+\w+\s+(\d+\.\d+)', stdout)
    #print("netpwr: {}".format(netpwr_re))
    netpwr = 0
    for i in netpwr_re:
      netpwr = netpwr + float(i)
    netpwr = netpwr / len(netpwr_re)
    netpwr = round(decimal.Decimal(netpwr), 2)
    print("Netpwr: {}".format(netpwr))
  else:
    
    #Write and Err
    write_err = re.findall('\d+ Mbits\/sec\s+(\d+\/\d+)', str(stdout))
    write_re = re.findall('\d+', str(write_err))
    write = float(write_re[0])
    print("Write: {}".format(write))
    err = float(write_re[1])
    print("Err: {}".format(err))

    # Retry
    retry_re = re.findall('\d+ Mbits\/sec\s+ \d+\/\d+\s+(\d+)', str(stdout))
    retry = float(retry_re[0])
    print("Retry: {}".format(retry))

    # Cwnd
    cwnd_rtt = re.findall('\d+ Mbits\/sec\s+ \d+\/\d+\s+\d+\s+(-*\d+\w+\-*/\d+\s+\w+)',stdout)
    #print(cwnd_rtt)
    cwnd_re = re.findall('-*\d+\s*', cwnd_rtt[0])
    #print(cwnd_rtt)
    cwnd = float(cwnd_re[0])
    print("Cwnd: {}".format(cwnd))
    cwnd_unit_re = re.findall('-*\d+\s*(\w+)', cwnd_rtt[0])
    cwnd_unit = cwnd_unit_re[0]
    print("Cwnd Unit: {}".format(cwnd_unit))
    rtt = float(cwnd_re[1])
    print("RTT: {}".format(rtt))
    rtt_unit = cwnd_unit_re[1]
    print("RTT Unit: {}".format(rtt_unit))


    # Netpwr
    netpwr = re.findall('\d+ Mbits\/sec\s+ \d+\/\d+\s+\d+\s+-*\d+\w+\/\d+\s+\w+\s+(\d+\.\d+)', stdout)
    netpwr = float(netpwr[0])
    print("Netpwr: {}".format(netpwr))
  print(stdout)
  # Example output from iperf that needs to be parsed
  # STDOUT: ------------------------------------------------------------
  # Client connecting to 10.237.229.201, TCP port 5001
  # TCP window size: 0.04 MByte (default)
  # ------------------------------------------------------------
  # [  6] local 10.76.234.115 port 53527 connected with 10.237.229.201 port 5001
  # [  3] local 10.76.234.115 port 53524 connected with 10.237.229.201 port 5001
  # [  4] local 10.76.234.115 port 53525 connected with 10.237.229.201 port 5001
  # [  5] local 10.76.234.115 port 53526 connected with 10.237.229.201 port 5001
  # [ ID] Interval       Transfer     Bandwidth
  # [  4]  0.0-60.0 sec  3730 MBytes  521.1 Mbits/sec
  # [  5]  0.0-60.0 sec  3499 MBytes   489 Mbits/sec
  # [  6]  0.0-60.0 sec  3044 MBytes   425 Mbits/sec
  # [  3]  0.0-60.0 sec  3738 MBytes   522 Mbits/sec
  # [SUM]  0.0-60.0 sec  14010 MBytes  1957 Mbits/sec


  #NEW OUTPUT
#   ------------------------------------------------------------
# Client connecting to 172.17.0.5, TCP port 20000 with pid 4167
# Write buffer size: 0.12 MByte
# TCP window size: 1.42 MByte (default)
# ------------------------------------------------------------
# [  3] local 172.17.0.6 port 45518 connected with 172.17.0.5 port 20000 (ct=0.08 ms)
# [ ID] Interval        Transfer    Bandwidth       Write/Err  Rtry     Cwnd/RTT        NetPwr
# [  3] 0.00-60.00 sec  236112 MBytes  33011 Mbits/sec  1888894/0          0       -1K/25 us  165054051.49



  thread_values = re.findall(r'\[SUM].*\s+(\d+\.?\d*).Mbits/sec', stdout)
  if not thread_values:
    # If there is no sum you have try and figure out an estimate
    # which happens when threads start at different times.  The code
    # below will tend to overestimate a bit.
    thread_values = re.findall('\[.*\d+\].*\s+(\d+\.?\d*).Mbits/sec', stdout)

    if len(thread_values) != thread_count:
      raise ValueError('Only %s out of %s iperf threads reported a'
                       ' throughput value.' %
                       (len(thread_values), thread_count))

  total_throughput = 0.0
  for value in thread_values:
    total_throughput += float(value)

  metadata = {
      # The meta data defining the environment
      'receiving_machine_type': receiving_vm.machine_type,
      'receiving_zone': receiving_vm.zone,
      'sending_machine_type': sending_vm.machine_type,
      'sending_thread_count': thread_count,
      'sending_zone': sending_vm.zone,
      'runtime_in_seconds': FLAGS.iperf_runtime_in_seconds,
      'ip_type': ip_type,
      'buffer_size' : buffer_size,
      'tcp_window_size': window_size_num,
      'write' : write,
      'err' : err,
      'retry' : retry,
      'cwnd' : cwnd,
      'rtt' : rtt,
      'netpwr' : netpwr
  }
  return sample.Sample('Throughput', total_throughput, 'Mbits/sec', metadata)


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
  for thread_count in FLAGS.iperf_sending_thread_count:
    for sending_vm, receiving_vm in vms, reversed(vms):
      # Send using external IP addresses
      if vm_util.ShouldRunOnExternalIpAddress():
        results.append(_RunIperf(sending_vm,
                                 receiving_vm,
                                 receiving_vm.ip_address,
                                 thread_count,
                                 'external'))

      # Send using internal IP addresses
      if vm_util.ShouldRunOnInternalIpAddress(sending_vm,
                                              receiving_vm):
        results.append(_RunIperf(sending_vm,
                                 receiving_vm,
                                 receiving_vm.internal_ip,
                                 thread_count,
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
