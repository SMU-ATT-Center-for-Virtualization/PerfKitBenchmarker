import posixpath

from perfkitbenchmarker import errors
from perfkitbenchmarker.data import ResourceNotFound
from perfkitbenchmarker.linux_packages import INSTALL_DIR

PACKAGE_NAME = 'iperf'

IPERF_TAR = 'iperf-2.0.13.tar.gz'
IPERF_URL = 'https://sourceforge.net/projects/iperf2/files/iperf-2.0.13.tar.gz'

IPERF_DIR = '%s/iperf-2.0.13' % INSTALL_DIR


def _Install(vm):
  """Installs the iperf package on the VM."""


  vm.Install('build_tools')
  vm.Install('wget')

  _CopyTar(vm)


  vm.RemoteCommand(
      'cd %s; tar xvf %s; cd %s; ./configure; make; sudo make install' % (
          INSTALL_DIR, IPERF_TAR, IPERF_DIR))


def YumInstall(vm):
  """Installs the iperf package on the VM."""
  _Install(vm)


def AptInstall(vm):
  """Installs the iperf package on the VM."""
  _Install(vm)


def _CopyTar(vm):
  """Copy the tar file for installation.

  Tries local data directory first, then IPERF_URL
  """

  try:
    print("UPLOAD TAR")
    vm.PushDataFile(IPERF_TAR, remote_path=(INSTALL_DIR + '/'))
  except ResourceNotFound:
    print("UPLOAD TAR EXCEPTION, DOWNLOADING FROM SOURCE")
    vm.RemoteCommand('wget -O %s/%s %s' % (
      INSTALL_DIR, IPERF_TAR, IPERF_URL))
