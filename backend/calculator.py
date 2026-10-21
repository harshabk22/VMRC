import json
import math
import os

from models import WorkloadRequest

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "instances.json")


def load_instances(region=None, live=False):
    with open(DATA_PATH) as f:
        instances = json.load(f)
    if region is not None:
        instances = [i for i in instances if i.get("region") == region]
    if live:
        import pricing as pricing_svc
        instances = pricing_svc.apply_live_prices(instances)
    return instances


def calculate_resources(workload: WorkloadRequest) -> dict:
    headroom = 1.0 + workload.headroom_percent / 100.0
    required_vcpu = workload.concurrent_users * workload.cpu_per_user / 100.0 * workload.growth_factor * headroom
    required_ram = workload.concurrent_users * workload.memory_per_user_mb * workload.growth_factor * headroom / 1024.0
    required_storage = workload.storage_gb * workload.growth_factor * headroom
    required_bandwidth = workload.bandwidth_mbps * workload.growth_factor * headroom
    return {
        "workload_type": workload.workload_type,
        "concurrent_users": workload.concurrent_users,
        "growth_factor": workload.growth_factor,
        "headroom_percent": workload.headroom_percent,
        "required_vcpu": round(required_vcpu, 2),
        "required_ram_gb": round(required_ram, 2),
        "required_storage_gb": round(required_storage, 2),
        "required_bandwidth_mbps": round(required_bandwidth, 2),
    }


def find_matching_instances(required_vcpu, required_ram, provider=None) -> list:
    matches = [
        inst for inst in load_instances()
        if inst["vcpu"] >= required_vcpu
        and inst["memory_gb"] >= required_ram
        and (provider is None or inst["provider"] == provider)
    ]
    return sorted(matches, key=lambda i: i["hourly_cost_usd"])


def recommend_for_workload(workload: WorkloadRequest) -> dict:
    resources = calculate_resources(workload)
    all_matches = find_matching_instances(resources["required_vcpu"], resources["required_ram_gb"])
    by_provider = {}
    for prov in ("aws", "azure", "gcp"):
        matches = [i for i in all_matches if i["provider"] == prov]
        by_provider[prov] = matches[:3]
    return {
        "resources": resources,
        "total_matches": len(all_matches),
        "recommendations": by_provider,
    }
