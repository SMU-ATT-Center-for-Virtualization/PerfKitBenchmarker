# Copyright 2019 PerfKitBenchmarker Authors. All rights reserved.
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


"""Module containing iperf installation and cleanup functions."""

import re

from perfkitbenchmarker import errors


# PACKAGE_NAME = 'iperf3'
# IPERF_ZIP = '2.0.4-RELEASE.zip'
# IPERF_DIR = 'iperf-2.0.4-RELEASE'
# PREPROVISIONED_DATA = {
#     IPERF_ZIP:
#         '84000784e9286531c227b14c999b236f9cc5679564ba1bff8702f28c30513853'
# }
# PACKAGE_DATA_URL = {
#     IPERF_ZIP: posixpath.join('https://github.com/esnet/iperf/archive',
#                               IPERF_ZIP)}



def _Install(vm):
  """Installs the iperf3 package on the VM."""
  vm.InstallPackages('iperf3')


def YumInstall(vm):
  """Installs the iperf3 package on the VM."""
  _Install(vm)

def AptInstall(vm):
  """Installs the iperf3 package on the VM."""
  _Install(vm)
