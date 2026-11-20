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
"""Builds NIXLBench against an installed NIXL."""

import posixpath
import shlex

from absl import flags
from perfkitbenchmarker.linux_packages import nixl
from perfkitbenchmarker.linux_packages import ucx

FLAGS = flags.FLAGS

NIXLBENCH_INSTALL_PREFIX = flags.DEFINE_string(
    'nixlbench_install_prefix',
    '/usr/local/nixlbench',
    'Installation prefix for NIXLBench.',
)
NIXLBENCH_SRC_DIR = posixpath.join(nixl.NIXL_DIR, 'benchmark', 'nixlbench')

_BUILD_TIMEOUT = 60 * 60
_CONFIGURE_TIMEOUT = 60 * 10


def NixlbenchBin() -> str:
  """Returns the NIXLBench binary path."""
  return posixpath.join(NIXLBENCH_INSTALL_PREFIX.value, 'bin', 'nixlbench')


def _LibDirs() -> list[str]:
  """Returns NIXLBench library directories across common install layouts."""
  lib_dir = posixpath.join(NIXLBENCH_INSTALL_PREFIX.value, 'lib')
  return [
      lib_dir,
      posixpath.join(lib_dir, 'x86_64-linux-gnu'),
      posixpath.join(lib_dir, 'aarch64-linux-gnu'),
  ]


def Environment() -> str:
  """Returns shell environment needed to find NIXLBench libraries."""
  return (
      f'export LD_LIBRARY_PATH={":".join(_LibDirs())}:$LD_LIBRARY_PATH;'
  )


def _Install(vm) -> None:
  """Builds and installs NIXLBench."""
  vm.Install('build_tools')
  vm.Install('nixl')
  vm.InstallPackages(
      'cmake ninja-build meson pkg-config libgflags-dev libaio-dev '
      'liburing-dev libssl-dev libcurl4-openssl-dev uuid-dev zlib1g-dev'
  )

  nixlbench_src = NIXLBENCH_SRC_DIR
  vm.RemoteCommand(f'sudo rm -rf {nixlbench_src}/build')
  meson_args = [
      'meson',
      'setup',
      'build',
      f'--prefix={NIXLBENCH_INSTALL_PREFIX.value}',
      '--buildtype=release',
      f'-Dnixl_path={nixl.NIXL_INSTALL_PREFIX.value}',
      f'-Ducx_path={ucx.UCX_INSTALL_DIR}',
      '-Dbuild_tests=false',
  ]
  vm.RemoteCommand(
      f'cd {nixlbench_src} && {ucx.Environment()} '
      f'{nixl.Environment()} {shlex.join(meson_args)}',
      timeout=_CONFIGURE_TIMEOUT,
  )
  vm.RemoteCommand(
      f'cd {nixlbench_src}/build && ninja',
      timeout=_BUILD_TIMEOUT,
  )
  vm.RemoteCommand(f'cd {nixlbench_src}/build && sudo ninja install')
  vm.RemoteCommand('sudo ldconfig')
  vm.RemoteCommand(f'test -x {NixlbenchBin()}')


def AptInstall(vm) -> None:
  """Installs NIXLBench on the VM."""
  _Install(vm)


def YumInstall(vm) -> None:
  """Installs NIXLBench on the VM."""
  _Install(vm)


def GetVersion(vm) -> str:
  """Returns the NIXLBench source version."""
  stdout, _ = vm.RemoteCommand(
      f'git -C {nixl.NIXL_DIR} describe --tags --always'
  )
  return stdout.strip()


def Uninstall(vm) -> None:
  """Uninstalls NIXLBench from the VM."""
  vm.RemoteCommand(f'sudo rm -rf {NIXLBENCH_INSTALL_PREFIX.value}')
