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
"""Installs NIXL KVBench in a virtual environment."""

import posixpath

from absl import flags
from perfkitbenchmarker import linux_packages
from perfkitbenchmarker.linux_packages import nixl

FLAGS = flags.FLAGS

KVBENCH_VENV = posixpath.join(linux_packages.INSTALL_DIR, 'kvbench-venv')
KVBENCH_BIN = posixpath.join(KVBENCH_VENV, 'bin', 'python')
KVBENCH_DIR = posixpath.join(nixl.NIXL_DIR, 'benchmark', 'kvbench')
KVBENCH_MAIN = posixpath.join(KVBENCH_DIR, 'main.py')

_INSTALL_TIMEOUT = 60 * 30


def _NixlPythonVersion() -> str:
  """Returns the PyPI-compatible NIXL version for the pinned tag."""
  return nixl.NIXL_VERSION.value.removeprefix('v')


def _Install(vm) -> None:
  """Installs KVBench and its Python dependencies."""
  vm.Install('pip')
  vm.InstallPackages('python3-venv')
  vm.RemoteCommand(f'sudo rm -rf {KVBENCH_VENV}')
  vm.RemoteCommand(f'python3 -m venv {KVBENCH_VENV}')
  vm.RemoteCommand(f'{KVBENCH_VENV}/bin/pip install --upgrade pip')
  # KVBench imports nixl.logging even for kvcache/plan. Install the CUDA
  # wheel without its heavyweight metadata dependencies; the benchmark only
  # uses the Python-side planning utilities.
  vm.RemoteCommand(
      f'{KVBENCH_VENV}/bin/pip install --no-deps'
      f' nixl=={_NixlPythonVersion()}'
      f' nixl-cu12=={_NixlPythonVersion()}',
      timeout=_INSTALL_TIMEOUT,
  )
  vm.RemoteCommand(
      f'{KVBENCH_VENV}/bin/pip install'
      " 'click==8.1.7' 'pyyaml>=6.0.2' 'etcd3>=0.12.0'"
      " 'tabulate==0.9.0' 'tqdm==4.66.5' 'numpy'",
      timeout=_INSTALL_TIMEOUT,
  )
  vm.RemoteCommand(
      f'{KVBENCH_VENV}/bin/pip install --no-deps -e {KVBENCH_DIR}',
      timeout=_INSTALL_TIMEOUT,
  )
  vm.RemoteCommand(f'test -f {KVBENCH_MAIN}')
  vm.RemoteCommand(f'cd {KVBENCH_DIR} && {KVBENCH_BIN} main.py --help')


def AptInstall(vm) -> None:
  """Installs KVBench on the VM."""
  _Install(vm)


def YumInstall(vm) -> None:
  """Installs KVBench on the VM."""
  _Install(vm)


def GetVersion(vm) -> str:
  """Returns the KVBench source version."""
  stdout, _ = vm.RemoteCommand(
      f'git -C {nixl.NIXL_DIR} describe --tags --always'
  )
  return stdout.strip()


def Uninstall(vm) -> None:
  """Uninstalls KVBench from the VM."""
  vm.RemoteCommand(f'sudo rm -rf {KVBENCH_VENV}')
