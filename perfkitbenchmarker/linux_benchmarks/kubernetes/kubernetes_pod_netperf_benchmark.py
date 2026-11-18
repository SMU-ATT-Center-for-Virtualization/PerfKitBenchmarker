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
"""Runs netperf between two Kubernetes pods in the same node pool."""

import logging
import os

from absl import flags
from perfkitbenchmarker import configs
from perfkitbenchmarker import data
from perfkitbenchmarker import errors
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_benchmarks import netperf_benchmark
from perfkitbenchmarker.linux_benchmarks.kubernetes import (
    kubernetes_netperf_common,
)
from perfkitbenchmarker.resources.container_service import kubectl
from perfkitbenchmarker.resources.container_service import kubernetes_commands

FLAGS = flags.FLAGS

KUBERNETES_POD_NETPERF_ENABLE_HOST_NETWORK = flags.DEFINE_boolean(
    'kubernetes_pod_netperf_enable_host_network',
    False,
    'Whether to set the GKE hostNetwork pod setting to True.',
)

KUBERNETES_POD_NETPERF_SAME_NODE = flags.DEFINE_boolean(
    'kubernetes_pod_netperf_same_node',
    False,
    'Whether to force the client and server pods onto the same node using '
    'podAffinity. If False, forces pods onto different nodes using '
    'podAntiAffinity.',
)

BENCHMARK_NAME = 'kubernetes_pod_netperf'
BENCHMARK_CONFIG = """
kubernetes_pod_netperf:
  description: >
    Run netperf between a client Pod and a server Pod in the same
    nodepool.
  container_specs:
    netperf_server:
      image: netperf-k8s
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
      netperf-pool:
        vm_count: 2
        vm_spec:
          GCP:
            machine_type: c4d-highcpu-8
            zone: us-central1-b
"""


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def Prepare(benchmark_spec):
  if all((
      KUBERNETES_POD_NETPERF_ENABLE_HOST_NETWORK.value,
      KUBERNETES_POD_NETPERF_SAME_NODE.value,
  )):
    raise errors.Config.InvalidValue(
        'kubernetes_pod_netperf_enable_host_network and '
        'kubernetes_pod_netperf_same_node cannot both be True; the pods would '
        'collide on the same host ports.'
    )

  server_image = benchmark_spec.container_specs['netperf_server'].image
  client_image = benchmark_spec.container_specs['netperf_client'].image

  host_network_spec = ''
  if KUBERNETES_POD_NETPERF_ENABLE_HOST_NETWORK.value:
    host_network_spec = """
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet"""

  node_selector_spec = """
  nodeSelector:
    cloud.google.com/gke-nodepool: netperf-pool"""

  if KUBERNETES_POD_NETPERF_SAME_NODE.value:
    affinity_spec = """
  affinity:
    podAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: role
            operator: In
            values:
            - netperf-server
        topologyKey: kubernetes.io/hostname"""
  else:
    affinity_spec = """
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: role
            operator: In
            values:
            - netperf-server
        topologyKey: kubernetes.io/hostname"""

  pod_yaml = f"""
apiVersion: v1
kind: Pod
metadata:
  name: netperf-server
  labels:
    app: netperf-server
    role: netperf-server
spec:{host_network_spec}{node_selector_spec}
  containers:
  - name: netperf
    image: {server_image}
{kubernetes_netperf_common.NetserverEnvYaml()}
---
apiVersion: v1
kind: Pod
metadata:
  name: netperf-client
  labels:
    app: netperf-client
spec:{host_network_spec}{affinity_spec}{node_selector_spec}
  containers:
  - name: netperf
    image: {client_image}
    command: ["/bin/bash", "-c", "sleep infinity"]
"""

  logging.info('Deploying netperf client and server pods.')
  kubernetes_commands.CreateResource(pod_yaml)

  logging.info('Waiting for netperf pods to be ready...')
  kubernetes_commands.WaitForResource(
      'pod/netperf-server', 'ready', timeout=300
  )
  kubernetes_commands.WaitForResource(
      'pod/netperf-client', 'ready', timeout=300
  )

  benchmark_spec.server_pod_ip = kubernetes_netperf_common.GetPodIp(
      'netperf-server'
  )
  logging.info('Netperf server pod IP: %s', benchmark_spec.server_pod_ip)

  script_path = data.ResourcePath(
      os.path.join(
          netperf_benchmark.REMOTE_SCRIPTS_DIR,
          netperf_benchmark.REMOTE_SCRIPT,
      )
  )
  kubectl.RunKubectlCommand([
      'cp',
      script_path,
      f'netperf-client:/{netperf_benchmark.REMOTE_SCRIPT}',
  ])
  kubectl.RunKubectlCommand([
      'exec',
      'netperf-client',
      '--',
      'chmod',
      '+x',
      f'/{netperf_benchmark.REMOTE_SCRIPT}',
  ])


def _GetMetadata(benchmark_spec):
  nodepool = kubernetes_netperf_common.GetNodePool(
      benchmark_spec, 'netperf-pool'
  )
  return kubernetes_netperf_common.GetPodMetadata(
      benchmark_spec,
      'netperf-pool',
      server_environment='kubernetes_pod',
      client_environment='kubernetes_pod',
      kubernetes_pod_host_network=(
          KUBERNETES_POD_NETPERF_ENABLE_HOST_NETWORK.value
      ),
      kubernetes_pod_same_node=KUBERNETES_POD_NETPERF_SAME_NODE.value,
      sending_zone=nodepool.zone,
      receiving_zone=nodepool.zone,
      sending_machine_type=nodepool.machine_type,
      receiving_machine_type=nodepool.machine_type,
  )


def Run(benchmark_spec):
  server_ip = benchmark_spec.server_pod_ip
  client_ip = kubernetes_netperf_common.GetPodIp('netperf-client')
  runner = kubernetes_netperf_common.PodCommandRunner('netperf-client')
  results = []
  metadata = _GetMetadata(benchmark_spec)

  for num_streams in FLAGS.netperf_num_streams:
    for benchmark_name in FLAGS.netperf_benchmarks:
      if FLAGS.netperf_warmup_test_length > 0:
        logging.info(
            'Running warmup for %s for %d seconds',
            benchmark_name,
            FLAGS.netperf_warmup_test_length,
        )
        netperf_benchmark.RunNetperf(
            runner,
            benchmark_name,
            [server_ip],
            num_streams,
            [client_ip],
            FLAGS.netperf_warmup_test_length,
        )

      samples = netperf_benchmark.RunNetperf(
          runner,
          benchmark_name,
          [server_ip],
          num_streams,
          [client_ip],
          FLAGS.netperf_test_length,
      )
      for sample in samples:
        sample.metadata.update(metadata)
        sample.metadata['ip_type'] = vm_util.IpAddressMetadata.INTERNAL
      results.extend(samples)

  return results


def Cleanup(benchmark_spec):
  del benchmark_spec
  kubernetes_commands.DeleteResource('pod/netperf-server')
  kubernetes_commands.DeleteResource('pod/netperf-client')
