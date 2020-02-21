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

"""MySQL Service Benchmarks.

This is a set of benchmarks that measures performance of MySQL Databases on
managed MySQL services.

- On AWS, we will use RDS+MySQL.
- On GCP, we will use Cloud SQL v2 (Performance Edition).

As other cloud providers deliver a managed MySQL service, we will add it here.

As of May 2017 to make this benchmark run for GCP you must install the
gcloud beta component. This is necessary because creating a Cloud SQL instance
with a non-default storage size is in beta right now. This can be removed when
this feature is part of the default components.
See https://cloud.google.com/sdk/gcloud/reference/beta/sql/instances/create
for more information.
To run this benchmark for GCP it is required to install a non-default gcloud
component. Otherwise this benchmark will fail.

To ensure that gcloud beta is installed, type
        'gcloud components list'
into the terminal. This will output all components and status of each.
Make sure that
  name: gcloud Beta Commands
  id:  beta
has status: Installed.
If not, run
        'gcloud components install beta'
to install it. This will allow this benchmark to properly create an instance.
"""

import logging
import re
import StringIO
import time

from perfkitbenchmarker import configs
from perfkitbenchmarker import flag_util
from perfkitbenchmarker import flags
from perfkitbenchmarker import publisher
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util


FLAGS = flags.FLAGS

# The default values for flags and BENCHMARK_CONFIG are not a recommended
# configuration for comparing sysbench performance.  Rather these values
# are set to provide a quick way to verify functionality is working.
# A broader set covering different permuations on much larger data sets
# is prefereable for comparison.

flag_util.DEFINE_integerlist(
    'sysbench_cpu_thread_counts',
    flag_util.IntegerList([8]),
    'array of thread counts passed to sysbench, one at a time',
    module_name=__name__)
flags.DEFINE_integer('sysbench_cpu_max_prime', 50000,
                     'Max prime to calculate')


BENCHMARK_NAME = 'sysbench_cpu'
BENCHMARK_CONFIG = """
sysbench_cpu:
  description: Sysbench CPU benchmarks.
  vm_groups:
    default:
      os_type: ubuntu1604
      vm_spec:
        GCP:
          machine_type: n1-standard-16
          zone: us-east4-a
        AWS:
          machine_type: db.m4.4xlarge
          zone: us-east-1a
"""

# Constants defined for Sysbench tests.


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def _ParseSysbenchOutput(sysbench_output):
  """Parses sysbench output.

  Extract relevant TPS and latency numbers, and populate the final result
  collection with these information.

  Specifically, we are interested in tps and latency numbers reported by each
  reporting interval.

  Args:
    sysbench_output: The output from sysbench.
  Returns:
    Three arrays, the tps, latency and qps numbers.

  """


#   Maximum prime number checked in CPU test: 50000
# Test execution summary:
#     total time:                          113.9137s
#     total number of events:              10000
#     total time taken by event execution: 113.9094
#     per-request statistics:
#          min:                                 11.11ms
#          avg:                                 11.39ms
#          max:                                 19.49ms
#          approx.  95 percentile:              11.68ms
# Threads fairness:
#     events (avg/stddev):           10000.0000/0.00
#     execution time (avg/stddev):   113.9094/0.00
  
  total_time = 0.0
  total_number_of_events = 0
  total_time_event_exec = 0.0
  per_request_min = 0.0
  per_request_avg = 0.0
  per_request_max = 0.0
  per_request_95p = 0.0


  sysbench_output_io = StringIO.StringIO(sysbench_output)
  for line in sysbench_output_io:
    # parse a line like (it's one line - broken up in the comment to fit):
    # [ 6s ] thds: 16 tps: 650.51 qps: 12938.26 (r/w/o: 9046.18/2592.05/1300.03)
    # lat (ms,99%): 40.37 err/s: 0.00 reconn/s: 0.00
    if re.search('total time: (.*?)', line):
      match = re.search('total time: (.*?)', line)
      total_time = match.group(1)
    elif re.search('total number of events: (.*?)', line):
      match = re.search('total number of events: (.*?)', line)
      total_number_of_events = match.group(1)
    elif re.search('min: (.*?)', line):
      match = re.search('min: (.*?)', line)
      per_request_min = match.group(1)
    elif re.search('avg: (.*?)', line):
      match = re.search('avg: (.*?)', line)
      per_request_avg = match.group(1)
    elif re.search('max: (.*?)', line):
      match = re.search('max: (.*?)', line)
      per_request_max = match.group(1)
    elif re.search('95 percentile: (.*?)', line):
      match = re.search('95 percentile: (.*?)', line)
      per_request_95p = match.group(1)
      # match = re.search('tps: (.*?) ', line)
      # tps_numbers.append(float(match.group(1)))
      # match = re.search(r'lat \(.*?\): (.*?) ', line)
      # latency_numbers.append(float(match.group(1)))
      # match = re.search(r'qps: (.*?) \(.*?\) ', line)
      # qps_numbers.append(float(match.group(1)))
      # if line.startswith('SQL statistics:'):
      #   break


  return total_time, total_number_of_events, per_request_min, per_request_avg, per_request_max, per_request_95p



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


def AddMetricsForSysbenchOutput(
    sysbench_output, results, metadata, metric_prefix=''):
  """Parses sysbench output.

  Extract relevant TPS and latency numbers, and populate the final result
  collection with these information.

  Specifically, we are interested in tps and latency numbers reported by each
  reporting interval.

  Args:
    sysbench_output: The output from sysbench.
    results: The dictionary to store results based on sysbench output.
    metadata: The metadata to be passed along to the Samples class.
    metric_prefix:  An optional prefix to append to each metric generated.
  """

  events_per_second = 0.0
  total_time = 0.0
  total_number_of_events = 0
  total_time_event_exec = 0.0
  per_request_min = 0.0
  per_request_avg = 0.0
  per_request_max = 0.0
  per_request_95p = 0.0


  sysbench_output_io = StringIO.StringIO(sysbench_output)
  for line in sysbench_output_io:
    # parse a line like (it's one line - broken up in the comment to fit):
    # [ 6s ] thds: 16 tps: 650.51 qps: 12938.26 (r/w/o: 9046.18/2592.05/1300.03)
    # lat (ms,99%): 40.37 err/s: 0.00 reconn/s: 0.00
    if re.search('events per second: (.*?)', line):
      l = line.split()
      events_per_second = float(l[len(l)-1])
    elif re.search('total time: (.*?)', line):
      l = line.split()
      total_time = float(l[len(l)-1][:-1])
    elif re.search('total number of events: (.*?)', line):
      l = line.split()
      total_number_of_events = float(l[len(l)-1])
    elif re.search('min: (.*?)', line):
      l = line.split()
      per_request_min = float(l[len(l)-1])
    elif re.search('avg: (.*?)', line):
      l = line.split()
      per_request_avg = float(l[len(l)-1])
    elif re.search('max: (.*?)', line):
      l = line.split()
      per_request_max = float(l[len(l)-1])
    elif re.search('95th percentile: (.*?)', line):
      l = line.split()
      per_request_95p = float(l[len(l)-1])

  print(total_time)
  # print(total_number_of_events)
  metadata.update({'total_time': total_time})
  metadata.update({'total_number_of_events': total_number_of_events})
  metadata.update({'per_request_min_ms': per_request_min})
  metadata.update({'per_request_max_ms': per_request_max})
  metadata.update({'per_request_avg_ms': per_request_avg})
  metadata.update({'per_request_95p_ms': per_request_95p})


#   CPU speed:
#     events per second:   101.14

# General statistics:
#     total time:                          10.0434s
#     total number of events:              1016

# Latency (ms):
#          min:                                   43.91
#          avg:                                   78.89
#          max:                                  121.86
#          95th percentile:                      101.13
#          sum:                                80155.27

# Threads fairness:
#     events (avg/stddev):           127.0000/0.00
#     execution time (avg/stddev):   10.0194/0.01




  events_per_second_sample = sample.Sample('events_per_second', events_per_second, 'events/sec', metadata)
  # min_sample = sample.Sample('per_request_min', per_request_min, 'ms', metadata)
  # max_sample = sample.Sample('per_request_avg', per_request_avg, 'ms', metadata)
  # total_time_sample = sample.Sample('per_request_max', per_request_max, 'ms', metadata)
  # total_time_sample = sample.Sample('per_request_95p', per_request_95p, 'ms', metadata)
  
  #return sample.Sample('Throughput', total_throughput, 'Mbits/sec', metadata)

  results.append(events_per_second_sample)


def _GetSysbenchCommand(benchmark_spec, sysbench_thread_count):
  """Returns the sysbench command as a string."""

  run_cmd_tokens = ['sysbench',
                    '--test=cpu',
                    '--cpu_max_prime=%d' % FLAGS.sysbench_cpu_max_prime,
                    '--threads=%d' % sysbench_thread_count,
                    'run']
  run_cmd = ' '.join(run_cmd_tokens)
  return run_cmd


def _IssueSysbenchCommand(vm, benchmark_spec, sysbench_thread_count):
  """Issues a sysbench run command given a vm and a duration.

      Does nothing if duration is <= 0

  Args:
    vm: The test VM to issue command to.
    duration: the duration of the sysbench run.
    benchmark_spec: The benchmark specification. Contains all data that is
                    required to run the benchmark.
    sysbench_thread_count: count of number of threads to use in --threads
                           parameter to sysbench

  Returns:
    stdout, stderr: the result of the command.
  """
  stdout = ''
  stderr = ''
  run_cmd = _GetSysbenchCommand(
      benchmark_spec,
      sysbench_thread_count)
  stdout, stderr = vm.RobustRemoteCommand(run_cmd)
  logging.info('Sysbench results: \n stdout is:\n%s\nstderr is\n%s',
               stdout, stderr)

  return stdout, stderr


def _IssueSysbenchCommandWithReturnCode(
    vm, benchmark_spec, sysbench_thread_count, show_results=True):
  """Run sysbench workload as specified by the benchmark_spec."""
  stdout = ''
  stderr = ''
  retcode = -1
  run_cmd = _GetSysbenchCommand(
      benchmark_spec,
      sysbench_thread_count)
  stdout, stderr, retcode = vm.RemoteCommandWithReturnCode(
      run_cmd,
      should_log=show_results,
      ignore_failure=True,
      suppress_warning=True)
  if show_results:
    logging.info('Sysbench results: \n stdout is:\n%s\nstderr is\n%s',
                 stdout, stderr)

  return stdout, stderr, retcode


def _RunSysbench(
    vm, metadata, benchmark_spec, sysbench_thread_count):
  """Runs the Sysbench OLTP test.

  Args:
    vm: The client VM that will issue the sysbench test.
    metadata: The PKB metadata to be passed along to the final results.
    benchmark_spec: The benchmark specification. Contains all data that is
                    required to run the benchmark.
    sysbench_thread_count: The number of client threads that will connect.

  Returns:
    Results: A list of results of this run.
  """
  results = []

  # Now run the sysbench OLTP test and parse the results.
  # First step is to run the test long enough to cover the warmup period
  # as requested by the caller. Second step is the 'real' run where the results
  # are parsed and reported.

  stdout, _ = _IssueSysbenchCommand(vm, benchmark_spec,
                        sysbench_thread_count)

  #stdout, _ = _IssueSysbenchCommand(vm, run_seconds, benchmark_spec,
  #                                  sysbench_thread_count)

  logging.info('\n Parsing Sysbench Results...\n')
  AddMetricsForSysbenchOutput(stdout, results, metadata)

  return results


def CreateMetadataFromFlags():
  """Create meta data with all flags for sysbench."""
  metadata = {
      'sysbench_testname': 'CPU',
      'sysbench_cpu_max_prime': FLAGS.sysbench_cpu_max_prime
  }
  return metadata


def UpdateBenchmarkSpecWithFlags(benchmark_spec):
  """Updates benchmark_spec with flags that are used in the run stage."""
  #benchmark_spec.tables = FLAGS.sysbench_tables
  #benchmark_spec.sysbench_table_size = FLAGS.sysbench_table_size
  pass

def Prepare(benchmark_spec):
  """Prepare the MySQL DB Instances, configures it.

     Prepare the client test VM, installs SysBench, configures it.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  # We would like to always cleanup server side states.
  # If we don't set this, our cleanup function will only be called when the VM
  # is static VM, but we have server side states to cleanup regardless of the
  # VM type.

  benchmark_spec.always_call_cleanup = True

  vm = benchmark_spec.vms[0]

  UpdateBenchmarkSpecWithFlags(benchmark_spec)

  # Setup common test tools required on the client VM
  vm.Install('sysbench1')



def Run(benchmark_spec):
  """Run the MySQL Service benchmark and publish results.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.

  Returns:
    Results.
  """
  logging.info('Starting SysBench tests')

  vm = benchmark_spec.vms[0]

  for thread_count in FLAGS.sysbench_cpu_thread_counts:
    metadata = CreateMetadataFromFlags()
    metadata['sysbench_thread_count'] = thread_count
    # The run phase is common across providers. The VMs[0] object contains all
    # information and states necessary to carry out the run.
    run_results = _RunSysbench(vm, metadata, benchmark_spec, thread_count)
    print run_results
    publisher.PublishRunStageSamples(benchmark_spec, run_results)

  # all results have already been published
  # database results take a long time to gather.  If later client counts
  # or failover tests fail, still want the data from the earlier tests.
  # so, results are published as they are found.
  return []


def Cleanup(benchmark_spec):
  """Clean up MySQL Service benchmark related states on server and client.

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  del benchmark_spec
