# (C) Datadog, Inc. 2024-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import ipaddress
from typing import Any  # noqa: F401
from urllib.parse import urlparse

from datadog_checks.base import OpenMetricsBaseCheckV2
from datadog_checks.base.checks.openmetrics.v2.transform import get_native_dynamic_transformer

from .kube_client import KubernetesAPIClient
from .metrics import METRICS_MAP


class KubeVirtApiCheck(OpenMetricsBaseCheckV2):
    __NAMESPACE__ = "kubevirt_api"
    DEFAULT_METRIC_LIMIT = 0

    def __init__(self, name, init_config, instances):
        super(KubeVirtApiCheck, self).__init__(name, init_config, instances)
        self.check_initializations.appendleft(self._parse_config)
        self.check_initializations.append(self._configure_additional_transformers)

    def check(self, _):
        # type: (Any) -> None

        self._init_base_tags()

        if self.kubevirt_api_healthz_endpoint:
            self._report_health_check(self.kubevirt_api_healthz_endpoint)
        else:
            self.log.warning(
                "Skipping health check. Please provide a `kubevirt_api_healthz_endpoint` to ensure the health of the KubeVirt API."  # noqa: E501
            )

        self._validate_metrics_endpoint(self.kubevirt_api_metrics_endpoint)

        self._setup_kube_client()

        self._report_vm_metrics()
        self._report_vmis_metrics()

        super().check(_)

    def _init_base_tags(self):
        self.base_vm_tags = []
        self.base_pod_tags = []

        if self.kube_cluster_name:
            self.base_vm_tags.append(f"kube_cluster_name:{self.kube_cluster_name}")
            self.base_pod_tags.append(f"kube_cluster_name:{self.kube_cluster_name}")

        if self.kube_namespace:
            self.base_pod_tags.append(f"kube_namespace:{self.kube_namespace}")

        if self.kube_pod_name:
            self.base_pod_tags.append(f"pod_name:{self.kube_pod_name}")

    def _setup_kube_client(self):
        try:
            self.kube_client = KubernetesAPIClient(log=self.log, kube_config_dict=self.kube_config_dict)
        except Exception as e:
            self.log.error("Cannot connect to Kubernetes API: %s", str(e))
            raise

    def _report_health_check(self, health_endpoint):
        try:
            self.log.debug("Checking health status at %s", health_endpoint)
            response = self.http.get(health_endpoint)
            response.raise_for_status()
            self.gauge("can_connect", 1, tags=[f"endpoint:{health_endpoint}", *self.base_pod_tags])
        except Exception as e:
            self.log.error(
                "Cannot connect to KubeVirt API HTTP endpoint '%s': %s.\n",
                health_endpoint,
                str(e),
            )
            self.gauge("can_connect", 0, tags=[f"endpoint:{health_endpoint}", *self.base_pod_tags])
            raise

    def _report_vm_metrics(self):
        vms = self.kube_client.get_vms()
        self.log.debug("Reporting metrics for %d VMs", len(vms))
        if vms:
            for vm in vms:
                vm_tags = self._extract_vm_tags(vm)
                self.gauge("vm.count", value=1, tags=vm_tags)
        else:
            self.gauge("vm.count", value=0, tags=self.base_vm_tags)

    def _report_vmis_metrics(self):
        vmis = self.kube_client.get_vmis()
        self.log.debug("Reporting metrics for %d VMIs", len(vmis))
        if vmis:
            for vmi in vmis:
                vmi_tags = self._extract_vmi_tags(vmi)
                self.gauge("vmi.count", value=1, tags=vmi_tags)
        else:
            self.gauge("vmi.count", value=0, tags=self.base_vm_tags)

    def _extract_vm_tags(self, vm):
        if not vm:
            return []

        tags = []
        tags.append(f"vm_name:{vm['metadata']['name']}")
        tags.append(f"vm_uid:{vm['metadata']['uid']}")
        tags.append(f"kube_namespace:{vm['metadata']['namespace']}")

        if self.kube_cluster_name:
            tags.append(f"kube_cluster_name:{self.kube_cluster_name}")

        for label, value in vm["spec"]["template"]["metadata"]["labels"].items():
            if not label.startswith("kubevirt.io/"):
                continue
            label_name = label.replace("kubevirt.io/", "")
            tags.append(f"vm_{label_name}:{value}")

        return tags

    def _extract_vmi_tags(self, vmi):
        if not vmi:
            return []

        tags = []
        tags.append(f"vmi_name:{vmi['metadata']['name']}")
        tags.append(f"vmi_uid:{vmi['metadata']['uid']}")
        tags.append(f"vmi_phase:{vmi['status']['phase']}")
        tags.append(f"kube_namespace:{vmi['metadata']['namespace']}")

        if self.kube_cluster_name:
            tags.append(f"kube_cluster_name:{self.kube_cluster_name}")

        for label, value in vmi["metadata"]["labels"].items():
            if not label.startswith("kubevirt.io/"):
                continue
            label_name = label.replace("kubevirt.io/", "")
            tags.append(f"vmi_{label_name}:{value}")

        return tags

    def _validate_metrics_endpoint(self, url):
        parsed_url = urlparse(url)

        host = parsed_url.hostname
        port = parsed_url.port

        if host and port:
            try:
                host = ipaddress.ip_address(host)
                return host, port
            except Exception as e:
                raise ValueError(f"Host '{host}' must be a valid ip address: {str(e)}")
        else:
            raise ValueError(f"URL '{url}' does not match the expected format `https://<host_ip>:<port>/<path>`")

    def _parse_config(self):
        self.kubevirt_api_metrics_endpoint = self.instance.get("kubevirt_api_metrics_endpoint")
        self.kubevirt_api_healthz_endpoint = self.instance.get("kubevirt_api_healthz_endpoint")
        self.kube_cluster_name = self.instance.get("kube_cluster_name")
        self.kube_namespace = self.instance.get("kube_namespace")
        self.kube_pod_name = self.instance.get("kube_pod_name")

        self.kube_config_dict = self.instance.get("kube_config_dict")

        parsed_url = urlparse(self.kubevirt_api_metrics_endpoint)
        if parsed_url.path != "/metrics":
            self.log.warning(
                "The provided endpoint '%s' does not have the '/metrics' path. Adding it automatically.",
                self.kubevirt_api_metrics_endpoint,
            )
            self.kubevirt_api_metrics_endpoint = "{}/metrics".format(self.kubevirt_api_metrics_endpoint)

        self.scraper_configs = []

        instance = {
            "openmetrics_endpoint": self.kubevirt_api_metrics_endpoint,
            "namespace": self.__NAMESPACE__,
            "enable_health_service_check": False,
            "rename_labels": {"version": "kubevirt_api_version", "host": "kubevirt_api_host"},
        }

        self.scraper_configs.append(instance)

    def _configure_additional_transformers(self):
        metric_transformer = self.scrapers[self.kubevirt_api_metrics_endpoint].metric_transformer
        metric_transformer.add_custom_transformer(r".*", self.configure_transformer_kubevirt_metrics(), pattern=True)

    def configure_transformer_kubevirt_metrics(self):
        def transform(_metric, sample_data, _runtime_data):
            for sample, tags, hostname in sample_data:
                metric_name = _metric.name
                metric_type = _metric.type

                # ignore metrics we don't collect
                if metric_name not in METRICS_MAP:
                    continue

                # add tags
                tags = tags + self.base_pod_tags

                # get mapped metric name
                new_metric_name = METRICS_MAP[metric_name]
                if isinstance(new_metric_name, dict) and "name" in new_metric_name:
                    new_metric_name = new_metric_name["name"]

                # send metric
                metric_transformer = self.scrapers[self.kubevirt_api_metrics_endpoint].metric_transformer

                if metric_type == "counter":
                    self.count(new_metric_name + ".count", sample.value, tags=tags, hostname=hostname)
                elif metric_type == "gauge":
                    self.gauge(new_metric_name, sample.value, tags=tags, hostname=hostname)
                else:
                    native_transformer = get_native_dynamic_transformer(
                        self, new_metric_name, None, metric_transformer.global_options
                    )

                    def add_tag_to_sample(sample, pod_tags):
                        [sample, tags, hostname] = sample
                        return [sample, tags + pod_tags, hostname]

                    modified_sample_data = (add_tag_to_sample(x, self.base_pod_tags) for x in sample_data)
                    native_transformer(_metric, modified_sample_data, _runtime_data)

        return transform
