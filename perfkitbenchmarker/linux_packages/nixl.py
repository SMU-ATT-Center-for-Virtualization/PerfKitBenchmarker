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
"""Installs NVIDIA NIXL from source."""

import posixpath
import shlex

from absl import flags
from perfkitbenchmarker import linux_packages
from perfkitbenchmarker.linux_packages import ucx

FLAGS = flags.FLAGS

NIXL_VERSION = flags.DEFINE_string(
    'nixl_version',
    'v1.5.2',
    'Git tag or commit of NIXL to build.',
)
NIXL_REPO = 'https://github.com/ai-dynamo/nixl.git'
NIXL_DIR = posixpath.join(linux_packages.INSTALL_DIR, 'nixl')
NIXL_INSTALL_PREFIX = flags.DEFINE_string(
    'nixl_install_prefix',
    '/usr/local/nixl',
    'Installation prefix for NIXL.',
)
NIXL_ENABLE_PLUGINS = flags.DEFINE_string(
    'nixl_enable_plugins',
    'UCX',
    'Comma-separated NIXL plugins to enable.',
)
NIXL_CUDA_STUB_DIR = flags.DEFINE_string(
    'nixl_cuda_stub_dir',
    '',
    'Optional directory containing CUDA stub include/lib trees. Empty means '
    'build without CUDA.',
)

_BUILD_TIMEOUT = 60 * 60
_CONFIGURE_TIMEOUT = 60 * 10


def NixlLibDir() -> str:
  """Returns the NIXL library directory."""
  return posixpath.join(NIXL_INSTALL_PREFIX.value, 'lib')


def _LibDirs() -> list[str]:
  """Returns NIXL library directories across common install layouts."""
  lib_dir = NixlLibDir()
  return [
      lib_dir,
      posixpath.join(lib_dir, 'x86_64-linux-gnu'),
      posixpath.join(lib_dir, 'aarch64-linux-gnu'),
      posixpath.join(lib_dir, 'plugins'),
      posixpath.join(lib_dir, 'x86_64-linux-gnu', 'plugins'),
      posixpath.join(lib_dir, 'aarch64-linux-gnu', 'plugins'),
  ]


def _PkgConfigDirs() -> list[str]:
  """Returns NIXL pkg-config directories across common install layouts."""
  lib_dir = NixlLibDir()
  return [
      posixpath.join(lib_dir, 'pkgconfig'),
      posixpath.join(lib_dir, 'x86_64-linux-gnu', 'pkgconfig'),
      posixpath.join(lib_dir, 'aarch64-linux-gnu', 'pkgconfig'),
  ]


def _CudaBuildArgs() -> list[str]:
  """Returns Meson arguments for an optional CUDA stub tree."""
  stub_dir = NIXL_CUDA_STUB_DIR.value
  if not stub_dir:
    return []
  return [
      f'-Dcudapath_inc={posixpath.join(stub_dir, "include")}',
      f'-Dcudapath_lib={posixpath.join(stub_dir, "lib")}',
      f'-Dcudapath_stub={posixpath.join(stub_dir, "lib", "stubs")}',
  ]


def _Install(vm) -> None:
  """Builds and installs NIXL from source."""
  vm.Install('build_tools')
  vm.Install('ucx')
  vm.InstallPackages(
      'cmake ninja-build meson pkg-config libgflags-dev libaio-dev '
      'liburing-dev libssl-dev libcurl4-openssl-dev uuid-dev zlib1g-dev '
      'pybind11-dev python3-dev'
  )

  # Ubuntu 24.04 ships Abseil 20220623, which lacks absl_log and causes
  # NIXL's Meson configure step to fail. NIXL has a supported Abseil
  # subproject fallback, but only if no system Abseil is detected.
  vm.RemoteCommand(
      'sudo apt-get remove -y libabsl-dev || true', ignore_failure=True
  )

  vm.RemoteCommand(f'sudo rm -rf {NIXL_DIR}')
  vm.RemoteCommand(
      'git clone --depth 1 --branch'
      f' {shlex.quote(NIXL_VERSION.value)} {NIXL_REPO} {NIXL_DIR}',
      timeout=600,
  )
  meson_args = [
      'meson',
      'setup',
      'build',
      f'--prefix={NIXL_INSTALL_PREFIX.value}',
      '--buildtype=release',
      f'-Ducx_path={ucx.UCX_INSTALL_DIR}',
      f'-Denable_plugins={NIXL_ENABLE_PLUGINS.value}',
      '-Ddisable_gds_backend=true',
      '-Dbuild_tests=false',
      '-Dbuild_examples=false',
  ]
  meson_args.extend(_CudaBuildArgs())
  vm.RemoteCommand(
      f'cd {NIXL_DIR} && {ucx.Environment()} {shlex.join(meson_args)}',
      timeout=_CONFIGURE_TIMEOUT,
  )
  vm.RemoteCommand(
      f'cd {NIXL_DIR}/build && ninja',
      timeout=_BUILD_TIMEOUT,
  )
  vm.RemoteCommand(f'cd {NIXL_DIR}/build && sudo ninja install')
  vm.RemoteCommand('sudo ldconfig')
  vm.RemoteCommand(f'test -d {NIXL_INSTALL_PREFIX.value}')


def AptInstall(vm) -> None:
  """Installs NIXL on the VM."""
  _Install(vm)


def YumInstall(vm) -> None:
  """Installs NIXL on the VM."""
  _Install(vm)


def GetVersion(vm) -> str:
  """Returns the installed NIXL version."""
  stdout, _ = vm.RemoteCommand(f'git -C {NIXL_DIR} describe --tags --always')
  return stdout.strip()


def Environment() -> str:
  """Returns shell environment needed to find the NIXL installation."""
  pkg_config_path = ':'.join(_PkgConfigDirs())
  library_path = ':'.join(_LibDirs())
  return (
      f'export PKG_CONFIG_PATH={pkg_config_path}:$PKG_CONFIG_PATH; '
      f'export LD_LIBRARY_PATH={library_path}:$LD_LIBRARY_PATH;'
  )


def Uninstall(vm) -> None:
  """Uninstalls NIXL from the VM."""
  vm.RemoteCommand(f'sudo rm -rf {NIXL_DIR} {NIXL_INSTALL_PREFIX.value}')
