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
"""Module containing AIPerf installation and cleanup functions."""

import posixpath

from absl import flags
from packaging import version
from perfkitbenchmarker import errors
from perfkitbenchmarker import linux_packages

AIPERF_VERSION = flags.DEFINE_string(
    'aiperf_version',
    '0.13.0',
    'Version of the AIPerf pip package to install.',
)

AIPERF_DIR = posixpath.join(linux_packages.INSTALL_DIR, 'aiperf-venv')
AIPERF_BIN = posixpath.join(AIPERF_DIR, 'bin', 'aiperf')
_AIPERF_MIN_PYTHON = version.Version('3.11')
_AIPERF_MAX_PYTHON = version.Version('3.14')


def _CheckPythonVersion(vm) -> None:
  """Checks that the VM has a Python version supported by AIPerf."""
  stdout, _ = vm.RemoteCommand('python3 --version')
  python_version = version.Version(stdout.strip().split()[-1])
  if not _AIPERF_MIN_PYTHON <= python_version < _AIPERF_MAX_PYTHON:
    raise errors.Benchmarks.PrepareException(
        'AIPerf requires Python >=3.11,<3.14. Found Python'
        f' {python_version} on the client VM.'
    )


def _Install(vm) -> None:
  """Installs AIPerf in a dedicated virtual environment."""
  vm.Install('pip')
  vm.InstallPackages('python3-venv')
  _CheckPythonVersion(vm)
  vm.RemoteCommand(f'sudo rm -rf {AIPERF_DIR}')
  vm.RemoteCommand(f'python3 -m venv {AIPERF_DIR}')
  vm.RemoteCommand(f'{AIPERF_DIR}/bin/pip install --upgrade pip')
  vm.RemoteCommand(
      f'{AIPERF_DIR}/bin/pip install aiperf=={AIPERF_VERSION.value}'
  )
  vm.RemoteCommand(f'{AIPERF_BIN} --version')


def AptInstall(vm) -> None:
  """Installs AIPerf on the VM."""
  _Install(vm)


def YumInstall(vm) -> None:
  """Installs AIPerf on the VM."""
  _Install(vm)


def GetVersion(vm) -> str:
  """Returns the version of AIPerf installed on the VM."""
  stdout, _ = vm.RemoteCommand(f'{AIPERF_BIN} --version')
  return stdout.strip()


def Uninstall(vm) -> None:
  """Uninstalls AIPerf from the VM."""
  vm.RemoteCommand(f'sudo rm -rf {AIPERF_DIR}')
