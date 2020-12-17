# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Module containing nmap installation and cleanup functions."""

import re

from perfkitbenchmarker import errors

def _Install(vm):
  """Installs the nmap package on the VM."""
  vm.InstallPackages('nmap')


def YumInstall(vm):
  """Installs the nmap package on the VM."""
  _Install(vm)


def AptInstall(vm):
  """Installs the nmap package on the VM."""
  _Install(vm)
