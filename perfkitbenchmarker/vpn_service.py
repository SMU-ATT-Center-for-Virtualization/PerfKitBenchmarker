# Copyright 2017 PerfKitBenchmarker Authors. All rights reserved.
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
from perfkitbenchmarker import errors, context, resource, flags
import itertools
import re
from itertools import ifilter
import time
import logging
import json
import uuid

flags.DEFINE_integer('vpn_service_tunnel_count', None,
                     'Number of tunnels to create for each VPNGW pair.')
flags.DEFINE_integer('vpn_service_gateway_count', None,
                     'Number of VPN GWs to create for each VPNGW pair.')
flags.DEFINE_string('vpn_service_name', None,
                    'If set, use this name for VPN Service.')
flags.DEFINE_string('vpn_service_shared_key', None,
                    'If set, use this PSK for VPNs.')
flags.DEFINE_string('vpn_service_routing_type', None,
                    'static or dynamic(BGP)')
flags.DEFINE_integer('vpn_service_ike_version', None, 'IKE version')

FLAGS = flags.FLAGS


def GetVPNServiceClass():
  """Gets the VPNService class.

  Args:

  Returns:
    Implementation class
  """
  return resource.GetResourceClass(VPNService)


class VPN(object):
  """An object representing the VPN."""

  def __init__(self, *args, **kwargs):
      #       self.GWPair = None  # pair of vpngw's to create tunnel between
      #       self.endpoint_config = None
      #       self.name = None  # name of the vpn created
      #       self.routing = 'static'
      #       self.psk = None
      return object.__init__(self, *args, **kwargs)

  def getKeyFromGWPair(self, gwpair, suffix=''):
    key = 'vpn' + ''.join(gw for gw in gwpair) + suffix + FLAGS.run_uri
    return key

  def Create(self, gwpair, suffix=''):
    self.GWPair = gwpair
    self.name = self.getKeyFromGWPair(gwpair)
    self.tunnel_config = TunnelConfig(name=self.name, suffix=suffix)

  def Delete(self):
    pass

  def GetVPN(self, gwpair, suffix=''):
    ''' gets a VPN object for the gwpair or creates one if none exists

    Args:
    gwpair: a tuple of two VPNGWs
    '''

    benchmark_spec = context.GetThreadBenchmarkSpec()
    if benchmark_spec is None:
      raise errors.Error('GetVPN called in a thread without a '
                         'BenchmarkSpec.')
    with benchmark_spec.vpngws_lock:
      key = self.getKeyFromGWPair(gwpair, suffix)
      if key not in benchmark_spec.vpns:
        self.Create(gwpair, suffix)
        benchmark_spec.vpns[key] = self
      return benchmark_spec.vpns[key]

#   def ConfigureTunnel(self, suffix):
  def ConfigureTunnel(self):

    benchmark_spec = context.GetThreadBenchmarkSpec()
    vpngw0 = benchmark_spec.vpngws[self.GWPair[0]]
    vpngw1 = benchmark_spec.vpngws[self.GWPair[1]]

    assert not (vpngw0.require_target_to_init and vpngw1.require_target_to_init), 'Cant connect 2 passive VPN GWs'

    while not self.isTunnelConfigured():
        vpngw0.ConfigureTunnel(self.tunnel_config)
        vpngw1.ConfigureTunnel(self.tunnel_config)

    tunnel_status = self.isTunnelReady()
    logging.info('Tunnel is ready?: %s ' % tunnel_status)

  def isTunnelConfigured(self):
    is_tunnel_configured = False
    if len(self.tunnel_config.endpoints) == 2:
      if self.tunnel_config.endpoints[self.GWPair[0]]['is_configured'] and self.tunnel_config.endpoints[self.GWPair[1]]['is_configured']:
        logging.info('is configured: True')
#         print self.tunnel_config.endpoints[self.GWPair[0]]['is_configured']
#         print self.tunnel_config.endpoints[self.GWPair[1]]['is_configured']
#         print self.tunnel_config.endpoints[self.GWPair[0]]['is_configured'] and self.tunnel_config.endpoints[self.GWPair[1]]['is_configured']
        is_tunnel_configured = True
    return is_tunnel_configured

  # tunnel should be up now, just wait for all clear
  # blocking here for now
  def isTunnelReady(self):
    benchmark_spec = context.GetThreadBenchmarkSpec()
    ready = False
    timeout = time.time() + 60 * 5  # give up after 5 mins
    while(not ready and time.time() < timeout):
      logging.info('Tunnel endpoints configured. Waiting for tunnel...')
      ready = benchmark_spec.vpngws[self.GWPair[0]].IsTunnelReady(self.tunnel_config.endpoints[self.GWPair[0]]['tunnel_id']) and benchmark_spec.vpngws[self.GWPair[1]].IsTunnelReady(self.tunnel_config.endpoints[self.GWPair[1]]['tunnel_id'])
      time.sleep(5)

    return ready


class TunnelConfig(object):
  """
  Object to hold all parms needed to configure a tunnel.

  tunnel_config =
  { tunnel_name = ''
    routing = ''
    psk = ''
    endpoints = [ep1={...}, ep2={...}
    }

  endpoint =
  { name = ''
    ip = ''
    cidr = ''
    require_target_to_init = t/f
    tunnel_id = ''

  }
  }

  """

  def __init__(self, **kwargs):
    super(TunnelConfig, self).__init__()
    self.tunnel_name = kwargs.get('tunnel_name', 'unnamed_tunnel')  # uniquely id this tunnel
    self.endpoints = {}
    self.routing = kwargs.get('routing', 'static')  # @TODO static/dynamic
    self.ike_version = kwargs.get('ike_version', '1')
    self.psk = kwargs.get('psk', 'key' + FLAGS.run_uri)
    self.suffix = kwargs.get('suffix', '')
#     self.name = kwargs.get('name') # name of this endpoint
#     self.target_name = kwargs.get('target_name')
#     self.ip_address = kwargs.get('ip_address') # public IP of this endpoint
#     self.target_ip = kwargs.get('target_ip') # IP of target VPN GW
#     self.forwarding_rules = kwargs.get('forwarding_rules')
#     self.routes = kwargs.get('routes')
#     self.network_name = kwargs.get('network_name')
#     self.az = kwargs.get('az')
#     self.region = kwargs.get('region')
#     self.cidr = kwargs.get('cidr')
#     self.created = kwargs.get('created', False)  # managed by BaseResource
#     self.endpoint_id = kwargs.get('endpoint_id')

  def set(self, **kwargs):
    for key in kwargs:
        setattr(self, key, kwargs[key])

  def __str__(self):
    #     print json.dumps(self.__dict__)
    return str(json.dumps(self.__dict__))


class VPNService(resource.BaseResource):
  RESOURCE_TYPE = 'BaseVPNService'
  REQUIRED_ATTRS = ['SERVICE']

  def __init__(self, spec):
    """Initialize the VPN Service object.

    Args:
      vpn_service_spec: spec of the vpn service.
    """
    super(VPNService, self).__init__()
#     self.shared_key = spec.vpn_service_spec.shared_key
    self.name = spec.name
    self.tunnel_count = spec.tunnel_count
#     self.gateway_count = spec.gateway_count
    self.gateway_count = FLAGS.vpn_service_gateway_count
    self.routing = spec.routing_type
    self.ike_version = spec.ike_version
    self.psk = FLAGS.run_uri
    self.spec = spec
#     print self.gateway_count
    self.vpns = {}

#     # update metadata
#     self.metadata.update({'t_count': self.tunnel_count,
#                           'vpn_name': self.name})

  def _Create(self):
    """Creates VPN objects for VPNGW pairs.
    """

    benchmark_spec = context.GetThreadBenchmarkSpec()
    if benchmark_spec is None:
      raise errors.Error('CreateVPN Service. called in a thread without a '
                         'BenchmarkSpec.')

    # with benchmark_spec.vpngws_lock:
    self.vpngw_pairs = self.GetVPNGWPairs(benchmark_spec.vpngws)  # @TODO change to endpoint pair
    # with benchmark_spec.vpns_lock:
    for gwpair in self.vpngw_pairs:
      # creates the vpn if it doesn't exist and registers in bm_spec.vpns
      suffix = self.GetNewSuffix()
      vpn_id = VPN().getKeyFromGWPair(gwpair, suffix)
      self.vpns[vpn_id] = VPN().GetVPN(gwpair, suffix)
#       vpn.psk = self.shared_key
#       vpn.routing = self.routing  # @TODO need mixed static/dynamic tunnels?
#       vpn.ConfigureTunnel(suffix=suffix)
      self.vpns[vpn_id].ConfigureTunnel()

  def _Delete(self):
    #     benchmark_spec = context.GetThreadBenchmarkSpec()
    #     if benchmark_spec is None:
    #       raise errors.Error('CreateVPN Service called in a thread without a BenchmarkSpec.')
    #     for vpn in benchmark_spec.vpns:
    #       benchmark_spec.vpns[vpn].Delete()
    pass

  def GetNewSuffix(self):
    # Names for tunnels, fr's, routes, etc need to be unique
    return format(uuid.uuid4().fields[1], 'x')

  def GetMetadata(self):
    """Return a dictionary of the metadata for VPNs created."""
    basic_data = {'vpn_service': self.name,
                  'routing_type': self.routing,
                  'ike_version': self.ike_version,
                  'tunnel_count': self.tunnel_count,
                  'gateway_count': self.gateway_count}
    return basic_data

  def GetVPNGWPairs(self, vpngws):
    # vpngw-us-west1-0-28ed049a <-> vpngw-us-central1-0-28ed049a # yes
    # vpngw-us-west1-0-28ed049a <-> vpngw-us-central1-1-28ed049a # no
     # get all gw pairs then filter out the non matching tunnel id's
    vpngw_pairs = itertools.combinations(vpngws, 2)
    r = re.compile(r"(?P<gw_prefix>.*-.*-.*)?-(?P<gw_tnum>[0-9])-(?P<run_id>.*)")
    # function = lambda x: r.search(x[0]).group('gw_tnum') == r.search(x[1]).group('gw_tnum')
    function = lambda x: r.search(x[0]).group('gw_prefix') != r.search(x[1]).group('gw_prefix')
    return ifilter(function, vpngw_pairs)