# Copyright 2020 PerfKitBenchmarker Authors. All rights reserved.
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
import re


# flags.DEFINE_boolean('ping_also_run_using_external_ip', False,
#                      'If set to True, the ping command will also be executed '
#                      'using the external ips of the vms.')


#TODO change these flags to be specific to this benchmark
#also uses flags
#FLAGS.iperf_sending_thread_count
#FLAGS.iperf_runtime_in_seconds

FLAGS = flags.FLAGS


BENCHMARK_NAME = 'vm_setup'
BENCHMARK_CONFIG = """
vm_setup:
  description: setup vms
  vm_groups:
    vm_1:
      vm_spec: *default_single_core
      vm_count: 1

"""

    # vm_2:
    #   vm_spec: *default_single_core
    #   vm_count: 0
    # vm_3:
    #   vm_spec: *default_single_core
    #   vm_count: 0
    # vm_4:
    #   vm_spec: *default_single_core
    #   vm_count: 0
    # vm_5:
    #   vm_spec: *default_single_core
    #   vm_count: 0
    # vm_6:
    #   vm_spec: *default_single_core
    #   vm_count: 0
    # vm_7:
    #   vm_spec: *default_single_core
    #   vm_count: 0

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
  print("PKB_VM_INFORMATION")
  print("INTERNAL_IP: " + vms[0].internal_ip)
  print("EXTERNAL_IP: " + vms[0].ip_address)
  print("NAME: " + vms[0].name)
  print("RUN_URI: " + FLAGS.run_uri)
  print("UID: " + benchmark_spec.uid)

def Run(benchmark_spec):
  results = ""
  return results


def Cleanup(benchmark_spec):  # pylint: disable=unused-argument
  """Cleanup ping on the target vm (by uninstalling).

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
        required to run the benchmark.
  """
  pass