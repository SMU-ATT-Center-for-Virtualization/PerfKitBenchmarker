# Copyright 2026 PerfKitBenchmarker Authors. All rights reserved.
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
"""Runs aggregate netperf from a GKE pod client to multiple GCE VM servers."""

import collections
import logging

from absl import flags
from perfkitbenchmarker import background_tasks
from perfkitbenchmarker import configs
from perfkitbenchmarker import errors
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_benchmarks import netperf_aggregate_benchmark
from perfkitbenchmarker.linux_benchmarks.kubernetes import (
    kubernetes_netperf_common,
)
from perfkitbenchmarker.resources.container_service import kubectl
from perfkitbenchmarker.resources.container_service import kubernetes_commands

FLAGS = flags.FLAGS

KUBERNETES_NETPERF_AGGREGATE_ENABLE_HOST_NETWORK = flags.DEFINE_boolean(
    'kubernetes_netperf_aggregate_enable_host_network',
    False,
    'Whether to set the GKE hostNetwork pod setting to True.',
)

BENCHMARK_NAME = 'kubernetes_netperf_aggregate'
BENCHMARK_CONFIG = """
kubernetes_netperf_aggregate:
  description: >
    Simultaneous aggregate netperf to multiple VM endpoints from a
    Kubernetes pod.
  vm_groups:
    servers:
      cloud: GCP
      vm_spec:
        GCP:
          machine_type: c4d-highcpu-96
          zone: us-central1-b
      vm_count: 2
  container_specs:
    netperf_client:
      image: netperf-k8s
  container_registry:
    cloud: GCP
    spec:
      GCP:
        zone: 'us-central1-b'
  container_cluster:
    cloud: GCP
    type: Kubernetes
    vm_count: 1
    vm_spec:
      GCP:
        machine_type: n4-standard-2
        zone: us-central1-b
    nodepools:
      netperf-client-pool:
        vm_count: 1
        vm_spec:
          GCP:
            machine_type: c4d-highcpu-192
            zone: us-central1-b
  flags:
    placement_group_style: closest_supported
"""

NETPERF_BASE_DIR = '/netperf-netperf-2.7.0'
NETPERF_EXAMPLES_DIR = f'{NETPERF_BASE_DIR}/doc/examples'

_INTERVAL_NAMING = collections.namedtuple(
    'IntervalNaming', 'output_file parse_name'
)
_BENCHMARK_INTERVAL_MAPPING = {
    'STREAM': _INTERVAL_NAMING('netperf_outbound', 'Outbound'),
    'MAERTS': _INTERVAL_NAMING('netperf_inbound', 'Inbound'),
    'RRAGG': _INTERVAL_NAMING('netperf_tps', 'Request/Response Aggregate'),
    'BIDIR': _INTERVAL_NAMING('netperf_bidirectional', 'Bidirectional'),
}


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def _RunInClientPod(command, timeout=None):
  kwargs = {'raise_on_failure': False}
  if timeout is not None:
    kwargs['timeout'] = timeout
  stdout, stderr, retcode = kubectl.RunKubectlCommand(
      ['exec', 'netperf-client', '--', '/bin/bash', '-c', command],
      **kwargs,
  )
  if retcode != 0:
    raise errors.Benchmarks.RunError(
        f'command failed in netperf-client pod: {command}\n{stderr}'
    )
  return stdout


def Prepare(benchmark_spec):
  server_vms = benchmark_spec.vm_groups['servers']

  logging.info('Preparing server VMs...')
  background_tasks.RunThreaded(
      netperf_aggregate_benchmark.PrepareNetperfAggregate, server_vms
  )

  image = benchmark_spec.container_specs['netperf_client'].image
  host_network_spec = ''
  if KUBERNETES_NETPERF_AGGREGATE_ENABLE_HOST_NETWORK.value:
    host_network_spec = """
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet"""

  node_selector_spec = """
  nodeSelector:
    cloud.google.com/gke-nodepool: netperf-client-pool"""

  pod_yaml = f"""
apiVersion: v1
kind: Pod
metadata:
  name: netperf-client
  labels:
    app: netperf-client
spec:{host_network_spec}{node_selector_spec}
  containers:
  - name: netperf
    image: {image}
    command: ["/bin/bash", "-c", "sleep infinity"]
"""

  logging.info('Deploying netperf client pod...')
  kubernetes_commands.CreateResource(pod_yaml)
  kubernetes_commands.WaitForResource(
      'pod/netperf-client', 'ready', timeout=300
  )

  for benchmark in FLAGS.netperf_aggregate_benchmarks:
    _RunInClientPod(
        f'sed -i "s/DO_{benchmark}=0;/DO_{benchmark}=1;/g" '
        f'{NETPERF_EXAMPLES_DIR}/runemomniaggdemo.sh'
    )


def _GetMetadata(benchmark_spec, server_vms):
  nodepool = kubernetes_netperf_common.GetNodePool(
      benchmark_spec, 'netperf-client-pool'
  )
  metadata = kubernetes_netperf_common.GetPodMetadata(
      benchmark_spec,
      'netperf-client-pool',
      server_environment='gce_vm',
      client_environment='kubernetes_pod',
      kubernetes_pod_host_network=(
          KUBERNETES_NETPERF_AGGREGATE_ENABLE_HOST_NETWORK.value
      ),
      server_count=len(server_vms),
      sending_zone=nodepool.zone,
      sending_machine_type=nodepool.machine_type,
      receiving_zone=server_vms[0].zone,
      receiving_machine_type=server_vms[0].machine_type,
  )
  metadata['ip_type'] = vm_util.IpAddressMetadata.INTERNAL
  return metadata


def Run(benchmark_spec):
  server_vms = benchmark_spec.vm_groups['servers']
  server_ips = []
  for vm in server_vms:
    server_ips.extend(vm.GetInternalIPs())

  logging.info('Setting up remote_hosts config inside the client pod...')
  setup_hosts_cmd = f'rm -f {NETPERF_EXAMPLES_DIR}/remote_hosts\n'
  for ip_num, ip in enumerate(server_ips):
    setup_hosts_cmd += (
        f"echo 'REMOTE_HOSTS[{ip_num}]={ip}' >> "
        f'{NETPERF_EXAMPLES_DIR}/remote_hosts\n'
    )
  setup_hosts_cmd += (
      f"echo 'NUM_REMOTE_HOSTS={len(server_ips)}' >> "
      f'{NETPERF_EXAMPLES_DIR}/remote_hosts\n'
  )
  _RunInClientPod(setup_hosts_cmd)

  logging.info('Running runemomniaggdemo.sh aggregate benchmark script...')
  _RunInClientPod(
      f'cd {NETPERF_EXAMPLES_DIR} && export PATH=$PATH:. && '
      'chmod +x runemomniaggdemo.sh && ./runemomniaggdemo.sh',
      timeout=1800,
  )

  metadata = _GetMetadata(benchmark_spec, server_vms)
  samples = []
  for benchmark in FLAGS.netperf_aggregate_benchmarks:
    naming = _BENCHMARK_INTERVAL_MAPPING[benchmark]
    proc_stdout = _RunInClientPod(
        f'cd {NETPERF_EXAMPLES_DIR} && python3 post_proc.py '
        f'--intervals {naming.output_file}.log'
    )
    _RunInClientPod(f'rm -f {NETPERF_EXAMPLES_DIR}/{naming.output_file}*')
    parsed_samples = netperf_aggregate_benchmark.ParseNetperfAggregateOutput(
        proc_stdout, naming.parse_name
    )
    for sample in parsed_samples:
      sample.metadata.update(metadata)
    samples.extend(parsed_samples)

  return samples


def Cleanup(benchmark_spec):
  kubernetes_commands.DeleteResource('pod/netperf-client')
  for vm in benchmark_spec.vm_groups['servers']:
    vm.RemoteCommand('sudo killall netserver', ignore_failure=True)
