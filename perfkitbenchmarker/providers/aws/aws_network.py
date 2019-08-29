# Copyright 2015 PerfKitBenchmarker Authors. All rights reserved.
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

"""Module containing classes related to AWS VM networking.

The Firewall class provides a way of opening VM ports. The Network class allows
VMs to communicate via internal ips and isolates PerfKitBenchmarker VMs from
others in
the same project. See https://aws.amazon.com/documentation/vpc/
for more information about AWS Virtual Private Clouds.
"""

import json
import logging
import threading
import uuid
import re
import xmltodict
import random
import string

from perfkitbenchmarker import context
from perfkitbenchmarker import errors
from perfkitbenchmarker import flags
from perfkitbenchmarker import network
from perfkitbenchmarker import providers
from perfkitbenchmarker import resource
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.providers.aws import util

flags.DEFINE_string('aws_vpc', None,
                    'The static AWS VPC id to use. Default creates a new one')
flags.DEFINE_string(
    'aws_subnet', None,
    'The static AWS subnet id to use.  Default creates a new one')
FLAGS = flags.FLAGS


REGION = 'region'
ZONE = 'zone'


class AwsFirewall(network.BaseFirewall):
  """An object representing the AWS Firewall."""

  CLOUD = providers.AWS

  def __init__(self):
    self.firewall_set = set()
    self._lock = threading.Lock()

  def AllowIcmp(self, region, security_group, cidr):
    # aws ec2 authorize-security-group-ingress
    # aws ec2 authorize-security-group-ingress --group-id sg-05075517a1daed16a --ip-permissions IpProtocol=icmp,FromPort=-1,ToPort=-1,IpRanges=[{CidrIp=0.0.0.0/0}]
    # --ip-permissions IpProtocol=icmp,FromPort=-1,ToPort=-1,IpRanges=[{CidrIp=0.0.0.0/0}]
    entry = (-1, -1, region, security_group)
    if entry in self.firewall_set:
      return
    with self._lock:
      if entry in self.firewall_set:
        return
      authorize_cmd = util.AWS_PREFIX + [
          'ec2',
          'authorize-security-group-ingress',
          '--region=%s' % region,
          '--group-id=%s' % security_group,
          '--protocol=icmp',
          '--port=-1',
          '--cidr=%s' % cidr]
      util.IssueRetryableCommand(
          authorize_cmd)
      self.firewall_set.add(entry)

  def AllowIcmp(self, vm):
    """Opens the ICMP protocol on the firewall.
    Args:
      vm: The BaseVirtualMachine object to open the ICMP protocol for.
    """

    #aws ec2 authorize-security-group-ingress --group-id <sg-id of icmp-sg-1> --protocol icmp --port -1 --cidr 0.0.0.0/0

    entry = (-1, -1, vm.region, vm.group_id)
    with self._lock:
      if entry in self.firewall_set:
        return
      authorize_cmd = util.AWS_PREFIX + [
          'ec2',
          'authorize-security-group-ingress',
          '--region=%s' % vm.region,
          '--group-id=%s' % vm.group_id,
          '--protocol=icmp',
          '--port=-1',
          '--cidr=0.0.0.0/0']
      util.IssueRetryableCommand(
          authorize_cmd)
      self.firewall_set.add(entry)


  def AllowPort(self, vm, start_port, end_port=None, source_range=None):
    """Opens a port on the firewall.

    Args:
      vm: The BaseVirtualMachine object to open the port for.
      start_port: The first local port to open in a range.
      end_port: The last local port to open in a range. If None, only start_port
        will be opened.
      source_range: List of source CIDRs to allow for this port. If None, all
        sources are allowed. i.e. ['0.0.0.0/0']
    """
    if vm.is_static or vm.network.is_static:
      return
    self.AllowPortInSecurityGroup(vm.region, vm.group_id, start_port, end_port,
                                  source_range)

  def AllowPortInSecurityGroup(self,
                               region,
                               security_group,
                               start_port,
                               end_port=None,
                               source_range=None):
    """Opens a port on the firewall for a security group.

    Args:
      region: The region of the security group
      security_group: The security group in which to open the ports
      start_port: The first local port to open in a range.
      end_port: The last local port to open in a range. If None, only start_port
        will be opened.
      source_range: List of source CIDRs to allow for this port.
    """
    end_port = end_port or start_port
    source_range = source_range or ['0.0.0.0/0']
    for source in source_range:
      entry = (start_port, end_port, region, security_group, source)
      if entry in self.firewall_set:
        return
      with self._lock:
        if entry in self.firewall_set:
          return
        authorize_cmd = util.AWS_PREFIX + [
            'ec2',
            'authorize-security-group-ingress',
            '--region=%s' % region,
            '--group-id=%s' % security_group,
            '--port=%s-%s' % (start_port, end_port),
            '--cidr=%s' % source,
        ]
        util.IssueRetryableCommand(authorize_cmd + ['--protocol=tcp'])
        util.IssueRetryableCommand(authorize_cmd + ['--protocol=udp'])
        self.firewall_set.add(entry)

  def DisallowAllPorts(self):
    """Closes all ports on the firewall."""
    pass


class AwsVpc(resource.BaseResource):
  """An object representing an Aws VPC."""

  def __init__(self, region, vpc_id=None, cidr_block='10.0.0.0/16'):
    super(AwsVpc, self).__init__(vpc_id is not None)
    self.region = region
    self.id = vpc_id
    # Subnets are assigned per-AZ.
    # _subnet_index tracks the next unused 10.0.x.0/24 block.
    self._subnet_index = 0
    # Lock protecting _subnet_index
    self._subnet_index_lock = threading.Lock()
    self.default_security_group_id = None
    if self.id:
      self._SetSecurityGroupId()

    self.cidr_block = cidr_block

  def _Create(self):
    """Creates the VPC."""
    create_cmd = util.AWS_PREFIX + [
        'ec2',
        'create-vpc',
        '--region=%s' % self.region,
        '--cidr-block=%s' % self.cidr_block]
    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.id = response['Vpc']['VpcId']
    self._EnableDnsHostnames()
    util.AddDefaultTags(self.id, self.region)

  def _PostCreate(self):
    self._SetSecurityGroupId()

  def _SetSecurityGroupId(self):
    """Looks up the VPC default security group."""
    groups = self.GetSecurityGroups('default')
    if len(groups) != 1:
      raise ValueError('Expected one security group, got {} in {}'.format(
          len(groups), groups))
    self.default_security_group_id = groups[0]['GroupId']
    logging.info('Default security group ID: %s',
                 self.default_security_group_id)

  def GetSecurityGroups(self, group_name=None):
    cmd = util.AWS_PREFIX + [
        'ec2',
        'describe-security-groups',
        '--region', self.region,
        '--filters',
        'Name=vpc-id,Values=' + self.id]
    if group_name:
      cmd.append('Name=group-name,Values={}'.format(group_name))
    stdout, _, _ = vm_util.IssueCommand(cmd)
    return json.loads(stdout)['SecurityGroups']

  def _Exists(self):
    """Returns true if the VPC exists."""
    describe_cmd = util.AWS_PREFIX + [
        'ec2',
        'describe-vpcs',
        '--region=%s' % self.region,
        '--filter=Name=vpc-id,Values=%s' % self.id]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    vpcs = response['Vpcs']
    assert len(vpcs) < 2, 'Too many VPCs.'
    return len(vpcs) > 0

  def _EnableDnsHostnames(self):
    """Sets the enableDnsHostnames attribute of this VPC to True.

    By default, instances launched in non-default VPCs are assigned an
    unresolvable hostname. This breaks the hadoop benchmark.  Setting the
    enableDnsHostnames attribute to 'true' on the VPC resolves this. See:
    http://docs.aws.amazon.com/AmazonVPC/latest/UserGuide/VPC_DHCP_Options.html
    """
    enable_hostnames_command = util.AWS_PREFIX + [
        'ec2',
        'modify-vpc-attribute',
        '--region=%s' % self.region,
        '--vpc-id', self.id,
        '--enable-dns-hostnames',
        '{ "Value": true }']

    util.IssueRetryableCommand(enable_hostnames_command)

  def _Delete(self):
    """Deletes the VPC."""
    delete_cmd = util.AWS_PREFIX + [
        'ec2',
        'delete-vpc',
        '--region=%s' % self.region,
        '--vpc-id=%s' % self.id]
    vm_util.IssueCommand(delete_cmd, raise_on_failure=False)

  def NextSubnetCidrBlock(self):
    """Returns the next available /24 CIDR block in this VPC.

    Each VPC has a 10.0.0.0/16 CIDR block.
    Each subnet is assigned a /24 within this allocation.
    Calls to this method return the next unused /24.

    Returns:
      A string representing the next available /24 block, in CIDR notation.
    Raises:
      ValueError: when no additional subnets can be created.
    """
    with self._subnet_index_lock:
      if self._subnet_index >= (1 << 8) - 1:
        raise ValueError('Exceeded subnet limit ({0}).'.format(
            self._subnet_index))
      cidr = '10.0.{0}.0/24'.format(self._subnet_index)
      self._subnet_index += 1
    return cidr


class AwsSubnet(resource.BaseResource):
  """An object representing an Aws subnet."""

  def __init__(self, zone, vpc_id, cidr_block='10.0.0.0/24', subnet_id=None):
    super(AwsSubnet, self).__init__(subnet_id is not None)
    self.zone = zone
    self.region = util.GetRegionFromZone(zone)
    self.vpc_id = vpc_id
    self.id = subnet_id
    self.cidr_block = cidr_block

  def _Create(self):
    """Creates the subnet."""

    create_cmd = util.AWS_PREFIX + [
        'ec2',
        'create-subnet',
        '--region=%s' % self.region,
        '--vpc-id=%s' % self.vpc_id,
        '--cidr-block=%s' % self.cidr_block]
    if not util.IsRegion(self.zone):
      create_cmd.append('--availability-zone=%s' % self.zone)

    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.id = response['Subnet']['SubnetId']
    util.AddDefaultTags(self.id, self.region)

  def _Delete(self):
    """Deletes the subnet."""
    logging.info('Deleting subnet %s. This may fail if all instances in the '
                 'subnet have not completed termination, but will be retried.',
                 self.id)
    delete_cmd = util.AWS_PREFIX + [
        'ec2',
        'delete-subnet',
        '--region=%s' % self.region,
        '--subnet-id=%s' % self.id]
    vm_util.IssueCommand(delete_cmd, raise_on_failure=False)

  def _Exists(self):
    """Returns true if the subnet exists."""
    return bool(self.GetDict())

  def GetDict(self):
    """The 'aws ec2 describe-subnets' for this VPC / subnet id.

    Returns:
      A dict of the single subnet or an empty dict if there are no subnets.

    Raises:
      AssertionError: If there is more than one subnet.
    """
    describe_cmd = util.AWS_PREFIX + [
        'ec2', 'describe-subnets',
        '--region=%s' % self.region,
        '--filter=Name=vpc-id,Values=%s' % self.vpc_id
    ]
    if self.id:
      describe_cmd.append('--filter=Name=subnet-id,Values=%s' % self.id)
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    subnets = response['Subnets']
    assert len(subnets) < 2, 'Too many subnets.'
    return subnets[0] if subnets else {}


class AwsInternetGateway(resource.BaseResource):
  """An object representing an Aws Internet Gateway."""

  def __init__(self, region, vpc_id=None):
    super(AwsInternetGateway, self).__init__(vpc_id is not None)
    self.region = region
    self.vpc_id = None
    self.id = None
    self.attached = False
    if vpc_id:
      self.vpc_id = vpc_id
      self.id = self.GetDict().get('InternetGatewayId')
      # if a gateway was found then it is attached to this VPC
      self.attached = bool(self.id)

  def _Create(self):
    """Creates the internet gateway."""
    create_cmd = util.AWS_PREFIX + [
        'ec2',
        'create-internet-gateway',
        '--region=%s' % self.region]
    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.id = response['InternetGateway']['InternetGatewayId']
    util.AddDefaultTags(self.id, self.region)

  def _Delete(self):
    """Deletes the internet gateway."""
    delete_cmd = util.AWS_PREFIX + [
        'ec2',
        'delete-internet-gateway',
        '--region=%s' % self.region,
        '--internet-gateway-id=%s' % self.id]
    vm_util.IssueCommand(delete_cmd, raise_on_failure=False)

  def _Exists(self):
    """Returns true if the internet gateway exists."""
    return bool(self.GetDict())

  def GetDict(self):
    """The 'aws ec2 describe-internet-gateways' for this VPC / gateway id.

    Returns:
      A dict of the single gateway or an empty dict if there are no gateways.

    Raises:
      AssertionError: If there is more than one internet gateway.
    """
    describe_cmd = util.AWS_PREFIX + [
        'ec2',
        'describe-internet-gateways',
        '--region=%s' % self.region,
    ]
    if self.id:
      describe_cmd.append('--filter=Name=internet-gateway-id,Values=%s' %
                          self.id)
    elif self.vpc_id:
      # Only query with self.vpc_id if the self.id is NOT set -- after calling
      # Detact() this object will set still have a vpc_id but will be filtered
      # out in a query if using attachment.vpc-id.
      # Using self.vpc_id instead of self.attached as the init phase always
      # sets it to False.
      describe_cmd.append('--filter=Name=attachment.vpc-id,Values=%s' %
                          self.vpc_id)
    else:
      raise errors.Error('Must have a VPC id or a gateway id')
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    internet_gateways = response['InternetGateways']
    assert len(internet_gateways) < 2, 'Too many internet gateways.'
    return internet_gateways[0] if internet_gateways else {}

  def Attach(self, vpc_id):
    """Attaches the internetgateway to the VPC."""
    if not self.attached:
      self.vpc_id = vpc_id
      attach_cmd = util.AWS_PREFIX + [
          'ec2',
          'attach-internet-gateway',
          '--region=%s' % self.region,
          '--internet-gateway-id=%s' % self.id,
          '--vpc-id=%s' % self.vpc_id]
      util.IssueRetryableCommand(attach_cmd)
      self.attached = True

  def Detach(self):
    """Detaches the internetgateway from the VPC."""
    if self.attached and not self.user_managed:
      detach_cmd = util.AWS_PREFIX + [
          'ec2',
          'detach-internet-gateway',
          '--region=%s' % self.region,
          '--internet-gateway-id=%s' % self.id,
          '--vpc-id=%s' % self.vpc_id]
      util.IssueRetryableCommand(detach_cmd)
      self.attached = False


class AwsRouteTable(resource.BaseResource):
  """An object representing a route table."""

  def __init__(self, region, vpc_id):
    super(AwsRouteTable, self).__init__()
    self.region = region
    self.vpc_id = vpc_id

  def _Create(self):
    """Creates the route table.

    This is a no-op since every VPC has a default route table.
    """
    pass

  def _Delete(self):
    """Deletes the route table.

    This is a no-op since the default route table gets deleted with the VPC.
    """
    pass

  @vm_util.Retry()
  def _PostCreate(self):
    """Gets data about the route table."""
    self.id = self.GetDict()[0]['RouteTableId']

  def GetDict(self):
    """Returns an array of the currently existing routes for this VPC."""
    describe_cmd = util.AWS_PREFIX + [
        'ec2',
        'describe-route-tables',
        '--region=%s' % self.region,
        '--filters=Name=vpc-id,Values=%s' % self.vpc_id]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    return json.loads(stdout)['RouteTables']

  def RouteExists(self):
    """Returns true if the 0.0.0.0/0 route already exists."""
    route_tables = self.GetDict()
    if not route_tables:
      return False
    for route in route_tables[0].get('Routes', []):
      if route.get('DestinationCidrBlock') == '0.0.0.0/0':
        return True
    return False

  def CreateRoute(self, internet_gateway_id):
    """Adds a route to the internet gateway."""
    if self.RouteExists():
      logging.info('Internet route already exists.')
      return
    create_cmd = util.AWS_PREFIX + [
        'ec2',
        'create-route',
        '--region=%s' % self.region,
        '--route-table-id=%s' % self.id,
        '--gateway-id=%s' % internet_gateway_id,
        '--destination-cidr-block=0.0.0.0/0']
    util.IssueRetryableCommand(create_cmd)


class AwsPlacementGroup(resource.BaseResource):
  """Object representing an AWS Placement Group.

  Attributes:
    region: The AWS region the Placement Group is in.
    name: The name of the Placement Group.
  """

  def __init__(self, region):
    """Init method for AwsPlacementGroup.

    Args:
      region: A string containing the AWS region of the Placement Group.
    """
    super(AwsPlacementGroup, self).__init__()
    self.name = (
        'perfkit-%s-%s' % (FLAGS.run_uri, str(uuid.uuid4())[-12:]))
    self.region = region

  def _Create(self):
    """Creates the Placement Group."""
    create_cmd = util.AWS_PREFIX + [
        'ec2',
        'create-placement-group',
        '--region=%s' % self.region,
        '--group-name=%s' % self.name,
        '--strategy=cluster']
    vm_util.IssueCommand(create_cmd)

  def _Delete(self):
    """Deletes the Placement Group."""
    delete_cmd = util.AWS_PREFIX + [
        'ec2',
        'delete-placement-group',
        '--region=%s' % self.region,
        '--group-name=%s' % self.name]
    # Failed deletes are ignorable (probably already deleted).
    vm_util.IssueCommand(delete_cmd, raise_on_failure=False)

  def _Exists(self):
    """Returns true if the Placement Group exists."""
    describe_cmd = util.AWS_PREFIX + [
        'ec2',
        'describe-placement-groups',
        '--region=%s' % self.region,
        '--filter=Name=group-name,Values=%s' % self.name]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    placement_groups = response['PlacementGroups']
    assert len(placement_groups) < 2, 'Too many placement groups.'
    return len(placement_groups) > 0


class _AwsRegionalNetwork(network.BaseNetwork):
  """Object representing regional components of an AWS network.

  The benchmark spec contains one instance of this class per region, which an
  AwsNetwork may retrieve or create via _AwsRegionalNetwork.GetForRegion.

  Attributes:
    region: string. The AWS region.
    vpc: an AwsVpc instance.
    internet_gateway: an AwsInternetGateway instance.
    route_table: an AwsRouteTable instance. The default route table.
  """

  CLOUD = providers.AWS

  def __repr__(self):
    return '%s(%r)' % (self.__class__, self.__dict__)

  def __init__(self, region, vpc_id=None, cidr_block='10.0.0.0/16'):
    self.region = region
    self.vpc = AwsVpc(self.region, vpc_id, cidr_block)
    self.internet_gateway = AwsInternetGateway(region, vpc_id) #CHECK
    self.route_table = None
    self.created = False

    # Locks to ensure that a single thread creates / deletes the instance.
    self._create_lock = threading.Lock()

    # Tracks the number of AwsNetworks using this _AwsRegionalNetwork.
    # Incremented by Create(); decremented by Delete();
    # When a Delete() call decrements _reference_count to 0, the RegionalNetwork
    # is destroyed.
    self._reference_count = 0
    self._reference_count_lock = threading.Lock()

  @classmethod
  def GetForRegion(cls, region, vpc_id=None, cidr_block='10.0.0.0/16'):
    """Retrieves or creates an _AwsRegionalNetwork.

    Args:
      region: string. AWS region name.
      vpc_id: string. AWS VPC id.

    Returns:
      _AwsRegionalNetwork. If an _AwsRegionalNetwork for the same region already
      exists in the benchmark spec, that instance is returned. Otherwise, a new
      _AwsRegionalNetwork is created and returned.
    """
    benchmark_spec = context.GetThreadBenchmarkSpec()
    if benchmark_spec is None:
      raise errors.Error('GetNetwork called in a thread without a '
                         'BenchmarkSpec.')
    key = cls.CLOUD, REGION, region
    # Because this method is only called from the AwsNetwork constructor, which
    # is only called from AwsNetwork.GetNetwork, we already hold the
    # benchmark_spec.networks_lock.
    if key not in benchmark_spec.networks:
      benchmark_spec.networks[key] = cls(region, vpc_id, cidr_block)
    return benchmark_spec.networks[key]

  def Create(self):
    """Creates the network."""
    with self._reference_count_lock:
      assert self._reference_count >= 0, self._reference_count
      self._reference_count += 1

    # Access here must be synchronized. The first time the block is executed,
    # the network will be created. Subsequent attempts to create the
    # network block until the initial attempt completes, then return.
    with self._create_lock:
      if self.created:
        return

      self.vpc.Create()

      self.internet_gateway.Create()
      self.internet_gateway.Attach(self.vpc.id)

      if self.route_table is None:
        self.route_table = AwsRouteTable(self.region, self.vpc.id)
      self.route_table.Create()
      self.route_table.CreateRoute(self.internet_gateway.id)

      self.created = True

  def Delete(self):
    """Deletes the network."""
    # Only actually delete if there are no more references.
    with self._reference_count_lock:
      assert self._reference_count >= 1, self._reference_count
      self._reference_count -= 1
      if self._reference_count:
        return

    self.internet_gateway.Detach()
    self.internet_gateway.Delete()
    self.vpc.Delete()


class AwsNetworkSpec(network.BaseNetworkSpec):
  """Configuration for creating an AWS network."""

  def __init__(self, zone, vpc_id=None, subnet_id=None, cidr=None):
    super(AwsNetworkSpec, self).__init__(zone)
    if vpc_id or subnet_id:
      logging.info('Confirming vpc (%s) and subnet (%s) selections', vpc_id,
                   subnet_id)
      my_subnet = AwsSubnet(self.zone, vpc_id, subnet_id=subnet_id).GetDict()
      self.vpc_id = my_subnet['VpcId']
      self.subnet_id = my_subnet['SubnetId']
      self.cidr_block = my_subnet['CidrBlock']
      logging.info('Using vpc %s subnet %s cidr %s', self.vpc_id,
                   self.subnet_id, self.cidr_block)
    elif cidr: #  user  provided subnet
      self.cidr_block = cidr
      self.cidr = cidr
      self.vpc_id = None
      self.subnet_id = None
    else:
      self.vpc_id = None
      self.subnet_id = None
      self.cidr_block = None


class AwsNetwork(network.BaseNetwork):
  """Object representing an AWS Network.

  Attributes:
    region: The AWS region the Network is in.
    regional_network: The AwsRegionalNetwork for 'region'.
    subnet: the AwsSubnet for this zone.
    placement_group: An AwsPlacementGroup instance.
  """

  CLOUD = providers.AWS

  def __repr__(self):
    return '%s(%r)' % (self.__class__, self.__dict__)

  def __init__(self, spec):
    """Initializes AwsNetwork instances.

    Args:
      spec: An AwsNetworkSpec object.
    """
    super(AwsNetwork, self).__init__(spec)
    self.region = util.GetRegionFromZone(spec.zone)

    if spec.cidr and FLAGS.use_vpn:
      self.regional_network = _AwsRegionalNetwork.GetForRegion(self.region, spec.vpc_id, spec.cidr)
    else:
      self.regional_network = _AwsRegionalNetwork.GetForRegion(self.region, spec.vpc_id)

    # self.regional_network = _AwsRegionalNetwork.GetForRegion(
    #     self.region, spec.vpc_id)
    self.subnet = None
    self.cidr = None
    self.placement_group = AwsPlacementGroup(self.region)
    self.is_static = False
    if spec.vpc_id:
      self.is_static = True
      self.subnet = AwsSubnet(
          self.zone,
          spec.vpc_id,
          cidr_block=spec.cidr_block,
          subnet_id=spec.subnet_id)

    self.elastic_ip = None
    self.global_accelerator = None

    if FLAGS.aws_global_accelerator:
      logging.warn("using aws global accelerator")
      self.elastic_ip = AwsElasticIP(self.region)
      self.global_accelerator = AwsGlobalAccelerator()

  @staticmethod
  def _GetNetworkSpecFromVm(vm):
    """Returns an AwsNetworkSpec created from VM attributes and flags."""
    return AwsNetworkSpec(vm.zone, FLAGS.aws_vpc, FLAGS.aws_subnet)

  def Create(self):
    """Creates the network."""
    logging.warn("CREATING NETWORK")
    logging.warn(FLAGS.run_uri)
    self.regional_network.Create()

    if FLAGS.use_vpn and self.cidr:
      self.subnet = AwsSubnet(self.zone, self.regional_network.vpc.id,
                              cidr_block=self.cidr)
      self.subnet.Create()

    if self.subnet is None:
      cidr = self.regional_network.vpc.NextSubnetCidrBlock()
      self.subnet = AwsSubnet(self.zone, self.regional_network.vpc.id,
                              cidr_block=cidr)
      self.subnet.Create()
    self.placement_group.Create()

    if getattr(self, 'vpngw', False):
      for gw in self.vpngw:
        self.vpngw[gw].Create()

    if FLAGS.aws_global_accelerator:
      logging.warn("using aws global accelerator")
      self.elastic_ip._Create()
      self.global_accelerator._Create()
      self.global_accelerator.AddListener('TCP', '10', '60000')
      self.global_accelerator.listeners[-1].AddEndpointGroup(self.region, self.elastic_ip.allocation_id, 128)

  def Delete(self):
    """Deletes the network."""
    if FLAGS.aws_global_accelerator:
      self.global_accelerator._Delete()
      self.elastic_ip._Delete()
    if self.subnet:
      self.subnet.Delete()
    self.placement_group.Delete()
    self.regional_network.Delete()

  @classmethod
  def _GetKeyFromNetworkSpec(cls, spec):
    """Returns a key used to register Network instances."""
    return (cls.CLOUD, ZONE, spec.zone)


class AwsGlobalAccelerator(resource.BaseResource):
  """An object representing an Aws Global Accelerator.
  https://docs.aws.amazon.com/global-accelerator/latest/dg/getting-started.html"""

# {
#    "Accelerator": { 
#       "AcceleratorArn": "string",
#       "CreatedTime": number,
#       "Enabled": boolean,
#       "IpAddressType": "string",
#       "IpSets": [ 
#          { 
#             "IpAddresses": [ "string" ],
#             "IpFamily": "string"
#          }
#       ],
#       "LastModifiedTime": number,
#       "Name": "string",
#       "Status": "string"
#    }
# }

  def __init__(self):
    super(AwsGlobalAccelerator, self).__init__()
    # all global accelerators must be located in us-west-2
    self.region = 'us-west-2'
    self.idempotency_token = None

    #The name can have a maximum of 32 characters, 
    #must contain only alphanumeric characters or hyphens (-), 
    #and must not begin or end with a hyphen.
    self.name = None
    self.accelerator_arn = None
    self.enabled = False
    self.ip_addresses = []
    self.listeners = []

# aws globalaccelerator create-accelerator 
#         --name ExampleAccelerator
#         --region us-west-2
#         --idempotencytoken dcba4321-dcba-4321-dcba-dcba4321

  def _Create(self):
    """Creates the internet gateway."""
    if not self.idempotency_token:
      self.idempotency_token = str(uuid.uuid4())[-50:]

    self.name = 'pkb-ga-%s-%s' % (FLAGS.run_uri, str(uuid.uuid4())[-12:])
            
    create_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'create-accelerator',
        '--name', self.name,
        '--region', self.region,
        '--idempotency-token', self.idempotency_token]
    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.accelerator_arn = response['Accelerator']['AcceleratorArn']
    self.ip_addresses = response['Accelerator']['IpSets'][0]['IpAddresses']
    logging.info("ACCELERATOR IP ADDRESSES")
    logging.info(self.ip_addresses)
    #util.AddDefaultTags(self.id, self.region)

  #@vm_util.Retry()
  def _Delete(self):
    """Deletes the Accelerator."""
    
    # need to disable accelerator before it can be deleted
    self._Update(enabled=False)
    status = self.Describe()
    while status['Accelerator']['Enabled'] == True:
      status = self.Describe()
    
    # need to delete listeners before accelerator can be deleted
    for listener in self.listeners:
      listener._Delete()

    delete_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'delete-accelerator',
        '--region', self.region,
        '--accelerator-arn', self.accelerator_arn]
    stdout, stderr, _ = vm_util.IssueCommand(delete_cmd)

    exists = self._Exists()
    while exists:
      vm_util.IssueCommand(delete_cmd)
      if "AcceleratorNotFoundException" in stderr:
        break
      exists = self._Exists()

  #@vm_util.Retry()
  def _Update(self,enabled):
    """Returns true if the internet gateway exists."""
    update_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'update-accelerator',
        '--region', self.region,
        '--accelerator-arn', self.accelerator_arn]
    if enabled:
      update_cmd += ['--enabled']
    else:
      update_cmd += ['--no-enabled']
    stdout, _ = util.IssueRetryableCommand(update_cmd)
    response = json.loads(stdout)
    accelerator = response['Accelerator']
    # assert accelerator['Enabled'] == enabled, 'Accelerator not updated'

  def _Exists(self):
    """Returns true if the accelerator exists."""
    describe_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'describe-accelerator',
        '--region', self.region,
        '--accelerator-arn', self.accelerator_arn]
    stdout, _, _ = vm_util.IssueCommand(describe_cmd)
    try:
      response = json.loads(stdout)
      accelerator = response['Accelerator']
      return len(accelerator) > 0
    except ValueError as e:
        return False



  def Describe(self):
    """Returns true if the accelerator exists."""
    describe_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'describe-accelerator',
        '--region', self.region,
        '--accelerator-arn', self.accelerator_arn]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    return response

  def Status(self):
    """Returns true if the accelerator exists."""
    describe_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'describe-accelerator',
        '--region', self.region,
        '--accelerator-arn', self.accelerator_arn]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    status = response['Accelerator']['Status']
    return status

  #  @vm_util.Retry(poll_interval=1, log_errors=False, max_retries=5
  #                retryable_exceptions=(AwsTransitionalVmRetryableError,))
  def isUp(self):
    """Returns true if the accelerator exists."""
    describe_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'describe-accelerator',
        '--region', self.region,
        '--accelerator-arn', self.accelerator_arn]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    status = response['Accelerator']['Status']
    return status

  def AddListener(self, protocol, start_port, end_port):
    """Returns true if the accelerator exists."""   
    self.listeners.append(AwsGlobalAcceleratorListener(self,
                                                       protocol,
                                                       start_port,
                                                       end_port))
    self.listeners[-1]._Create()


class AwsGlobalAcceleratorListener(resource.BaseResource):
  """Class representing an AWS Global Accelerator listener."""

  def __init__(self, accelerator, protocol, start_port, end_port):
    super(AwsGlobalAcceleratorListener, self).__init__()
    self.accelerator_arn = accelerator.accelerator_arn
    #self.target_group_arn = target_group.arn
    self.start_port = start_port
    self.end_port = end_port
    self.protocol = protocol
    self.region = accelerator.region
    self.idempotency_token = None
    self.arn = None
    self.endpoint_groups = []

# aws globalaccelerator create-listener 
#        --accelerator-arn arn:aws:globalaccelerator::012345678901:accelerator/1234abcd-abcd-1234-abcd-1234abcdefgh 
#        --port-ranges FromPort=80,ToPort=80 FromPort=81,ToPort=81 
#        --protocol TCP
#        --region us-west-2
#        --idempotencytoken dcba4321-dcba-4321-dcba-dcba4321

  def _Create(self):
    if not self.idempotency_token:
      self.idempotency_token = str(uuid.uuid4())[-50:]
    """Create the listener."""
    create_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'create-listener',
        '--accelerator-arn', self.accelerator_arn,
        '--region', self.region,
        '--protocol', self.protocol,
        '--port-ranges', 
        'FromPort=%s,ToPort=%s' % (str(self.start_port), str(self.end_port)),
        '--idempotency-token', self.idempotency_token
    ]
    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.listener_arn = response['Listener']['ListenerArn']
    logging.info("LISTENER ARN")
    logging.info(self.listener_arn)

# RESPONSE
# {
#    "Listener": { 
#       "ClientAffinity": "string",
#       "ListenerArn": "string",
#       "PortRanges": [ 
#          { 
#             "FromPort": number,
#             "ToPort": number
#          }
#       ],
#       "Protocol": "string"
#    }
# }

  def _Exists(self):
    """Returns true if the accelerator exists."""
    describe_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'describe-listener',
        '--region', self.region,
        '--listener-arn', self.listener_arn]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    accelerator = response['Listener']
    return len(accelerator) > 0

  def _Delete(self):
    """Deletes Listeners"""
    for endpoint_group in self.endpoint_groups:
      endpoint_group._Delete()
    delete_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'delete-listener',
        '--region', self.region,
        '--listener-arn', self.listener_arn]
    vm_util.IssueCommand(delete_cmd)

  def AddEndpointGroup(self, region, endpoint, weight):
    """Add end point group to listener."""   
    self.endpoint_groups.append(AwsEndpointGroup(self, region))

    self.endpoint_groups[-1]._Create(endpoint, weight)

class AwsEndpointGroup(resource.BaseResource):
  """An object representing an Aws Global Accelerator.
  https://docs.aws.amazon.com/global-accelerator/latest/dg/getting-started.html"""

# {
#    "EndpointGroup": { 
#       "EndpointDescriptions": [ 
#          { 
#             "EndpointId": "string",
#             "HealthReason": "string",
#             "HealthState": "string",
#             "Weight": number
#          }
#       ],
#       "EndpointGroupArn": "string",
#       "EndpointGroupRegion": "string",
#       "HealthCheckIntervalSeconds": number,
#       "HealthCheckPath": "string",
#       "HealthCheckPort": number,
#       "HealthCheckProtocol": "string",
#       "ThresholdCount": number,
#       "TrafficDialPercentage": number
#    }
# }

  def __init__(self, listener, endpoint_group_region):
    super(AwsEndpointGroup, self).__init__()
    # all global accelerators must be located in us-west-2
    self.region = 'us-west-2'
    self.idempotency_token = None
    self.listener_arn = listener.listener_arn
    self.endpoint_group_region = endpoint_group_region
    self.endpoint_group_arn = None
    self.endpoints = []

# aws globalaccelerator create-endpoint-group 
#            --listener-arn arn:aws:globalaccelerator::012345678901:accelerator/1234abcd-abcd-1234-abcd-1234abcdefgh/listener/0123vxyz 
#            --endpoint-group-region us-east-1 
#            --endpoint-configurations EndpointId=eipalloc-eip01234567890abc,Weight=128
#            --region us-west-2
#            --idempotencytoken dcba4321-dcba-4321-dcba-dcba4321

  def _Create(self, endpoint, weight=128):
    """Creates the internet gateway."""
    if not self.idempotency_token:
      self.idempotency_token = str(uuid.uuid4())[-50:]

    create_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'create-endpoint-group',
        '--listener-arn', self.listener_arn,
        '--endpoint-group-region', self.endpoint_group_region,
        '--region', self.region,
        '--idempotency-token', self.idempotency_token,
        '--endpoint-configurations',
        'EndpointId=%s,Weight=%s' % (endpoint, str(weight))]
    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.endpoint_group_arn = response['EndpointGroup']['EndpointGroupArn']
    self.endpoints.append(endpoint)
    #util.AddDefaultTags(self.id, self.region)
    return

  def _Update(self, endpoint, weight=128):
    """Update the endpoint group."""
    if not self.idempotency_token:
      self.idempotency_token = ''.join(
        random.choice(string.ascii_lowercase + 
                      string.ascii_uppercase +  
                      string.digits) for i in range(50))

    update_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'update-endpoint-group',
        '--region', self.region,
        '--endpoint-configurations',
        'EndpointId=%s,Weight=%s' % (endpoint, str(weight))]
    vm_util.IssueCommand(update_cmd)
    #util.AddDefaultTags(self.id, self.region)

  def _Delete(self):
    """Deletes the endpoint group."""
    delete_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'delete-endpoint-group',
        '--region', self.region,
        '--endpoint-group-arn', self.endpoint_group_arn]
    vm_util.IssueCommand(delete_cmd)

  def _Exists(self):
    """Returns true if the internet gateway exists."""
    describe_cmd = util.AWS_PREFIX + [
        'globalaccelerator',
        'describe-endpoint-group',
        '--region', self.region,
        '--endpoint-group-arn', self.endpoint_group_arn]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    internet_gateways = response['EndpointGroup']
    return len(internet_gateways) > 0


class AwsElasticIP(resource.BaseResource):
  """An object representing an Aws Internet Gateway."""

  def __init__(self, region, domain='vpc'):
    super(AwsElasticIP, self).__init__()
    assert (domain in ('vpc', 'standard')), "Elastic IP domain type, %s, must be either vpc or standard" % domain
    self.domain = domain
    self.public_ip = None
    self.region = region
    self.allocation_id = None
    self.attached = False
    self.instance_id = None

  def _Create(self):
    """Creates the internet gateway."""
    create_cmd = util.AWS_PREFIX + [
        'ec2',
        'allocate-address',
        '--domain', self.domain,
        '--region', self.region]
    stdout, _, _ = vm_util.IssueCommand(create_cmd)
    response = json.loads(stdout)
    self.allocation_id = response['AllocationId']
    self.public_ip = response['PublicIp']
    #util.AddDefaultTags(self.id, self.region)

  def _Delete(self):
    """Deletes the internet gateway."""
    delete_cmd = util.AWS_PREFIX + [
        'ec2',
        'release-address',
        '--region', self.region,
        '--allocation-id', self.allocation_id]
    vm_util.IssueCommand(delete_cmd)

  def _Exists(self):
    """Returns true if the internet gateway exists."""
    describe_cmd = util.AWS_PREFIX + [
        'ec2',
        'describe-addresses',
        '--region', self.region,
        '--allocation-ids', self.allocation_id]
    stdout, _ = util.IssueRetryableCommand(describe_cmd)
    response = json.loads(stdout)
    addresses = response['Addresses']
    return len(addresses) > 0

#aws ec2 associate-address --instance-id i-0b263919b6498b123 --allocation-id eipalloc-64d5890a
  def AssociateAddress(self, instance_id):
    """Associates elastic IP with an EC2 instance in a VPC"""
    if not self.attached:
      self.instance_id = instance_id
      attach_cmd = util.AWS_PREFIX + [
          'ec2',
          'associate-address',
          '--region', self.region,
          '--instance-id', self.instance_id,
          '--allocation-id', self.allocation_id]
      util.IssueRetryableCommand(attach_cmd)
      self.attached = True

  def DisassociateAddress(self):
    """Detaches the internetgateway from the VPC."""
    if self.attached:
      detach_cmd = util.AWS_PREFIX + [
          'ec2',
          'disassociate-address',
          '--region', self.region,
          '--association-id', self.allocation_id]
      util.IssueRetryableCommand(detach_cmd)
      self.attached = False
