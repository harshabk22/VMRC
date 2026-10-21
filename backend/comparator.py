from calculator import load_instances


def compare_instances(instance_ids) -> dict:
    instances = {i["id"]: i for i in load_instances()}
    missing = [iid for iid in instance_ids if iid not in instances]
    if missing:
        raise ValueError("unknown instance ids: {}".format(", ".join(missing)))

    selected = [instances[iid] for iid in instance_ids]
    cheapest = min(selected, key=lambda i: i["hourly_cost_usd"])
    return {
        "instances": selected,
        "cheapest_id": cheapest["id"],
        "comparison": {
            "max_vcpu": max(i["vcpu"] for i in selected),
            "max_memory_gb": max(i["memory_gb"] for i in selected),
            "max_hourly_cost_usd": max(i["hourly_cost_usd"] for i in selected),
            "min_hourly_cost_usd": min(i["hourly_cost_usd"] for i in selected),
        },
    }


def compare_across_providers(vcpu, memory_gb) -> dict:
    result = {}
    for prov in ("aws", "azure", "gcp"):
        matches = [
            i for i in load_instances()
            if i["provider"] == prov and i["vcpu"] >= vcpu and i["memory_gb"] >= memory_gb
        ]
        matches.sort(key=lambda i: i["hourly_cost_usd"])
        result[prov] = matches[0] if matches else None
    return {
        "requirements": {"vcpu": vcpu, "memory_gb": memory_gb},
        "equivalents": result,
    }
