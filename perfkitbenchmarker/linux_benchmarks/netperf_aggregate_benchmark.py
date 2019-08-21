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

"""Runs plain netperf in a few modes.

docs:
http://www.netperf.org/svn/netperf2/tags/netperf-2.4.5/doc/netperf.html#TCP_005fRR
manpage: http://manpages.ubuntu.com/manpages/maverick/man1/netperf.1.html

Runs TCP_RR, TCP_CRR, and TCP_STREAM benchmarks from netperf across two
machines.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import collections
import csv
import io
import json
import logging
import os
import re
from perfkitbenchmarker import configs
from perfkitbenchmarker import data
from perfkitbenchmarker import flag_util
from perfkitbenchmarker import flags
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_packages import netperf
from six.moves import zip



# ALL_BENCHMARKS = ['TCP_RR', 'TCP_CRR', 'TCP_STREAM', 'UDP_RR', 'UDP_STREAM']

# flags.DEFINE_list('netperf_aggregate_benchmarks', ALL_BENCHMARKS,
#                   'The netperf benchmark(s) to run.')
# flags.register_validator(
#     'netperf_benchmarks',
#     lambda benchmarks: benchmarks and set(benchmarks).issubset(ALL_BENCHMARKS))

FLAGS = flags.FLAGS

BENCHMARK_NAME = 'netperf_aggregate'
BENCHMARK_CONFIG = """
netperf_aggregate:
  description: Run TCP_RR, TCP_CRR, UDP_RR and TCP_STREAM
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
    vm_3:
      vm_spec: *default_single_core
"""

MBPS = 'Mbits/sec'
TRANSACTIONS_PER_SECOND = 'transactions_per_second'
# Specifies the keys and to include in the results for OMNI tests.
# Any user of ParseNetperfOutput() (e.g. container_netperf_benchmark), must
# specify these selectors to ensure the parsing doesn't break.
OUTPUT_SELECTOR = (
    'THROUGHPUT,THROUGHPUT_UNITS,P50_LATENCY,P90_LATENCY,'
    'P99_LATENCY,STDDEV_LATENCY,MIN_LATENCY,MAX_LATENCY,'
    'CONFIDENCE_ITERATION,THROUGHPUT_CONFID,'
    'LOCAL_TRANSPORT_RETRANS,REMOTE_TRANSPORT_RETRANS')

# Command ports are even (id*2), data ports are odd (id*2 + 1)
PORT_START = 12865

REMOTE_SCRIPTS_DIR = 'netperf_test_scripts'
REMOTE_SCRIPT = 'netperf_test.py'

PERCENTILES = [50, 90, 99]

# By default, Container-Optimized OS (COS) host firewall allows only
# outgoing connections and incoming SSH connections. To allow incoming
# connections from VMs running netperf, we need to add iptables rules
# on the VM running netserver.
_COS_RE = re.compile(r'\b(cos|gci)-')


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def PrepareNetperf(vm):
  """Installs netperf on a single vm."""

#INSTALL
# Ensure gcc, make, automake, texinfo and python-rrdtool are installed
# Bring a netperf source tree to the systems
# cd to the top of the tree
# run ./autogen.sh
# ./configure --enable-burst --enable-demo --enable-histogram
# make  # sudo make install if you prefer
  vm.Install('texinfo')
  vm.Install('python_rrdtool')
  vm.Install('netperf')


  # Set keepalive to a low value to ensure that the control connection
  # is not closed by the cloud networking infrastructure.
  # This causes keepalive packets to be sent every minute on all ipv4
  # tcp connections.
  #
  # TODO(user): Keepalive is not enabled on the netperf control socket.
  # While (for unknown reasons) this hack fixes the issue with the socket
  # being closed anyway, a more correct approach would be to patch netperf
  # and enable keepalive on the control socket in addition to changing the
  # system defaults below.

  vm.ApplySysctlPersistent({
      'net.ipv4.tcp_keepalive_time': 60,
      'net.ipv4.tcp_keepalive_intvl': 60,
  })

  # PORT_END = PORT_START * 2 - 1
  PORT_END = PORT_START

  if vm.image and re.search(_COS_RE, vm.image):
    _SetupHostFirewall(benchmark_spec)

  if vm_util.ShouldRunOnExternalIpAddress():
    vm.AllowPort(PORT_START, PORT_END)

  netserver_cmd = ('for i in $(seq {port_start} 2 {port_end}); do '
                   '{netserver_path} -p $i & done').format(
                       port_start=PORT_START,
                       port_end=PORT_END,
                       netserver_path=netperf.NETSERVER_PATH)
  vm.RemoteCommand(netserver_cmd)

  path = data.ResourcePath(os.path.join(REMOTE_SCRIPTS_DIR, REMOTE_SCRIPT))
  logging.info('Uploading %s to %s', path, vm)
  vm.PushFile(path, REMOTE_SCRIPT)
  vm.RemoteCommand('sudo chmod 777 %s' % REMOTE_SCRIPT)

  vm.RemoteCommand("cd %s && sed 's/DO_STREAM=1;/DO_STREAM=0;/g' runemomniaggdemo.sh "
                   "&& sed -i 's/DO_STREAM=1;/DO_STREAM=0;/g' runemomniaggdemo.sh "
                   "&& sed -i 's/DO_MAERTS=1;/DO_MAERTS=0;/g' runemomniaggdemo.sh "
                   "&& sed -i 's/DO_BIDIR=1;/DO_BIDIR=0;/g' runemomniaggdemo.sh "
                   "&& sed -i 's/DO_RR=1;/DO_RR=0;/g' runemomniaggdemo.sh "
                   "&& sed -i 's/DO_ANCILLARY=1;/DO_ANCILLARY=0;/g' runemomniaggdemo.sh " 
                   "&& sed -i 's/DURATION=120/DURATION=60/g' runemomniaggdemo.sh " 
                   % (netperf.NETPERF_EXAMPLE_DIR))

  # vm.RemoteCommand('cd %s && CFLAGS=-DHIST_NUM_OF_BUCKET=%s '
  #                  './autogen.sh'
  #                  './configure --enable-burst --enable-demo --enable-histogram=yes '
  #                  '&& make' % (NETPERF_DIR, FLAGS.netperf_histogram_buckets))


#SETUP
# src/netserver # start the netserver on the load generator systems
# On the system to be the one under test, cd to  doc/examples/ in the netperf source tree
# Ensure that runemomniaggdemo.sh and find_max_burst.sh have the execute bit set
# chmod +x runemomniaggdemo.sh find_max_burst.sh
# Edit runemomniaggdemo.sh to:
# Enable aggregate Transactions Per Second (tps) tests - DO_RRAGG set to '1'
# Disable the other tests - DO_mumble set to '0'
def Prepare(benchmark_spec):
  """Install netperf on the target vm.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """

  logging.info("INPUT TO CONTINUE")
  lol = raw_input()

  
  logging.info("RUNNING NETPERF Prepare")
  vms = benchmark_spec.vms
  vms = vms[:3]
  vm_util.RunThreaded(PrepareNetperf, vms)

  # See comments where _COS_RE is defined.
  # if vms[1].image and re.search(_COS_RE, vms[1].image):
  #   _SetupHostFirewall(benchmark_spec)

  # Start the netserver processes
  # if vm_util.ShouldRunOnExternalIpAddress():
  #   # Open all of the command and data ports
  #   vms[1].AllowPort(PORT_START, PORT_START + num_streams * 2 - 1)

  # netserver_cmd = ('for i in $(seq {port_start} 2 {port_end}); do '
  #                  '{netserver_path} -p $i & done').format(
  #                      port_start=PORT_START,
  #                      port_end=PORT_START + num_streams * 2 - 1,
  #                      netserver_path=netperf.NETSERVER_PATH)
  # vms[1].RemoteCommand(netserver_cmd)

  # Copy remote test script to client
  # path = data.ResourcePath(os.path.join(REMOTE_SCRIPTS_DIR, REMOTE_SCRIPT))
  # logging.info('Uploading %s to %s', path, vms[0])
  # vms[0].PushFile(path, REMOTE_SCRIPT)
  # vms[0].RemoteCommand('sudo chmod 777 %s' % REMOTE_SCRIPT)



#FIXME: change this
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


def ParseNetperfAggregateOutput(stdout, metadata, benchmark_name,
                       enable_latency_histograms):
  """Parses the stdout of a single netperf process.

  Args:
    stdout: the stdout of the netperf process
    metadata: metadata for any sample.Sample objects we create

  Returns:
    A tuple containing (throughput_sample, latency_samples, latency_histogram)
  """
  # Don't modify the metadata dict that was passed in
  metadata = metadata.copy()

  # Extract stats from stdout
  # Sample output:
  #
  # "MIGRATED TCP REQUEST/RESPONSE TEST from 0.0.0.0 (0.0.0.0) port 20001
  # AF_INET to 104.154.50.86 () port 20001 AF_INET : +/-2.500% @ 99% conf.
  # : first burst 0",\n
  # Throughput,Throughput Units,Throughput Confidence Width (%),
  # Confidence Iterations Run,Stddev Latency Microseconds,
  # 50th Percentile Latency Microseconds,90th Percentile Latency Microseconds,
  # 99th Percentile Latency Microseconds,Minimum Latency Microseconds,
  # Maximum Latency Microseconds\n
  # 1405.50,Trans/s,2.522,4,783.80,683,735,841,600,900\n
  try:
    fp = io.StringIO(stdout)
    # "-o" flag above specifies CSV output, but there is one extra header line:
    banner = next(fp)
    assert banner.startswith('MIGRATED'), stdout
    r = csv.DictReader(fp)
    results = next(r)
    logging.info('Netperf Results: %s', results)
    assert 'Throughput' in results
  except:
    raise Exception('Netperf ERROR: Failed to parse stdout. STDOUT: %s' %
                    stdout)

  # Update the metadata with some additional infos
  meta_keys = [('Confidence Iterations Run', 'confidence_iter'),
               ('Throughput Confidence Width (%)', 'confidence_width_percent')]
  if 'TCP' in benchmark_name:
    meta_keys.extend([
        ('Local Transport Retransmissions', 'netperf_retransmissions'),
        ('Remote Transport Retransmissions', 'netserver_retransmissions'),
    ])

  metadata.update({meta_key: results[netperf_key]
                   for netperf_key, meta_key in meta_keys})

  # Create the throughput sample
  throughput = float(results['Throughput'])
  throughput_units = results['Throughput Units']
  if throughput_units == '10^6bits/s':
    # TCP_STREAM benchmark
    unit = MBPS
    metric = '%s_Throughput' % benchmark_name
  elif throughput_units == 'Trans/s':
    # *RR benchmarks
    unit = TRANSACTIONS_PER_SECOND
    metric = '%s_Transaction_Rate' % benchmark_name
  else:
    raise ValueError('Netperf output specifies unrecognized throughput units %s'
                     % throughput_units)
  throughput_sample = sample.Sample(metric, throughput, unit, metadata)

  latency_hist = None
  latency_samples = []
  if enable_latency_histograms:
    # Parse the latency histogram. {latency: count} where "latency" is the
    # latency in microseconds with only 2 significant figures and "count" is the
    # number of response times that fell in that latency range.
    latency_hist = netperf.ParseHistogram(stdout)
    hist_metadata = {'histogram': json.dumps(latency_hist)}
    hist_metadata.update(metadata)
    latency_samples.append(sample.Sample(
        '%s_Latency_Histogram' % benchmark_name, 0, 'us', hist_metadata))
  if unit != MBPS:
    for metric_key, metric_name in [
        ('50th Percentile Latency Microseconds', 'p50'),
        ('90th Percentile Latency Microseconds', 'p90'),
        ('99th Percentile Latency Microseconds', 'p99'),
        ('Minimum Latency Microseconds', 'min'),
        ('Maximum Latency Microseconds', 'max'),
        ('Stddev Latency Microseconds', 'stddev')]:
      if metric_key in results:
        latency_samples.append(
            sample.Sample('%s_Latency_%s' % (benchmark_name, metric_name),
                          float(results[metric_key]), 'us', metadata))

  return (throughput_sample, latency_samples, latency_hist)


def RunNetperf(vm, benchmark_name, server1_ip, server2_ip):
  """Spawns netperf on a remote VM, parses results.

  Args:
    vm: The VM that the netperf TCP_RR benchmark will be run upon.
    benchmark_name: The netperf benchmark to run, see the documentation.
    server_ip: A machine that is running netserver.
    num_streams: The number of netperf client threads to run.

  Returns:
    A sample.Sample object with the result.
  """

#RUN
# Set DURATION to your desired time length for each data point - eg 60 (seconds)
# Add the location of the find_max_burst.sh script to your $PATH variable
# export PATH=$PATH:.
# Edit/create a "remote_hosts" file like:
# REMOTE_HOSTS[0]=lg1
# REMOTE_HOSTS[1]=lg2
# REMOTE_HOSTS[2]=lg3
# REMOTE_HOSTS[3]=lg4
# NUM_REMOTE_HOSTS=4
# ./runemomniaggdemo.sh   # execute the script
# ./post_proc.py --intervals netperf_tps.log # script relies on python_rrdtool
# Enjoy the results.  There will also be a chart in "netperf_tsp_overall.svg" for those who prefer pictures over text
  
  vm.RemoteCommand("cd %s && echo REMOTE_HOSTS[0]=%s > remote_hosts && "
                   "echo REMOTE_HOSTS[1]=%s >> remote_hosts && "
                   "echo NUM_REMOTE_HOSTS=2 >> remote_hosts"
                   % (netperf.NETPERF_EXAMPLE_DIR, server1_ip, server2_ip))

  # vm.RemoteCommand('sudo su')

  vm.RemoteCommand('cd %s && export PATH=$PATH:.' % (netperf.NETPERF_EXAMPLE_DIR))

  stdout,_ = vm.RemoteCommand('cd %s && cat remote_hosts' % (netperf.NETPERF_EXAMPLE_DIR))
  logging.info(stdout)


                                      # command,
                                      # should_log=False,
                                      # retries=None,
                                      # ignore_failure=False,
                                      # login_shell=False,
                                      # suppress_warning=False,
                                      # timeout=None

  logging.info("INPUT TO CONTINUE")
  lol = raw_input()

  vm.RemoteCommand("cd %s && export PATH=$PATH:. && ./runemomniaggdemo.sh" % (netperf.NETPERF_EXAMPLE_DIR), should_log=True, login_shell=True, timeout=1200)
  # vm.RobustRemoteCommand("cd %s && ./runemomniaggdemo.sh" % (netperf.NETPERF_EXAMPLE_DIR), should_log=True, timeout=1200,
  #                         ignore_failure=False)

  remote_stdout, _ = vm.RemoteCommand("cd %s && ./post_proc.py --intervals netperf_tps.log" % (netperf.NETPERF_EXAMPLE_DIR))

  logging.info(remote_stdout)

    # Metadata to attach to samples
  metadata = {'number_of_hosts': 3,
              'netperf_test_length_unit': 'transactions',
              'max_iter': 1}

  # if FLAGS.netperf_thinktime != 0:
  #   netperf_cmd += (' -X {thinktime},{thinktime_array_size},'
  #                   '{thinktime_run_length} ').format(
  #                       thinktime=FLAGS.netperf_thinktime,
  #                       thinktime_array_size=FLAGS.netperf_thinktime_array_size,
  #                       thinktime_run_length=FLAGS.netperf_thinktime_run_length)

  # Run all of the netperf processes and collect their stdout
  # TODO: Record process start delta of netperf processes on the remote machine

  # Give the remote script the max possible test length plus 5 minutes to
  # complete
  
  # remote_cmd = ('./%s --netperf_cmd="%s" --num_streams=%s --port_start=%s' %
  #               (REMOTE_SCRIPT, netperf_cmd, num_streams, PORT_START))
  # remote_stdout, _ = vm.RobustRemoteCommand(remote_cmd, should_log=True,
  #                                           timeout=remote_cmd_timeout)

  # Decode stdouts, stderrs, and return codes from remote command's stdout
  json_out = json.loads(remote_stdout)
  stdouts = json_out[0]

  

  parsed_output = [ParseNetperfOutput(stdout, metadata, benchmark_name,
                                      enable_latency_histograms)
                   for stdout in stdouts]

  if len(parsed_output) == 1:
    # Only 1 netperf thread
    throughput_sample, latency_samples, histogram = parsed_output[0]
    return [throughput_sample] + latency_samples
  else:
    # Multiple netperf threads

    samples = []

    # Unzip parsed output
    # Note that latency_samples are invalid with multiple threads because stats
    # are computed per-thread by netperf, so we don't use them here.
    throughput_samples, _, latency_histograms = [list(t)
                                                 for t in zip(*parsed_output)]
    # They should all have the same units
    throughput_unit = throughput_samples[0].unit
    # Extract the throughput values from the samples
    throughputs = [s.value for s in throughput_samples]
    # Compute some stats on the throughput values
    throughput_stats = sample.PercentileCalculator(throughputs, [50, 90, 99])
    throughput_stats['min'] = min(throughputs)
    throughput_stats['max'] = max(throughputs)
    # Calculate aggregate throughput
    throughput_stats['total'] = throughput_stats['average'] * len(throughputs)
    # Create samples for throughput stats
    for stat, value in throughput_stats.items():
      samples.append(
          sample.Sample('%s_Throughput_%s' % (benchmark_name, stat),
                        float(value),
                        throughput_unit, metadata))
    if enable_latency_histograms:
      # Combine all of the latency histogram dictionaries
      latency_histogram = collections.Counter()
      for histogram in latency_histograms:
        latency_histogram.update(histogram)
      # Create a sample for the aggregate latency histogram
      hist_metadata = {'histogram': json.dumps(latency_histogram)}
      hist_metadata.update(metadata)
      samples.append(sample.Sample(
          '%s_Latency_Histogram' % benchmark_name, 0, 'us', hist_metadata))
      # Calculate stats on aggregate latency histogram
      latency_stats = _HistogramStatsCalculator(latency_histogram, [50, 90, 99])
      # Create samples for the latency stats
      for stat, value in latency_stats.items():
        samples.append(
            sample.Sample('%s_Latency_%s' % (benchmark_name, stat),
                          float(value),
                          'us', metadata))
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
  client_vm = vms[0]  # Client aka "sending vm"
  server_vm1 = vms[1]  # Server1 aka "receiving vm"
  server_vm2 = vms[2]  # Server2 aka "receiving vm"

  # TODO: expand beyond 3 fixed VMs
  # vm_num = len(vms)
  # for i in vm_num:


  logging.info('netperf running on %s', client_vm)
  results = []
  metadata = {
      'sending_zone': client_vm.zone,
      'sending_machine_type': client_vm.machine_type,
      'receiving_zone': server_vm1.zone,
      'receiving_machine_type': server_vm1.machine_type
  }



# for num_streams in FLAGS.netperf_num_streams:
#   assert num_streams >= 1
  for netperf_benchmark in FLAGS.netperf_benchmarks:
    if vm_util.ShouldRunOnExternalIpAddress():

      external_ip_results = RunNetperf(client_vm, netperf_benchmark,
                                       server_vm1.ip_address, server_vm2.ip_address)
      for external_ip_result in external_ip_results:
        external_ip_result.metadata['ip_type'] = 'external'
        external_ip_result.metadata.update(metadata)
      results.extend(external_ip_results)

    if vm_util.ShouldRunOnInternalIpAddress(client_vm, server_vm1) and vm_util.ShouldRunOnInternalIpAddress(client_vm, server_vm2):

      internal_ip_results = RunNetperf(client_vm, netperf_benchmark,
                                       server_vm1.internal_ip, server_vm2.internal_ip)
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
