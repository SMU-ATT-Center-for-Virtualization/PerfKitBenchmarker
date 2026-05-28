
""" Run netperf between a GCE client VM and a GKE pod """

import functools
import logging

from absl import flags
from perfkitbenchmarker import background_tasks
from perfkitbenchmarker import configs
from perfkitbenchmarker import vm_util
from perfkitbenchmarker.linux_benchmarks import netperf_benchmark
from perfkitbenchmarker.resources.container_service import kubernetes_commands

FLAGS = flags.FLAGS

KUBERNETES_NETPERF_ENABLE_HOST_NETWORK = flags.DEFINE_boolean(
    'kubernetes_netperf_enable_host_network',
    False,
    'Whether to set the GKE hostNetwork pod setting to True'
)

BENCHMARK_NAME = 'kubernetes_netperf'

BENCHMARK_CONFIG = """
kubernetes_netperf:
  description: Run netperf between a client VM and a Kubernetes Pod.
  vm_groups:
    clients:
      vm_spec:
        GCP:
          machine_type: c4d-highcpu-8
          zone: us-central1-b
      vm_count: 1
  container_specs:
    kubernetes_netperf:
      image: netperf
  container_registry:
    cloud: GCP
    spec:
      GCP:
        zone: 'us-central1-b'
  container_cluster:
    cloud: GCP
    type: Kubernetes
    vm_count: 1
    vm_spec:
      GCP:
        machine_type: n4-standard-2
        zone: us-central1-b
    nodepools:
      netperf-server-pool:
        vm_count: 1
        vm_spec:
          GCP:
            machine_type: c4d-highcpu-8
            zone: us-central1-b
"""

def GetConfig(user_config):
    return configs.LoadConfig(BENCHMARK_CONFIG, user_config, BENCHMARK_NAME)

def _PrepareClient(client_vm):
    client_vm.Install('netperf')
    netperf_benchmark.PrepareClientVM(client_vm)

def _PrepareCluster(benchmark_spec):
    image = benchmark_spec.container_specs['kubernetes_netperf'].image

    host_network_spec = ""
    if KUBERNETES_NETPERF_ENABLE_HOST_NETWORK.value:
        logging.info("Creating netperf-server pod with hostNetwork: True")
        host_network_spec = f"""
    hostNetwork: true
    dnsPolicy: ClusterFirstWithHostNet"""

    node_selector_spec = """
    nodeSelector:
        cloud.google.com/gke-nodepool: netperf-server-pool"""

    pod_yaml = f"""
apiVersion: v1
kind: Pod
metadata:
    name: netperf-server
    labels:
        app: netperf
spec:{host_network_spec}{node_selector_spec}
    containers:
    - name: netperf
      image: {image}
"""

    kubernetes_commands.CreateResource(pod_yaml)

    logging.info("Waiting for netperf-server pod to be ready...")
    kubernetes_commands.WaitForResource('pod/netperf-server', 'ready', timeout = 300)

    # get internal IP for GKE pod
    pod_ip = kubernetes_commands.Get('pod', 'netperf-server', json_selector='.status.podIP')
    benchmark_spec.server_pod_ip = pod_ip.strip()
    logging.info(f"Netperf server pod IP: {benchmark_spec.server_pod_ip}")


def Prepare(benchmark_spec):
    client_vm = benchmark_spec.vm_groups['clients'][0]

    prepare_fns = [
        functools.partial(_PrepareCluster, benchmark_spec),
        functools.partial(_PrepareClient, client_vm)
    ]
    background_tasks.RunThreaded(lambda f: f(), prepare_fns)

def Run(benchmark_spec):
    client_vm = benchmark_spec.vm_groups['clients'][0]
    server_ip = benchmark_spec.server_pod_ip

    results = []
    metadata = {
        'server_environment': 'kubernetes_pod',
        'sending_zone': client_vm.zone,
        'sending_machine_type': client_vm.machine_type,
        'receiving_zone': benchmark_spec.container_cluster.nodepools['netperf-server-pool'].zone,
        'receiving_machine_type': benchmark_spec.container_cluster.nodepools['netperf-server-pool'].machine_type,
        'kubernetes_pod_host_network': KUBERNETES_NETPERF_ENABLE_HOST_NETWORK.value,
        'gke_enable_dataplane_v2': FLAGS.gke_enable_dataplane_v2,
        'gce_node_group': FLAGS.gce_node_group,
        'gke_node_group': FLAGS.gke_node_group,
    }

    for num_streams in netperf_benchmark.FLAGS.netperf_num_streams:
        for benchmark_name in netperf_benchmark.FLAGS.netperf_benchmarks:

            samples = netperf_benchmark.RunNetperf(
                client_vm,
                benchmark_name,
                [server_ip],
                num_streams,
                client_vm.GetInternalIPs(),
                FLAGS.netperf_test_length,
            )

            for sample in samples:
                sample.metadata.update( metadata )
                sample.metadata['ip_type'] = vm_util.IpAddressMetadata.INTERNAL

            results.extend( samples )

    return results

def Cleanup(benchmark_spec):
    kubernetes_commands.DeleteResource('pod/netperf-server')
    client_vm = benchmark_spec.vm_groups['clients'][0]
    client_vm.RemoteCommand(f'sudo rm -rf {netperf_benchmark.REMOTE_SCRIPT}', ignore_failure=True)

