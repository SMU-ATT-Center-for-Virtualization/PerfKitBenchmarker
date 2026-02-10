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

"""Schedules ping runs across all VM pairs.
"""

import logging
import re
from absl import flags
from perfkitbenchmarker import configs
from perfkitbenchmarker import sample
from perfkitbenchmarker import vm_util

import itertools
import queue
import threading
import time

FLAGS = flags.FLAGS


BENCHMARK_NAME = 'ping_scheduler'
BENCHMARK_CONFIG = """
ping_scheduler:
  description: Benchmarks ping latency over a set of VMs
  vm_groups:
    vm_1:
      vm_spec: *default_dual_core
    vm_2:
      vm_spec: *default_dual_core
    vm_3:
      vm_spec: *default_dual_core
    vm_4:
      vm_spec: *default_dual_core
"""

METRICS = ('Min Latency', 'Average Latency', 'Max Latency', 'Latency Std Dev')


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)

'''
    benchmark_spec.vm_groups => dict[group_name] = vms
'''

def Prepare(benchmark_spec):
    """Check that at least three VM groups are specified."""
    if len(benchmark_spec.vm_groups) <= 2:
        raise ValueError(
            'Scheduled Ping benchmark requires more than two VM groups, '
            f'found {len(benchmark_spec.vm_groups)}'
        )

    if vm_util.ShouldRunOnExternalIpAddress():
        for vm in benchmark_spec.vms:
            vm.AllowIcmp()

'''
    metaregions: (northamerica, us, southamerica, europe, africa, me, asia, australia)
    metaregion_rules : list of tuples => (metaregion_a, metaregion_b, max_tests, current_tests)
'''
def Run(benchmark_spec):
    # list of sender-receiver pairs which have not been attempted yet 
    s_r_pairs = list(itertools.combinations(benchmark_spec.vm_groups.keys(), 2))
    s_r_pairs_rev = [(r, s) for (s, r) in s_r_pairs]
    s_r_pairs += s_r_pairs_rev
    logging.info(f'Ping Run Start - s_r_pairs: {s_r_pairs}')

    all_mr = ['africa', 'australia', 'asia', 'europe', 'me', 'northamerica', 'southamerica', 'us']
    mr_pairs = list(itertools.combinations(all_mr, 2))
    mr_restrictions = [ [s, r, 2, 0] for (s, r) in mr_pairs]
    for mr in all_mr:
        mr_restrictions.append( [mr, mr, 4, 0] )
    logging.info(f'Metaregion Restrictions Rules: {mr_restrictions}')

    # dict to keep track of which vms are currently running a benchmark
    # False => not busy, True => busy
    # True/False mirror of benchmark_spec.vm_groups[group_name] => vms (list)
    busy_vms = dict.fromkeys(benchmark_spec.vm_groups.keys(), [])
    for group in benchmark_spec.vm_groups.keys():
        busy_vms[group] = [False for i in range(len(benchmark_spec.vm_groups[group]))]
    logging.info(f'Ping Run Start - busy_vms: {busy_vms}')

    results = queue.Queue()
    vms_to_free = queue.Queue()
    procs = [None for i in range(16)] #TODO: parameterize num of procs for parallel runs

    while (len(s_r_pairs) > 0):
        for ind in range(len(procs)):
            if procs[ind] is not None and not procs[ind].is_alive(): #'cleanup' finished runs
                procs[ind].join() # shouldn't block
                vm_pair = vms_to_free.get() # s_vm_group, s_i, r_vm_group, r_i
                busy_vms[vm_pair[0]][vm_pair[1]] = False
                busy_vms[vm_pair[2]][vm_pair[3]] = False
                procs[ind] = None

                s_vm = benchmark_spec.vm_groups[vm_pair[0]][vm_pair[1]]
                r_vm = benchmark_spec.vm_groups[vm_pair[2]][vm_pair[3]]
                _update_metaregion_rules(s_vm, r_vm, -1, mr_restrictions)

                logging.info(f'Cleanup: {vm_pair}')
                logging.info(f'Remaining s_r_pairs: {s_r_pairs}')
                logging.info(f'Procs: {procs}')
                logging.info(f'Updated Metaregion Rules: {mr_restrictions}')

            if procs[ind] is None: #start new runs
                s_r_list = _GetRun(s_r_pairs, busy_vms)

                for s_r in s_r_list:
                    if _check_mr_violation(benchmark_spec, s_r, mr_restrictions):
                        (s_i, r_i) = _PrepareRun(s_r, s_r_pairs, busy_vms)
                        s_vm = benchmark_spec.vm_groups[s_r[0]][s_i]
                        r_vm = benchmark_spec.vm_groups[s_r[1]][r_i]

                        _update_metaregion_rules(s_vm, r_vm, 1, mr_restrictions)

                        vm_pair = (s_r[0], s_i, s_r[1], r_i)
                        procs[ind] = threading.Thread(target = _RunPing, args=(results, vms_to_free, s_vm, r_vm, vm_pair))
                        procs[ind].start()
                        logging.info(f"Staring: {vm_pair}")
                        logging.info(f"Updated Metaregion Rules: {mr_restrictions}")
                        break

        time.sleep(5)

    logging.info(f'Exited loop, wrapping up all procs')
    for ind in range(len(procs)):
        if procs[ind] is not None:
            procs[ind].join()

    flattened_results = []
    for subl in list(results.queue):
        flattened_results.extend(subl)

    return flattened_results

def _check_mr_violation(benchmark_spec, s_r, mr_restrictions):
    mr_0 = benchmark_spec.vm_groups[s_r[0]][0].zone.split('-')[0]
    mr_1 = benchmark_spec.vm_groups[s_r[1]][0].zone.split('-')[0]
    for rule in mr_restrictions:
        if (mr_0 == rule[0] and mr_1 == rule[1]) or (mr_0 == rule[1] and mr_1 == rule[0]):
            return True if rule[3] < rule[2] else False
    return True

def _update_metaregion_rules(s_vm, r_vm, val, mr_restrictions):
    mr_0 = s_vm.zone.split('-')[0]
    mr_1 = r_vm.zone.split('-')[0]
    for ind in range(len(mr_restrictions)):
        rule = mr_restrictions[ind]
        if (mr_0 == rule[0] and mr_1 == rule[1]) or (mr_0 == rule[1] and mr_1 == rule[0]):
            mr_restrictions[ind][3] += val
            return True
    return False

# get list of s_r_pairs to run - TODO: refactor later
def _GetRun(s_r_pairs, busy_vms):
    res = []
    for (s, r) in s_r_pairs:
        if False in busy_vms[s] and False in busy_vms[r]:
            res.append( (s, r) )
    return res

'''
# get a s_r pair to run, basic greedy strategy
def _GetRun(s_r_pairs, busy_vms):
    for (s, r) in s_r_pairs:
        if False in busy_vms[s] and False in busy_vms[r]:
            return (s, r)
    return None
'''

# given s_r, remove from s_r_pairs todo-list and mark corresponding vms as busy
def _PrepareRun(s_r, s_r_pairs, busy_vms):
    s_r_pairs.remove(s_r)
    # get index of first avilable VMs in each vm-group
    s_i = busy_vms[s_r[0]].index(False)
    r_i = busy_vms[s_r[1]].index(False)
    # mark as busy and return indices
    busy_vms[s_r[0]][s_i] = True
    busy_vms[s_r[1]][r_i] = True
    return (s_i, r_i)

# modified version of _RunPing : place results on queue for use with multiple concurrent threads
def _RunPing(results_q, vm_free_q, sending_vm, receiving_vm, vm_pair):
    results = []

    if vm_util.ShouldRunOnInternalIpAddress(sending_vm, receiving_vm):
        ping_cmd = f'ping -c 100 {receiving_vm.internal_ip}'
        stdout, _ = sending_vm.RemoteCommand(ping_cmd)
        stats = re.findall('([0-9]*\\.[0-9]*)', stdout.splitlines()[-1])
        assert len(stats) == len(METRICS), stats

        metadata = {
            'ip_type': vm_util.IpAddressMetadata.INTERNAL,
            'receiving_zone': receiving_vm.zone,
            'sending_zone': sending_vm.zone,
        }

        for i, metric in enumerate(METRICS):
            results.append(sample.Sample(metric, float(stats[i]), 'ms', metadata))

    if vm_util.ShouldRunOnExternalIpAddress():
        ping_cmd = f'ping -c 100 {receiving_vm.ip_address}'
        stdout, _ = sending_vm.RemoteCommand(ping_cmd)
        stats = re.findall('([0-9]*\\.[0-9]*)', stdout.splitlines()[-1])
        assert len(stats) == len(METRICS), stats

        metadata = {
            'ip_type': vm_util.IpAddressMetadata.EXTERNAL,
            'receiving_zone': receiving_vm.zone,
            'sending_zone': sending_vm.zone,
        }

        for i, metric in enumerate(METRICS):
            results.append(sample.Sample(metric, float(stats[i]), 'ms', metadata))

    results_q.put(results)
    vm_free_q.put(vm_pair)

def Cleanup(benchmark_spec):  # pylint: disable=unused-argument
  """Cleanup ping on the target vm (by uninstalling).

  Args:
    benchmark_spec: The benchmark specification. Contains all data that is
      required to run the benchmark.
  """
  pass
