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
"""Shared helpers for Kubernetes/GKE NIXL benchmarks."""

import posixpath

from perfkitbenchmarker import data
from perfkitbenchmarker import errors
from perfkitbenchmarker.resources.container_service import kubectl
from perfkitbenchmarker.resources.container_service import kubernetes_commands


class NixlPod:
  """Adapts a Kubernetes pod to the VM command interface used by NIXLBench."""

  def __init__(self, pod_name: str):
    self.pod_name = pod_name
    self._internal_ip: str | None = None

  @property
  def internal_ip(self) -> str:
    """Returns the pod IP, resolving it lazily."""
    if self._internal_ip is None:
      self._internal_ip = kubernetes_commands.Get(
          'pod', self.pod_name, json_selector='.status.podIP'
      ).strip()
    return self._internal_ip

  @property
  def node_name(self) -> str:
    """Returns the node hosting the pod."""
    return kubernetes_commands.Get(
        'pod', self.pod_name, json_selector='.spec.nodeName'
    ).strip()

  @property
  def mtu(self) -> int:
    """Returns the MTU of the pod's primary network interface."""
    stdout, _ = self.RemoteCommand('cat /sys/class/net/eth0/mtu')
    return int(stdout.strip())

  def RemoteCommand(
      self,
      command: str,
      timeout: float | None = None,
      ignore_failure: bool = False,
      **unused_kwargs,
  ) -> tuple[str, str]:
    """Runs a command in the pod and returns its stdout and stderr."""
    del unused_kwargs
    stdout, stderr, retcode = kubectl.RunKubectlCommand(
        ['exec', self.pod_name, '--', '/bin/bash', '-c', command],
        timeout=timeout,
        raise_on_failure=False,
    )
    if retcode and not ignore_failure:
      raise errors.VirtualMachine.RemoteCommandError(
          f'Command failed in pod {self.pod_name}: {command}\n{stderr}'
      )
    return stdout, stderr

  def PushDataFile(self, name: str, remote_path: str) -> None:
    """Copies a PKB data file into the pod."""
    local_path = data.ResourcePath(name)
    remote_path = posixpath.join(
        remote_path, posixpath.basename(local_path)
    )
    _, stderr, retcode = kubectl.RunKubectlCommand(
        ['cp', local_path, f'{self.pod_name}:{remote_path}'],
        raise_on_failure=False,
    )
    if retcode:
      raise errors.VirtualMachine.RemoteCommandError(
          f'Failed to copy {name} to pod {self.pod_name}: {stderr}'
      )
