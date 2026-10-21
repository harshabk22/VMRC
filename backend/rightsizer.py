from calculator import load_instances
from cost_engine import HOURS_PER_MONTH

HEADROOM_PERCENT = 20.0
NET_SATURATION_PERCENT = 80.0


def percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    k = (len(ordered) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _classify(p95_cpu, p95_mem, net_saturated=False):
    # ponytail: one flat link-saturation threshold for every provider. Real
    # committed/burst bandwidth tiers per provider + AZ would need a per-instance
    # bandwidth model; do that only when the catalog carries burst credits.
    if net_saturated:
        return "UNDER_PROVISIONED"
    if p95_cpu < 5 and p95_mem < 10:
        return "IDLE"
    if p95_cpu > 80 or p95_mem > 85:
        return "UNDER_PROVISIONED"
    if p95_cpu < 30 and p95_mem < 50:
        return "OVER_PROVISIONED"
    return "OPTIMIZED"


def analyze_utilization(current_instance_id, cpu_utils, mem_utils, net_utils) -> dict:
    all_instances = load_instances()
    instances = {i["id"]: i for i in all_instances}
    if current_instance_id not in instances:
        raise ValueError("unknown instance id: {}".format(current_instance_id))
    current = instances[current_instance_id]

    stats = {}
    for label, samples in (("cpu", cpu_utils), ("memory", mem_utils), ("network", net_utils)):
        stats[label] = {
            "p50": round(percentile(samples, 50), 2),
            "p90": round(percentile(samples, 90), 2),
            "p95": round(percentile(samples, 95), 2),
            "p99": round(percentile(samples, 99), 2),
        }

    # network samples are already percent-of-link, so they are only meaningful
    # when the catalog declares a non-zero bandwidth for this instance.
    net_cap = current.get("network_gbps") or 0
    net_saturated = net_cap > 0 and stats["network"]["p95"] > NET_SATURATION_PERCENT
    classification = _classify(stats["cpu"]["p95"], stats["memory"]["p95"], net_saturated)

    headroom = 1.0 + HEADROOM_PERCENT / 100.0
    needed_vcpu = current["vcpu"] * stats["cpu"]["p95"] / 100.0 * headroom
    needed_mem = current["memory_gb"] * stats["memory"]["p95"] / 100.0 * headroom

    recommendation = None
    savings_monthly = 0.0
    if classification == "OVER_PROVISIONED":
        candidates = [
            i for i in all_instances
            if i["provider"] == current["provider"]
            and i["vcpu"] < current["vcpu"]
            and i["vcpu"] >= needed_vcpu
            and i["memory_gb"] >= needed_mem
        ]
        candidates.sort(key=lambda i: (-i["vcpu"], i["hourly_cost_usd"]))
        if candidates:
            target = candidates[0]
            savings_monthly = (current["hourly_cost_usd"] - target["hourly_cost_usd"]) * HOURS_PER_MONTH
            recommendation = {
                "instance_id": target["id"],
                "instance_type": target["instance_type"],
                "vcpu": target["vcpu"],
                "memory_gb": target["memory_gb"],
                "hourly_cost_usd": target["hourly_cost_usd"],
                "monthly_savings": round(savings_monthly, 2),
                "yearly_savings": round(savings_monthly * 12, 2),
            }

    return {
        "current_instance": current,
        "utilization": stats,
        "classification": classification,
        "required_vcpu_with_headroom": round(needed_vcpu, 2),
        "required_memory_gb_with_headroom": round(needed_mem, 2),
        "recommendation": recommendation,
    }


def recommend_optimization(current_instance_id, cpu_utils, mem_utils) -> dict:
    all_instances = load_instances()
    instances = {i["id"]: i for i in all_instances}
    if current_instance_id not in instances:
        raise ValueError("unknown instance id: {}".format(current_instance_id))
    current = instances[current_instance_id]

    p95_cpu = percentile(cpu_utils, 95)
    p95_mem = percentile(mem_utils, 95)
    headroom = 1.0 + HEADROOM_PERCENT / 100.0
    needed_vcpu = current["vcpu"] * p95_cpu / 100.0 * headroom
    needed_mem = current["memory_gb"] * p95_mem / 100.0 * headroom

    candidates = [
        i for i in all_instances
        if i["vcpu"] >= needed_vcpu and i["memory_gb"] >= needed_mem
    ]
    if not candidates:
        raise ValueError("no instance in the catalog meets the p95 requirements with 20% headroom")
    candidates.sort(key=lambda i: i["hourly_cost_usd"])
    target = candidates[0]

    savings_monthly = (current["hourly_cost_usd"] - target["hourly_cost_usd"]) * HOURS_PER_MONTH
    return {
        "current_instance": {
            "id": current["id"],
            "instance_type": current["instance_type"],
            "provider": current["provider"],
            "vcpu": current["vcpu"],
            "memory_gb": current["memory_gb"],
            "hourly_cost_usd": current["hourly_cost_usd"],
        },
        "p95_cpu_utilization": round(p95_cpu, 2),
        "p95_memory_utilization": round(p95_mem, 2),
        "required_vcpu_with_headroom": round(needed_vcpu, 2),
        "required_memory_gb_with_headroom": round(needed_mem, 2),
        "recommended_instance": {
            "id": target["id"],
            "instance_type": target["instance_type"],
            "provider": target["provider"],
            "vcpu": target["vcpu"],
            "memory_gb": target["memory_gb"],
            "hourly_cost_usd": target["hourly_cost_usd"],
        },
        "monthly_savings": round(savings_monthly, 2),
        "yearly_savings": round(savings_monthly * 12, 2),
        "current_monthly_cost": round(current["hourly_cost_usd"] * HOURS_PER_MONTH, 2),
        "recommended_monthly_cost": round(target["hourly_cost_usd"] * HOURS_PER_MONTH, 2),
    }
