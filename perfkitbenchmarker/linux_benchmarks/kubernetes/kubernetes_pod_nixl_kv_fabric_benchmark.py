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
"""Runs the NIXL KV fabric benchmark between two GKE pods."""

import logging

from absl import flags
from perfkitbenchmarker import background_tasks
from perfkitbenchmarker import configs
from perfkitbenchmarker import errors
from perfkitbenchmarker import sample
from perfkitbenchmarker.linux_benchmarks import nixl_kv_fabric_common
from perfkitbenchmarker.linux_benchmarks.kubernetes import (
    kubernetes_nixl_common,
)
from perfkitbenchmarker.linux_packages import kvbench
from perfkitbenchmarker.linux_packages import nixl
from perfkitbenchmarker.linux_packages import nixlbench
from perfkitbenchmarker.linux_packages import ucx
from perfkitbenchmarker.providers.gcp import flags as gcp_flags
from perfkitbenchmarker.resources.container_service import kubernetes_commands

FLAGS = flags.FLAGS

KUBERNETES_NIXL_POD_HOST_NETWORK = flags.DEFINE_boolean(
    'kubernetes_nixl_pod_host_network',
    False,
    'Whether to run the NIXL pods with hostNetwork enabled.',
)

BENCHMARK_NAME = 'kubernetes_pod_nixl_kv_fabric'
BENCHMARK_CONFIG = """
kubernetes_pod_nixl_kv_fabric:
  description: >
    Run the NIXL KV cache fabric benchmark between two GKE pods.
  container_specs:
    nixl_worker:
      image: nixl-k8s
  container_registry:
    cloud: GCP
    spec:
      GCP:
        zone: 'us-east1-c'
  container_cluster:
    cloud: GCP
    type: Kubernetes
    vm_count: 1
    vm_spec:
      GCP:
        machine_type: n4-standard-2
        zone: us-east1-c
    nodepools:
      nixl-workers:
        vm_count: 2
        egress_bandwidth_tier: TIER_1
        vm_spec:
          GCP:
            machine_type: c3-highcpu-44
            zone: us-east1-c
"""

_TARGET_POD = 'nixl-target'
_INITIATOR_POD = 'nixl-initiator'
_NODEPOOL = 'nixl-workers'


def GetConfig(user_config):
  return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)


def CheckPrerequisites(benchmark_config) -> None:
  nixl_kv_fabric_common.CheckPrerequisites(benchmark_config)


def _PodManifest(image: str) -> str:
  """Returns the manifest for the target and initiator pods."""
  host_network_spec = ''
  if KUBERNETES_NIXL_POD_HOST_NETWORK.value:
    host_network_spec = """
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet"""

  node_selector_spec = f"""
  nodeSelector:
    cloud.google.com/gke-nodepool: {_NODEPOOL}"""

  affinity_spec = """
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
          - key: app
            operator: In
            values:
            - nixl-kv-fabric
        topologyKey: kubernetes.io/hostname"""

  return f"""
apiVersion: v1
kind: Pod
metadata:
  name: {_TARGET_POD}
  labels:
    app: nixl-kv-fabric
    role: nixl-target
spec:{host_network_spec}{node_selector_spec}{affinity_spec}
  containers:
  - name: nixl
    image: {image}
    command: ["/bin/bash", "-c", "sleep infinity"]
---
apiVersion: v1
kind: Pod
metadata:
  name: {_INITIATOR_POD}
  labels:
    app: nixl-kv-fabric
    role: nixl-initiator
spec:{host_network_spec}{node_selector_spec}{affinity_spec}
  containers:
  - name: nixl
    image: {image}
    command: ["/bin/bash", "-c", "sleep infinity"]
"""


def _GetPods() -> tuple[kubernetes_nixl_common.NixlPod, ...]:
  return (
      kubernetes_nixl_common.NixlPod(_TARGET_POD),
      kubernetes_nixl_common.NixlPod(_INITIATOR_POD),
  )


def _PreparePod(pod: kubernetes_nixl_common.NixlPod) -> None:
  """Verifies the NIXL image and stages the KVBench data files."""
  pod.RemoteCommand(f'mkdir -p {nixl_kv_fabric_common.REMOTE_DATA_DIR}')
  pod.PushDataFile(
      nixl_kv_fabric_common.MODEL_FILES[
          nixl_kv_fabric_common.MODEL.value
      ],
      nixl_kv_fabric_common.REMOTE_DATA_DIR,
  )
  pod.PushDataFile(
      nixl_kv_fabric_common.MODEL_CONFIG_FILES[
          nixl_kv_fabric_common.MODEL_CONFIG.value
      ],
      nixl_kv_fabric_common.REMOTE_DATA_DIR,
  )
  pod.RemoteCommand(f'test -x {nixlbench.NixlbenchBin()}')
  pod.RemoteCommand(f'test -x {kvbench.KVBENCH_BIN}')
  pod.RemoteCommand(f'test -f {kvbench.KVBENCH_DIR}/main.py')
  pod.RemoteCommand(
      'test -f '
      f'{nixl_kv_fabric_common.DataFile(nixl_kv_fabric_common.MODEL_FILES[
          nixl_kv_fabric_common.MODEL.value
      ])}'
  )
  pod.RemoteCommand(
      'test -f '
      f'{nixl_kv_fabric_common.DataFile(
          nixl_kv_fabric_common.MODEL_CONFIG_FILES[
              nixl_kv_fabric_common.MODEL_CONFIG.value
          ])}'
  )


def Prepare(benchmark_spec) -> None:
  """Creates the two worker pods and stages the benchmark data."""
  image = benchmark_spec.container_specs['nixl_worker'].image
  kubernetes_commands.CreateResource(_PodManifest(image))
  kubernetes_commands.WaitForResource(
      f'pod/{_TARGET_POD}', 'ready', timeout=600
  )
  kubernetes_commands.WaitForResource(
      f'pod/{_INITIATOR_POD}', 'ready', timeout=600
  )

  target, initiator = _GetPods()
  logging.info(
      'NIXL target pod %s on %s (%s), initiator pod %s on %s (%s)',
      target.pod_name,
      target.node_name,
      target.internal_ip,
      initiator.pod_name,
      initiator.node_name,
      initiator.internal_ip,
  )
  background_tasks.RunThreaded(_PreparePod, [target, initiator])


def _GetMetadata(
    benchmark_spec,
    target: kubernetes_nixl_common.NixlPod,
    initiator: kubernetes_nixl_common.NixlPod,
) -> dict[str, object]:
  """Returns GKE and pod metadata common to every sample."""
  cluster = benchmark_spec.container_cluster
  nodepool = cluster.nodepools.get(_NODEPOOL, cluster.default_nodepool)
  return {
      'environment': 'kubernetes_pod',
      'worker_machine_type': nodepool.machine_type,
      'nixl_version': nixl.NIXL_VERSION.value,
      'ucx_version': ucx.UCX_VERSION.value,
      'nixl_worker_count': 2,
      'nixl_worker_internal_ips': (
          f'{target.internal_ip},{initiator.internal_ip}'
      ),
      'nixl_image': benchmark_spec.container_specs['nixl_worker'].image,
      'gke_cluster_name': cluster.name,
      'gke_nodepool': nodepool.name,
      'gke_nodepool_egress_bandwidth_tier': getattr(
          nodepool, 'egress_bandwidth_tier', None
      ),
      'gke_node_machine_type': nodepool.machine_type,
      'gke_node_target': target.node_name,
      'gke_node_initiator': initiator.node_name,
      'gke_pod_target': target.pod_name,
      'gke_pod_initiator': initiator.pod_name,
      'gke_pod_ip_target': target.internal_ip,
      'gke_pod_ip_initiator': initiator.internal_ip,
      'gke_pod_host_network': KUBERNETES_NIXL_POD_HOST_NETWORK.value,
      'gke_pod_mtu': target.mtu,
      'gke_enable_dataplane_v2': any((
          gcp_flags.GKE_ENABLE_DATAPLANE_V2.value,
          cluster.enable_network_policy,
      )),
  }


def Run(benchmark_spec) -> list[sample.Sample]:
  """Runs KVBench-derived NIXLBench cases between the two pods."""
  target, initiator = _GetPods()
  model_path = nixl_kv_fabric_common.DataFile(
      nixl_kv_fabric_common.MODEL_FILES[nixl_kv_fabric_common.MODEL.value]
  )
  config_path = nixl_kv_fabric_common.DataFile(
      nixl_kv_fabric_common.MODEL_CONFIG_FILES[
          nixl_kv_fabric_common.MODEL_CONFIG.value
      ]
  )
  cases = nixl_kv_fabric_common.GetKvBenchCases(
      target, model_path, config_path
  )
  samples = nixl_kv_fabric_common.RunCases(
      target,
      initiator,
      cases,
      _GetMetadata(benchmark_spec, target, initiator),
  )
  if not samples:
    raise errors.Benchmarks.RunError('No NIXLBench samples were collected.')
  return samples


def Cleanup(benchmark_spec) -> None:
  """Deletes the NIXL pods."""
  del benchmark_spec
  kubernetes_commands.DeleteResource(f'pod/{_TARGET_POD}')
  kubernetes_commands.DeleteResource(f'pod/{_INITIATOR_POD}')
