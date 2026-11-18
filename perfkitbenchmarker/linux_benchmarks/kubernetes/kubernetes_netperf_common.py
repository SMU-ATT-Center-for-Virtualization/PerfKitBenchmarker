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
"""Shared helpers for Kubernetes/GKE netperf benchmarks."""

from absl import flags
from perfkitbenchmarker import errors
from perfkitbenchmarker.providers.gcp import flags as gcp_flags
from perfkitbenchmarker.resources.container_service import kubectl
from perfkitbenchmarker.resources.container_service import kubernetes_commands

FLAGS = flags.FLAGS


class PodCommandRunner:
  """Adapts a Kubernetes pod to the VM command interface used by netperf."""

  def __init__(self, pod_name: str):
    self.pod_name = pod_name

  def RobustRemoteCommand(
      self,
      command: str,
      timeout: float | None = None,
      ignore_failure: bool = False,
  ) -> tuple[str, str]:
    """Runs a command in the pod and returns its stdout and stderr."""
    stdout, stderr, retcode = kubectl.RunKubectlCommand(
        ['exec', self.pod_name, '--', '/bin/bash', '-c', command],
        timeout=timeout,
        raise_on_failure=False,
    )
    if retcode and not ignore_failure:
      raise errors.Benchmarks.RunError(
          f'Command failed in pod {self.pod_name}: {command}\n{stderr}'
      )
    return stdout, stderr


def GetMaxStreams() -> int:
  """Returns the largest configured netperf stream count."""
  return max(FLAGS.netperf_num_streams)


def NetserverEnvYaml(indent: str = '    ') -> str:
  """Returns the pod env block needed by the netperf-k8s entrypoint."""
  return (
      f'{indent}env:\n'
      f'{indent}- name: NETPERF_PORT_START\n'
      f'{indent}  value: "{FLAGS.netperf_port_start}"\n'
      f'{indent}- name: NETPERF_MAX_STREAMS\n'
      f'{indent}  value: "{GetMaxStreams()}"\n'
  )


def GetPodIp(pod_name: str) -> str:
  """Returns the pod IP for a running pod."""
  return kubernetes_commands.Get(
      'pod', pod_name, json_selector='.status.podIP'
  ).strip()


def GetNodePool(benchmark_spec, nodepool_name: str):
  """Returns a nodepool config, falling back to the default nodepool."""
  cluster = benchmark_spec.container_cluster
  return cluster.nodepools.get(nodepool_name, cluster.default_nodepool)


def GetPodMetadata(
    benchmark_spec,
    nodepool_name: str,
    *,
    server_environment: str,
    client_environment: str,
    **extra,
) -> dict[str, object]:
  """Returns common metadata for a netperf pod benchmark."""
  nodepool = GetNodePool(benchmark_spec, nodepool_name)
  metadata = {
      'server_environment': server_environment,
      'client_environment': client_environment,
      'nodepool': nodepool.name,
      'nodepool_zone': nodepool.zone,
      'nodepool_machine_type': nodepool.machine_type,
      'nodepool_node_group': nodepool.node_group,
      'gke_enable_dataplane_v2': any((
          gcp_flags.GKE_ENABLE_DATAPLANE_V2.value,
          benchmark_spec.container_cluster.enable_network_policy,
      )),
      'gce_node_group': gcp_flags.GCE_NODE_GROUP.value,
  }
  metadata.update(extra)
  return metadata
