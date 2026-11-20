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
"""Installs UCX from source for NIXL's TCP transport."""

import posixpath
import shlex

from absl import flags
from perfkitbenchmarker import linux_packages

FLAGS = flags.FLAGS

UCX_VERSION = flags.DEFINE_string(
    'ucx_version',
    'v1.22.0',
    'Git tag or commit of UCX to build.',
)
UCX_REPO = 'https://github.com/openucx/ucx.git'
UCX_DIR = posixpath.join(linux_packages.INSTALL_DIR, 'ucx')
UCX_INSTALL_DIR = '/usr/local/ucx'
UCX_INFO_BIN = posixpath.join(UCX_INSTALL_DIR, 'bin', 'ucx_info')
UCX_PKG_CONFIG_DIR = posixpath.join(UCX_INSTALL_DIR, 'lib', 'pkgconfig')
UCX_LIB_DIR = posixpath.join(UCX_INSTALL_DIR, 'lib')

_BUILD_TIMEOUT = 60 * 60
_CONFIGURE_TIMEOUT = 60 * 10


def _Install(vm) -> None:
  """Builds and installs UCX from source."""
  vm.Install('build_tools')
  vm.Install('curl')
  vm.InstallPackages(
      'autoconf automake libtool pkg-config libnuma-dev '
      'libibverbs-dev librdmacm-dev rdma-core libssl-dev'
  )

  vm.RemoteCommand(f'sudo rm -rf {UCX_DIR}')
  vm.RemoteCommand(
      'git clone --depth 1 --branch'
      f' {shlex.quote(UCX_VERSION.value)} {UCX_REPO} {UCX_DIR}',
      timeout=600,
  )
  vm.RemoteCommand(
      f'cd {UCX_DIR} && ./autogen.sh',
      timeout=_CONFIGURE_TIMEOUT,
  )
  vm.RemoteCommand(
      f'cd {UCX_DIR} && ./contrib/configure-release'
      f' --prefix={UCX_INSTALL_DIR} --enable-mt --with-verbs --without-cuda',
      timeout=_CONFIGURE_TIMEOUT,
  )
  vm.RemoteCommand(
      f'cd {UCX_DIR} && make -j"$(nproc)"',
      timeout=_BUILD_TIMEOUT,
  )
  vm.RemoteCommand(f'cd {UCX_DIR} && sudo make install')
  vm.RemoteCommand('sudo ldconfig')
  vm.RemoteCommand(f'test -x {UCX_INFO_BIN}')
  vm.RemoteCommand(f'{UCX_INFO_BIN} -d')


def AptInstall(vm) -> None:
  """Installs UCX on the VM."""
  _Install(vm)


def YumInstall(vm) -> None:
  """Installs UCX on the VM."""
  _Install(vm)


def GetVersion(vm) -> str:
  """Returns the installed UCX version."""
  stdout, _ = vm.RemoteCommand(f'{UCX_INFO_BIN} -v')
  return stdout.strip()


def Environment() -> str:
  """Returns shell environment needed to find the UCX installation."""
  return (
      f'export PKG_CONFIG_PATH={UCX_PKG_CONFIG_DIR}:$PKG_CONFIG_PATH; '
      f'export LD_LIBRARY_PATH={UCX_LIB_DIR}:$LD_LIBRARY_PATH;'
  )


def Uninstall(vm) -> None:
  """Uninstalls UCX from the VM."""
  vm.RemoteCommand(f'sudo rm -rf {UCX_DIR} {UCX_INSTALL_DIR}')
