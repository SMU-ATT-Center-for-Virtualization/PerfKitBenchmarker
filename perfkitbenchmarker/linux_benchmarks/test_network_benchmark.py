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
from perfkitbenchmarker import flag_util
from perfkitbenchmarker import vm_util
import re
import time

flags.DEFINE_integer('test_network_duration', 60,
                     'Duration to sleep for test')
FLAGS = flags.FLAGS


BENCHMARK_NAME = 'test_network'
BENCHMARK_CONFIG = """
test_network:
  description: sleep for specified amount of time
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
    vm_2:
      vm_spec: *default_single_core
"""

METRICS = ('Min Latency', 'Average Latency', 'Max Latency', 'Latency Std Dev')


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):  # pylint: disable=unused-argument
  """ Installs iperf
  """
  vms = benchmark_spec.vms

  for vm in vms:
    vm.Install('iperf')

def Run(benchmark_spec):
  """ run _RunTest
  """
  vms = benchmark_spec.vms
  results = []
  results = results + _RunTest()
  return results


def _RunTest():
  """Sleep and return fake data
  """
  logging.info("SLEEP TIME")
  time.sleep(FLAGS.test_network_duration)
  results = []
  metadata = {'meta': 'data',
              'test': 'test'}
  results.append(sample.Sample('test_metric', float(0), 'test_unit', metadata))

  return results


def Cleanup(benchmark_spec):  # pylint: disable=unused-argument
  """Cleanup ping on the target vm (by uninstalling).

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  pass
