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
"""Runs netperf and netserver on the SAME VM.

docs:
https://hewlettpackard.github.io/netperf/doc/netperf.html
manpage: http://manpages.ubuntu.com/manpages/maverick/man1/netperf.1.html

Runs TCP_RR, TCP_CRR, and TCP_STREAM benchmarks from netperf locally on a single
VM to assess local host TCP/networking stack performance.
"""

import collections
import csv
import json
import logging
import os
import re
import time
from absl import flags
from perfkitbenchmarker import background_tasks
from perfkitbenchmarker import configs
from perfkitbenchmarker import data
from perfkitbenchmarker import errors
from perfkitbenchmarker import flag_util
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_packages import netperf
import six

P = sample.Percentile

TCP_RR = 'TCP_RR'
TCP_CRR = 'TCP_CRR'
TCP_STREAM = 'TCP_STREAM'
UDP_RR = 'UDP_RR'
UDP_STREAM = 'UDP_STREAM'
SCTP_STREAM = 'SCTP_STREAM'

TCP_BENCHMARKS = [TCP_RR, TCP_CRR, TCP_STREAM]
ALL_BENCHMARKS = TCP_BENCHMARKS + [UDP_RR, UDP_STREAM, SCTP_STREAM]

FLAGS = flags.FLAGS

BENCHMARK_NAME = 'netperf_same_host'
BENCHMARK_CONFIG = """
netperf_same_host:
  description: Run netperf and netserver on the same single VM to test host TCP stack.
  vm_groups:
    vm_1:
      vm_spec: *default_dual_core
"""

MBPS = 'Mbits/sec'
TRANSACTIONS_PER_SECOND = 'transactions_per_second'
OUTPUT_SELECTOR = (
    'THROUGHPUT,THROUGHPUT_UNITS,P50_LATENCY,P90_LATENCY,'
    'P99_LATENCY,STDDEV_LATENCY,MIN_LATENCY,MAX_LATENCY,'
    'CONFIDENCE_ITERATION,THROUGHPUT_CONFID,'
    'LOCAL_TRANSPORT_RETRANS,REMOTE_TRANSPORT_RETRANS,'
    'TRANSPORT_MSS'
)

PORT_START = 20000
REMOTE_SCRIPTS_DIR = 'netperf_test_scripts'
REMOTE_SCRIPT = 'netperf_test.py'
LOOPBACK_IP = '127.0.0.1'

_SELECTED_METRICS = [
    P(50),
    P(90),
    P(99),
    sample.Aggregation.AVERAGE,
    sample.Aggregation.STDDEV,
    sample.Aggregation.MIN,
    sample.Aggregation.MAX,
    sample.Aggregation.TOTAL,
]

_COS_RE = re.compile(r'\b(cos|gci)-')


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def PrepareNetperf(vm):
  """Installs netperf on the target vm."""
  vm.Install('netperf')


def Prepare(benchmark_spec):
  """Prepares the single VM to run both netserver and netperf locally."""
  vm = benchmark_spec.vms[0]
  PrepareNetperf(vm)
  PrepareClientVM(vm)
  PrepareServerVM(vm, [LOOPBACK_IP], [LOOPBACK_IP])


def PrepareClientVM(client_vm):
  """Installs netperf and copies remote test script to client VM."""
  path = data.ResourcePath(os.path.join(REMOTE_SCRIPTS_DIR, REMOTE_SCRIPT))
  logging.info('Uploading %s to %s', path, client_vm)
  client_vm.PushFile(path, REMOTE_SCRIPT)
  client_vm.RemoteCommand(f'sudo chmod 755 {REMOTE_SCRIPT}')


def PrepareServerVM(server_vm, client_vm_internal_ips, client_vm_ip_address):
  """Starts netserver processes on the local VM."""
  num_streams = max(FLAGS.netperf_num_streams)

  if server_vm.image and re.search(_COS_RE, server_vm.image):
    for internal_ip in client_vm_internal_ips:
      _SetupHostFirewall(server_vm, internal_ip, client_vm_ip_address)

  port_end = PORT_START + num_streams * 2 - 1
  netserver_cmd = (
      f'for i in $(seq {PORT_START} 2 {port_end}); do '
      f'{netperf.NETSERVER_PATH} -p $i & done'
  )
  server_vm.RemoteCommand(netserver_cmd)


def _SetupHostFirewall(server_vm, client_vm_internal_ip, client_vm_ip_address):
  """Set up host firewall rules if needed."""
  ip_addrs = [client_vm_internal_ip]
  cmd = 'sudo iptables -A INPUT -p %s -s %s -j ACCEPT'
  for protocol in 'tcp', 'udp':
    for ip_addr in ip_addrs:
      server_vm.RemoteHostCommand(cmd % (protocol, ip_addr))


def _HistogramStatsCalculator(histogram):
  """Computes values at percentiles in a distribution as well as stddev."""
  stats = {}
  by_value = sorted(
      [(value, count) for value, count in histogram.items()], key=lambda x: x[0]
  )
  total_count = sum(histogram.values())
  if total_count == 0:
    return stats
  cur_value_index = 0
  cur_index = 0

  latency_percentiles = FLAGS.netperf_histogram_percentiles #_HISTOGRAM_PERCENTILES.value
  for percent in latency_percentiles:
    index = int(float(total_count) * float(percent) / 100.0)
    index = min(index, total_count - 1)
    for value, count in by_value[cur_value_index:]:
      if cur_index + count > index:
        stat_str = f'p{int(percent) if percent.is_integer() else percent}'
        stats[stat_str] = by_value[cur_value_index][0]
        break
      else:
        cur_index += count
        cur_value_index += 1

  value_sum = float(sum([value * count for value, count in histogram.items()]))
  average = value_sum / float(total_count)
  if total_count > 1:
    total_of_squares = sum(
        [(value - average) ** 2 * count for value, count in histogram.items()]
    )
    stats['stddev'] = (total_of_squares / (total_count - 1)) ** 0.5
  else:
    stats['stddev'] = 0
  return stats


def ParseNetperfOutput(
    stdout, metadata, benchmark_name, enable_latency_histograms
):
  """Parses the stdout of a single netperf process."""
  metadata = metadata.copy()

  try:
    fp = six.StringIO(stdout)
    banner = next(fp)
    assert banner.startswith('OMNI') or banner.startswith('MIGRATED'), stdout
    r = csv.DictReader(fp)
    results = next(r)
    logging.info('Netperf Results: %s', results)
    assert 'Throughput' in results
  except (StopIteration, AssertionError) as e:
    message = 'Netperf ERROR: Failed to parse stdout. STDOUT: %s' % stdout
    logging.exception(message)
    raise errors.Benchmarks.KnownIntermittentError(message) from e

  meta_keys = [
      ('Confidence Iterations Run', 'confidence_iter'),
      ('Throughput Confidence Width (%)', 'confidence_width_percent'),
  ]
  if 'TCP' in benchmark_name:
    meta_keys.extend([
        ('Local Transport Retransmissions', 'netperf_retransmissions'),
        ('Remote Transport Retransmissions', 'netserver_retransmissions'),
        ('Transport MSS bytes', 'netperf_mss'),
    ])

  metadata.update(
      {meta_key: results[netperf_key] for netperf_key, meta_key in meta_keys}
  )

  throughput = float(results['Throughput'])
  throughput_units = results['Throughput Units']
  if throughput_units == '10^6bits/s':
    unit = MBPS
    metric = '%s_Throughput' % benchmark_name
  elif throughput_units == 'Trans/s':
    unit = TRANSACTIONS_PER_SECOND
    metric = '%s_Transaction_Rate' % benchmark_name
  else:
    raise ValueError(
        'Netperf output specifies unrecognized throughput units %s'
        % throughput_units
    )
  throughput_sample = sample.Sample(metric, throughput, unit, metadata)

  latency_hist = None
  latency_samples = []
  if enable_latency_histograms:
    latency_hist = netperf.ParseHistogram(stdout)
    hist_metadata = {'histogram': json.dumps(latency_hist)}
    hist_metadata.update(metadata)
    latency_samples.append(
        sample.Sample(
            '%s_Latency_Histogram' % benchmark_name, 0, 'us', hist_metadata
        )
    )
  if unit != MBPS:
    for metric_key, metric_name in [
        ('50th Percentile Latency Microseconds', 'p50'),
        ('90th Percentile Latency Microseconds', 'p90'),
        ('99th Percentile Latency Microseconds', 'p99'),
        ('Minimum Latency Microseconds', 'min'),
        ('Maximum Latency Microseconds', 'max'),
        ('Stddev Latency Microseconds', 'stddev'),
    ]:
      if metric_key in results:
        latency_samples.append(
            sample.Sample(
                '%s_Latency_%s' % (benchmark_name, metric_name),
                float(results[metric_key]),
                'us',
                metadata,
            )
        )

  return (throughput_sample, latency_samples, latency_hist)


def RunNetperf(
    vm, benchmark_name, server_ips, num_streams, client_ips, test_length
):
  """Spawns netperf on the VM targeting local server_ips."""
  enable_latency_histograms = FLAGS.netperf_enable_histograms or num_streams > 1
  enable_latency_histograms = enable_latency_histograms and (
      benchmark_name not in ['TCP_STREAM', 'UDP_STREAM']
  )

  confidence = (
      f'-I 99,5 -i {FLAGS.netperf_max_iter},3' if FLAGS.netperf_max_iter else ''
  )
  verbosity = '-v2 ' if enable_latency_histograms else ''

  metadata = {
      'netperf_test_length': test_length,
      'sending_thread_count': num_streams,
      'max_iter': FLAGS.netperf_max_iter or 1,
      'netperf_warmup_test_length': FLAGS.netperf_warmup_test_length,
      'same_host': True,
  }

  remote_cmd_list = []
  protocol, direction = benchmark_name.split('_')
  
  for server_ip_idx, server_ip in enumerate(server_ips):
    client_ip = client_ips[server_ip_idx % len(client_ips)]
    netperf_cmd = (
        f'{netperf.NETPERF_PATH} '
        '-p {command_port} '
        f'-j {verbosity} '
        f'-t OMNI '
        f'-H {server_ip} -L {client_ip} '
        f'-l {test_length} {confidence}'
        ' -- '
        f'-T {protocol} '
        f'-d {direction} '
        '-P ,{data_port} '
        f'-o {OUTPUT_SELECTOR}'
    )

    if direction == 'STREAM':
      if protocol == 'UDP':
        send_size = FLAGS.netperf_udp_stream_send_size_in_bytes
        netperf_cmd += ' -R 1'
      elif protocol == 'TCP':
        send_size = FLAGS.netperf_tcp_stream_send_size_in_bytes
      elif protocol == 'SCTP':
        send_size = _SCTP_SEND_SIZE.value
      else:
        raise ValueError(
            f'Unsupported protocol for netperf stream benchmark: {protocol}'
        )
      netperf_cmd += f' -m {send_size} -M {send_size} '
      metadata['netperf_send_size_in_bytes'] = send_size

    if FLAGS.netperf_thinktime != 0:
      netperf_cmd += (
          ' -X '
          f'{FLAGS.netperf_thinktime},'
          f'{FLAGS.netperf_thinktime_array_size},'
          f'{FLAGS.netperf_thinktime_run_length} '
      )

    if FLAGS.netperf_mss and protocol == 'TCP':
      netperf_cmd += f' -G {FLAGS.netperf_mss}b'
      metadata['netperf_mss_requested'] = FLAGS.netperf_mss

    remote_cmd = (
        f'./{REMOTE_SCRIPT} --netperf_cmd="{netperf_cmd}" '
        f'--num_streams={num_streams} --port_start={PORT_START+(server_ip_idx*2*num_streams)}'
    )

    if (
        FLAGS.netperf_numactl_membind #NETPERF_NUMACTL_MEMBIND.value
        or FLAGS.netperf_numactl_physcpubind #NETPERF_NUMACTL_PHYSCPUBIND.value
        or FLAGS.netperf_numactl_cpunodebind #NETPERF_NUMACTL_CPUNODEBIND.value
    ):
      numactl_prefix = 'numactl '
      if FLAGS.netperf_numactl_physcpubind: #if NETPERF_NUMACTL_PHYSCPUBIND.value:
        numactl_prefix += f'--physcpubind {FLAGS.netperf_numactl_physcpubind} '
        metadata['netperf_physcpubind'] = FLAGS.netperf_numactl_physcpubind
      if FLAGS.netperf_numactl_cpunodebind:
        numactl_prefix += f'--cpunodebind {FLAGS.netperf_numactl_cpunodebind} '
        metadata['netperf_cpunodebind'] = FLAGS.netperf_numactl_cpunodebind
      if FLAGS.netperf_numactl_membind:
        numactl_prefix += f'--membind {FLAGS.netperf_numactl_membind} '
        metadata['netperf_membind'] = FLAGS.netperf_numactl_membind
      remote_cmd = f'{numactl_prefix} {remote_cmd}'

    if FLAGS.netperf_perf_record:
      remote_cmd = f'sudo perf record -g -F 99 -- {remote_cmd}'

    remote_cmd_list.append(remote_cmd)

  remote_cmd_timeout = (
      test_length * (FLAGS.netperf_max_iter or 1) + 300
  )
  start_time = time.time()
  remote_stdout_stderr_threads = background_tasks.RunThreaded(
      lambda cmd: vm.RobustRemoteCommand(cmd, timeout=remote_cmd_timeout),
      remote_cmd_list,
  )
  end_time = time.time()
  start_time_sample = sample.Sample('start_time', start_time, 'sec', metadata)
  end_time_sample = sample.Sample('end_time', end_time, 'sec', metadata)

  json_outs = [
      json.loads(remote_stdout_stderr[0])
      for remote_stdout_stderr in remote_stdout_stderr_threads
  ]
  stdouts_list = [json_out[0] for json_out in json_outs]

  parsed_output = []
  for stdouts in stdouts_list:
    for stdout in stdouts:
      parsed_output.append(
          ParseNetperfOutput(
              stdout, metadata, benchmark_name, enable_latency_histograms
          )
      )

  samples = [start_time_sample, end_time_sample]
  if len(parsed_output) == 1:
    throughput_sample, latency_samples, histogram = parsed_output[0]
    latency_histogram = collections.Counter()
    latency_histogram.update(histogram)
    latency_stats = _HistogramStatsCalculator(latency_histogram)
    for stat, value in latency_stats.items():
      if stat in ['p50', 'p90', 'p99']:
        continue
      latency_samples.append(
          sample.Sample(
              f'{benchmark_name}_Latency_{stat}',
              float(value),
              'us',
              throughput_sample.metadata,
          )
      )
    output_samples = samples + [throughput_sample] + latency_samples
    if benchmark_name.upper() == 'TCP_STREAM':
      output_samples.append(
          sample.Sample(
              throughput_sample.metric + '_1stream',
              throughput_sample.value,
              throughput_sample.unit,
              throughput_sample.metadata,
          )
      )
    return output_samples
  else:
    throughput_samples, _, latency_histograms = (
        list(t) for t in zip(*parsed_output)
    )
    throughput_unit = throughput_samples[0].unit

    if 'TCP' in benchmark_name.upper():
      netperf_retransmissions = 0
      netserver_retransmissions = 0
      netperf_mss = None
      for throughput_sample in throughput_samples:
        if throughput_sample.metadata['netserver_retransmissions'] == '-1':
          continue
        netperf_retransmissions += int(
            throughput_sample.metadata['netperf_retransmissions']
        )
        netserver_retransmissions += int(
            throughput_sample.metadata['netserver_retransmissions']
        )
        sample_netperf_mss = throughput_sample.metadata['netperf_mss']
        if netperf_mss is None:
          netperf_mss = sample_netperf_mss
        elif netperf_mss != sample_netperf_mss:
          raise ValueError(
              'Netperf MSS values do not match for multiple netperf threads.'
          )
      metadata['netperf_retransmissions'] = netperf_retransmissions
      metadata['netserver_retransmissions'] = netserver_retransmissions
      metadata['netperf_mss'] = netperf_mss if netperf_mss else 'unknown'

    throughputs = [s.value for s in throughput_samples]
    base_sample = sample.Sample(
        f'{benchmark_name}_Throughput_', 0, throughput_unit, metadata
    )
    samples.extend(
        sample.SummarizePercentiles(throughputs, base_sample, _SELECTED_METRICS)
    )
    if benchmark_name.upper() == 'TCP_STREAM':
      total_throughput = next(
          s.value
          for s in samples
          if s.metric == f'{benchmark_name}_Throughput_total'
      )
      samples.append(
          sample.Sample(
              f'{benchmark_name}_Throughput_{len(parsed_output)}streams',
              total_throughput,
              throughput_unit,
              metadata,
          )
      )
    if enable_latency_histograms:
      latency_histogram = collections.Counter()
      for histogram in latency_histograms:
        latency_histogram.update(histogram)
      hist_metadata = {'histogram': json.dumps(latency_histogram)}
      hist_metadata.update(metadata)
      samples.append(
          sample.Sample(
              f'{benchmark_name}_Latency_Histogram', 0, 'us', hist_metadata
          )
      )
      latency_stats = _HistogramStatsCalculator(latency_histogram)
      for stat, value in latency_stats.items():
        samples.append(
            sample.Sample(
                f'{benchmark_name}_Latency_{stat}', float(value), 'us', metadata
            )
        )
    return samples


def Run(benchmark_spec):
  """Executes netperf and netserver locally on the single VM in benchmark_spec."""
  vm = benchmark_spec.vms[0]
  return RunSingleVM(vm)


def RunSingleVM(vm, test_length=None):
  """Runs netperf locally against netserver running on loopback (127.0.0.1)."""
  run_test_length = test_length or FLAGS.netperf_test_length
  logging.info('netperf running locally on %s', vm)
  results = []
  metadata = {
      'sending_zone': vm.zone,
      'sending_machine_type': vm.machine_type,
      'receiving_zone': vm.zone,
      'receiving_machine_type': vm.machine_type,
      'ip_type': 'loopback',
  }

  target_ips = [LOOPBACK_IP]

  for num_streams in FLAGS.netperf_num_streams:
    assert num_streams >= 1

    for netperf_benchmark in FLAGS.netperf_benchmarks:
      if FLAGS.netperf_warmup_test_length > 0:
        logging.info(
            'Running warmup for %s for %d seconds',
            netperf_benchmark,
            FLAGS.netperf_warmup_test_length,
        )
        RunNetperf(
            vm,
            netperf_benchmark,
            vm.GetInternalIPs(), #target_ips,
            num_streams,
            vm.GetInternalIPs(), #target_ips,
            FLAGS.netperf_warmup_test_length,
        )
      loopback_results = RunNetperf(
          vm,
          netperf_benchmark,
          vm.GetInternalIPs(), #target_ips,
          num_streams,
          vm.GetInternalIPs(), #target_ips,
          run_test_length,
      )
      for loopback_result in loopback_results:
        loopback_result.metadata.update(metadata)
      results.extend(loopback_results)

  return results


def Cleanup(benchmark_spec):
  """Cleans up netperf and netserver on the single VM."""
  vm = benchmark_spec.vms[0]
  CleanupSingleVM(vm)


def CleanupSingleVM(vm):
  """Cleans up netserver processes and remote scripts on the VM."""
  vm.RemoteCommand('sudo killall netserver || true')
  vm.RemoteCommand(f'sudo rm -rf {REMOTE_SCRIPT}')
