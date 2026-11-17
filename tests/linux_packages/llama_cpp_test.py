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
"""Tests for llama.cpp installation helpers."""

from unittest import mock

from absl.testing import absltest
from absl.testing import flagsaver
from perfkitbenchmarker import errors
from perfkitbenchmarker.linux_packages import llama_cpp


class LlamaCppTest(absltest.TestCase):

  def _Vm(self, gpu_count):
    vm = mock.Mock()
    vm.gpu_count = gpu_count
    vm.TryRemoteCommand.return_value = True
    vm.RemoteCommand.return_value = ("CUDA0: NVIDIA L4\n", "")
    return vm

  def _Commands(self, vm):
    return [call.args[0] for call in vm.RemoteCommand.call_args_list]

  @flagsaver.flagsaver(llama_cpp_backend="auto")
  def testCpuBuildDoesNotEnableCuda(self):
    vm = self._Vm(gpu_count=0)

    llama_cpp._Install(vm, "libcurl4-openssl-dev")

    commands = self._Commands(vm)
    configure = next(c for c in commands if "cmake -B build" in c)
    self.assertNotIn("GGML_CUDA", configure)
    self.assertTrue(any("--target llama-server" in c for c in commands))

  @flagsaver.flagsaver(llama_cpp_backend="auto")
  def testGpuBuildEnablesCuda(self):
    vm = self._Vm(gpu_count=4)

    llama_cpp._Install(vm, "libcurl4-openssl-dev")

    commands = self._Commands(vm)
    configure = next(c for c in commands if "cmake -B build" in c)
    self.assertIn("-DGGML_CUDA=ON", configure)
    self.assertIn("-DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc", configure)
    self.assertTrue(any("--target llama-server" in c for c in commands))
    self.assertTrue(any("--list-devices" in c for c in commands))

  @flagsaver.flagsaver(llama_cpp_backend="cpu")
  def testExplicitCpuBackendOverridesGpu(self):
    vm = self._Vm(gpu_count=4)

    llama_cpp._Install(vm, "libcurl4-openssl-dev")

    configure = next(c for c in self._Commands(vm) if "cmake -B build" in c)
    self.assertNotIn("GGML_CUDA", configure)

  @flagsaver.flagsaver(llama_cpp_backend="cuda")
  def testCudaBackendRequiresNvcc(self):
    vm = self._Vm(gpu_count=4)
    vm.TryRemoteCommand.return_value = False

    with self.assertRaises(errors.Benchmarks.PrepareException):
      llama_cpp._Install(vm, "libcurl4-openssl-dev")

  @flagsaver.flagsaver(llama_cpp_backend="cuda")
  def testCudaBuildRequiresVisibleDevice(self):
    vm = self._Vm(gpu_count=4)
    vm.RemoteCommand.return_value = ("", "")

    with self.assertRaises(errors.Benchmarks.PrepareException):
      llama_cpp._Install(vm, "libcurl4-openssl-dev")


if __name__ == "__main__":
  absltest.main()
