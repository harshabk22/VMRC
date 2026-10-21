from calculator import load_instances

# ponytail: flat per-provider rates, not tiered/live — swap in the AWS Price List + Azure Retail + GCP Catalog APIs when region selector lands

# Commitments are provider-independent: 0.63 is exactly AWS EC2 Standard RI 1yr
# No-Upfront and GCP 1yr resource-based CUD; 0.45 is GCP 3yr CUD and stays
# conservative vs AWS 3yr No-Upfront (0.43).
COMPUTE_MULTIPLIERS = {
    "ondemand": 1.0,
    "reserved_1yr": 0.63,
    "reserved_3yr": 0.45,
}

# Spot is NOT one global discount. Documented bands: AWS 46-78% off (midpoint
# ~60%), GCP a flat ~40% off (n2-standard-16 $0.4660 vs $0.7769 = 0.60). Azure
# advertises "up to 80%" but most shapes realize 30-40% off, so the headline 0.20
# is not a defensible representative.
SPOT_MULTIPLIER = {
    "aws": 0.40,
    # Measured, not estimated: spot/on-demand ratio across the eastus Azure SKUs in
    # this catalog (D2s_v5 0.2111, D32s_v5 0.2111, L8s_v3 0.2105, F16s_v2 0.2200)
    # from the Retail Prices API. Spread <5%, so a scalar is fair. 0.60 was wrong.
    "azure": 0.21,
    "gcp": 0.60,
}

# General-purpose block SSD, $/GB-month:
#   aws   0.08  gp3, the current default (the old 0.10 was the deprecated gp2 tier)
#   azure 0.079 Premium SSD v2 (Premium SSD v1 is 0.15)
#   gcp   0.10  pd-balanced (pd-standard 0.04, pd-ssd 0.17)
STORAGE_COST_PER_GB_MONTH = {
    "aws": 0.08,
    "azure": 0.079,
    "gcp": 0.10,
}

# Internet egress, $/GB, one representative flat rate per provider:
#   aws   0.09  us-east-1 first 10TB (first 100GB/mo is free and is not modelled)
#   azure 0.087
#   gcp   0.12  Premium tier (Standard tier is 0.085)
EGRESS_COST_PER_GB = {
    "aws": 0.09,
    "azure": 0.087,
    "gcp": 0.12,
}

PRICING_MODELS = tuple(COMPUTE_MULTIPLIERS) + ("spot",)

HOURS_PER_MONTH = 730

# The catalog ships exactly these three providers. A provider outside this set is
# a corrupt row ("AWS", None, "amazone"), never a real tariff, so it must raise:
# a wrong rate that is only wrong *sometimes* is the worst failure mode a cost
# calculator can have — the user spends against it without knowing.
KNOWN_PROVIDERS = frozenset(("aws", "azure", "gcp"))


def _rate(table, provider):
    if provider not in KNOWN_PROVIDERS:
        raise ValueError("unknown provider: {!r} (expected one of {})".format(
            provider, ", ".join(sorted(KNOWN_PROVIDERS))))
    return table[provider]


def estimate_cost(instance_ids, pricing_model, hours_per_month=HOURS_PER_MONTH, storage_gb=0, data_egress_gb=0) -> dict:
    if pricing_model not in PRICING_MODELS:
        raise ValueError("unknown pricing_model: {}".format(pricing_model))
    instances = {i["id"]: i for i in load_instances()}
    missing = [iid for iid in instance_ids if iid not in instances]
    if missing:
        raise ValueError("unknown instance ids: {}".format(", ".join(missing)))

    per_instance = []
    grand_monthly = 0.0
    for iid in instance_ids:
        inst = instances[iid]
        provider = inst["provider"]
        if pricing_model == "spot":
            multiplier = _rate(SPOT_MULTIPLIER, provider)
        else:
            multiplier = COMPUTE_MULTIPLIERS[pricing_model]
        compute = inst["hourly_cost_usd"] * hours_per_month * multiplier
        storage = storage_gb * _rate(STORAGE_COST_PER_GB_MONTH, provider)
        egress = data_egress_gb * _rate(EGRESS_COST_PER_GB, provider)
        monthly = compute + storage + egress
        grand_monthly += monthly
        per_instance.append({
            "instance_id": iid,
            "instance_type": inst["instance_type"],
            "provider": provider,
            "pricing_model": pricing_model,
            "compute_monthly": round(compute, 2),
            "storage_monthly": round(storage, 2),
            "egress_monthly": round(egress, 2),
            "monthly_total": round(monthly, 2),
            "yearly_total": round(monthly * 12, 2),
        })

    return {
        "pricing_model": pricing_model,
        "hours_per_month": hours_per_month,
        "storage_gb_per_instance": storage_gb,
        "data_egress_gb_per_instance": data_egress_gb,
        "instances": per_instance,
        "grand_monthly_total": round(grand_monthly, 2),
        "grand_yearly_total": round(grand_monthly * 12, 2),
    }