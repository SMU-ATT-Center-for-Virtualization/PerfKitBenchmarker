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
"""Module containing llama.cpp installation and cleanup functions."""

import posixpath
import shlex

from absl import flags
from perfkitbenchmarker import errors
from perfkitbenchmarker import linux_packages

LLAMA_CPP_VERSION = flags.DEFINE_string(
    'llama_cpp_version',
    'v0.9.0',
    'Git tag or commit of llama.cpp to build.',
)
LLAMA_CPP_BACKEND = flags.DEFINE_enum(
    'llama_cpp_backend',
    'auto',
    ['auto', 'cpu', 'cuda'],
    'Build backend. "auto" uses CUDA when the VM has a GPU.',
)

LLAMA_CPP_REPO = 'https://github.com/ggml-org/llama.cpp'
LLAMA_CPP_DIR = posixpath.join(linux_packages.INSTALL_DIR, 'llama.cpp')
LLAMA_SERVER_BIN = posixpath.join(LLAMA_CPP_DIR, 'build', 'bin', 'llama-server')
CUDA_NVCC = '/usr/local/cuda/bin/nvcc'

_BUILD_TIMEOUT = 60 * 60 * 2
_CONFIGURE_TIMEOUT = 60 * 10


def _UseCuda(vm) -> bool:
  """Returns whether llama.cpp should be built with CUDA."""
  if LLAMA_CPP_BACKEND.value == 'cuda':
    return True
  if LLAMA_CPP_BACKEND.value == 'cpu':
    return False
  return bool(getattr(vm, 'gpu_count', 0))


def _Install(vm, curl_dev_package: str) -> None:
  """Builds llama.cpp from source on the VM."""
  vm.Install('build_tools')
  vm.Install('cmake')
  vm.Install('curl')
  vm.InstallPackages(curl_dev_package)
  use_cuda = _UseCuda(vm)
  if use_cuda and not vm.TryRemoteCommand(f'test -x {shlex.quote(CUDA_NVCC)}'):
    raise errors.Benchmarks.PrepareException(
        'llama.cpp CUDA backend requested, but nvcc was not found at'
        f' {CUDA_NVCC}. Install the CUDA toolkit before llama.cpp.'
    )

  vm.RemoteCommand(f'sudo rm -rf {LLAMA_CPP_DIR}')
  vm.RemoteCommand(
      'git clone --depth 1 --branch'
      f' {shlex.quote(LLAMA_CPP_VERSION.value)} {LLAMA_CPP_REPO}'
      f' {LLAMA_CPP_DIR}',
      timeout=600,
  )
  cmake_args = [
      '-DGGML_NATIVE=ON',
      '-DCMAKE_BUILD_TYPE=Release',
  ]
  if use_cuda:
    cmake_args += [
        '-DGGML_CUDA=ON',
        f'-DCMAKE_CUDA_COMPILER={CUDA_NVCC}',
    ]
  vm.RemoteCommand(
      f'cd {LLAMA_CPP_DIR} && cmake -B build {shlex.join(cmake_args)}',
      timeout=_CONFIGURE_TIMEOUT,
  )
  vm.RemoteCommand(
      f'cd {LLAMA_CPP_DIR} && cmake --build build --config Release'
      ' -j"$(nproc)" --target llama-server',
      timeout=_BUILD_TIMEOUT,
  )
  vm.RemoteCommand(f'test -x {LLAMA_SERVER_BIN}')
  if use_cuda:
    stdout, _ = vm.RemoteCommand(
        f'{LLAMA_SERVER_BIN} --list-devices',
        ignore_failure=True,
    )
    if 'CUDA' not in stdout:
      raise errors.Benchmarks.PrepareException(
          'llama-server was built but did not report a CUDA device. Output:'
          f' {stdout!r}'
      )


def AptInstall(vm) -> None:
  """Installs llama.cpp on the VM."""
  _Install(vm, 'libcurl4-openssl-dev')


def YumInstall(vm) -> None:
  """Installs llama.cpp on the VM."""
  _Install(vm, 'libcurl-devel')


def GetVersion(vm) -> str:
  """Returns the installed llama.cpp version."""
  stdout, _ = vm.RemoteCommand(
      f'git -C {LLAMA_CPP_DIR} describe --tags --always'
  )
  return stdout.strip()


def Uninstall(vm) -> None:
  """Uninstalls llama.cpp from the VM."""
  vm.RemoteCommand(f'sudo rm -rf {LLAMA_CPP_DIR}')
