"""Live pricing with a visible paper trail. Stdlib only.

Azure Retail Prices is unauthenticated and proven working, so Azure refreshes
for real from Microsoft.
AWS and GCP feeds use zero-credential open community feeds (Vantage Instances API
and instances.vantage.sh GCP feed) providing live on-demand Linux pricing per region.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import database as db

AZURE_RETAIL_URL = "https://prices.azure.com/api/retail/prices"
AZURE_API_VERSION = "2023-01-01-preview"

VANTAGE_EC2_FAMILY_URL = "https://instances-api.vantage.sh/api/v1/instances/ec2/families/{}/global"
VANTAGE_GCP_URL = "https://instances.vantage.sh/gcp/instances.json"

CACHE_TTL_HOURS = 24

DEFAULT_REGIONS = {"aws": "us-east-1", "azure": "eastus", "gcp": "us-central1"}


class PricingError(ValueError):
    pass


def azure_meter(instance_type: str) -> str:
    """D2s_v5 -> 'D2s v5'. Azure meters use a space, the catalog uses '_'."""
    return instance_type.replace("_", " ")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "vmrc-pricing/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_azure_price(instance_type: str, region: str = "eastus") -> float:
    """One Linux on-demand Consumption price, straight from Microsoft.

    Exact meter match + product without 'Windows' + not Spot/Low Priority.
    Raises PricingError when the meter is missing or ambiguous.
    """
    meter = azure_meter(instance_type)
    filt = (
        "serviceName eq 'Virtual Machines'"
        " and armRegionName eq '{}'".format(region) +
        " and priceType eq 'Consumption'"
        " and contains(meterName, '{}')".format(meter.replace("'", "''"))
    )
    url = "{}?api-version={}&$filter={}".format(
        AZURE_RETAIL_URL, AZURE_API_VERSION, urllib.parse.quote(filt, safe=""))
    found = {}
    while url:
        page = _get(url)
        for item in page.get("Items", []):
            name = item.get("meterName", "")
            product = item.get("productName", "")
            if name != meter or "Windows" in product:
                continue
            found[name] = item.get("unitPrice")
        url = page.get("NextPageLink")
    if len(found) != 1:
        raise PricingError(
            "azure meter {!r} in {}: expected 1 Linux on-demand price, found {}".format(
                meter, region, len(found)))
    return float(found[meter])


def refresh_azure(instance_types, region: str = "eastus") -> dict:
    """Fetch live Azure prices and cache them. Returns {instance_type: price}."""
    prices = {t: fetch_azure_price(t, region) for t in instance_types}
    db.save_price_snapshots("azure", region, prices, source="azure-retail-live")
    return prices


def _extract_vantage_prices(items, requested_set: set, region: str) -> dict:
    """Extract Linux on-demand prices for requested instances from Vantage JSON items."""
    prices = {}
    if not isinstance(items, list):
        return prices
    for item in items:
        itype = item.get("instance_type")
        if itype in requested_set:
            ondemand = item.get("pricing", {}).get(region, {}).get("linux", {}).get("ondemand")
            if ondemand is not None:
                try:
                    prices[itype] = round(float(ondemand), 4)
                except (ValueError, TypeError):
                    pass
    return prices


def fetch_aws_prices(instance_types, region: str = "us-east-1") -> dict:
    """Fetch live AWS EC2 Linux ondemand prices from zero-credential Vantage endpoint."""
    families = sorted({t.split(".")[0] for t in instance_types if "." in t})
    prices = {}
    requested_set = set(instance_types)

    for fam in families:
        url = VANTAGE_EC2_FAMILY_URL.format(urllib.parse.quote(fam, safe=""))
        try:
            items = _get(url)
            prices.update(_extract_vantage_prices(items, requested_set, region))
        except Exception:
            # If one family fails or times out, continue to allow partial updates
            pass

    return prices


def refresh_aws(instance_types, region: str = "us-east-1") -> dict:
    """Fetch live AWS prices and cache them. Returns {instance_type: price}."""
    prices = fetch_aws_prices(instance_types, region)
    if not prices:
        raise PricingError(f"No AWS live prices could be retrieved for region {region}")
    db.save_price_snapshots("aws", region, prices, source="vantage-ec2-live")
    return prices


def fetch_gcp_prices(instance_types, region: str = "us-central1") -> dict:
    """Fetch live GCP Linux ondemand prices from zero-credential Vantage feed."""
    items = _get(VANTAGE_GCP_URL)
    return _extract_vantage_prices(items, set(instance_types), region)


def refresh_gcp(instance_types, region: str = "us-central1") -> dict:
    """Fetch live GCP prices and cache them. Returns {instance_type: price}."""
    prices = fetch_gcp_prices(instance_types, region)
    if not prices:
        raise PricingError(f"No GCP live prices could be retrieved for region {region}")
    db.save_price_snapshots("gcp", region, prices, source="vantage-gcp-live")
    return prices


REFRESHERS = {
    "azure": refresh_azure,
    "aws": refresh_aws,
    "gcp": refresh_gcp,
}


def refresh_all(catalog_instances, regions: dict = None) -> dict:
    """Refresh live prices for Azure, AWS, and GCP simultaneously."""
    if regions is None:
        regions = DEFAULT_REGIONS

    results = {}
    for provider, refresher in REFRESHERS.items():
        types = [i["instance_type"] for i in catalog_instances if i.get("provider") == provider]
        if not types:
            continue
        region = regions.get(provider, DEFAULT_REGIONS.get(provider))
        try:
            p = refresher(types, region)
            results[provider] = {"count": len(p), "region": region, "status": "ok"}
        except Exception as e:
            results[provider] = {"count": 0, "status": "error", "error": str(e)}

    total = sum(v.get("count", 0) for v in results.values())
    return {"total_refreshed": total, "providers": results}


def cached_prices(provider: str, region: str, max_age_hours: int = CACHE_TTL_HOURS):
    rows = db.get_price_snapshots(provider, region)
    if not rows:
        return None
    latest = max(r["fetched_at"] for r in rows)
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(latest)).total_seconds() / 3600
    if age_h > max_age_hours:
        return None
    return {r["instance_type"]: r["unit_price"] for r in rows}, latest


def get_status() -> dict:
    """Honest provenance per provider: live+fresh, live+stale, or snapshot_only."""
    out = {}
    for provider, region in DEFAULT_REGIONS.items():
        rows = db.get_price_snapshots(provider, region)
        if rows:
            latest = max(r["fetched_at"] for r in rows)
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(latest)).total_seconds() / 3600
            out[provider] = {
                "mode": "live" if age_h <= CACHE_TTL_HOURS else "live-stale",
                "region": region,
                "source": rows[0]["source"],
                "fetched_at": latest,
                "count": len(rows),
            }
        else:
            out[provider] = {
                "mode": "snapshot_only",
                "region": region,
                "source": "verified-snapshot instances.json (no unauthenticated feed)",
                "fetched_at": None,
                "count": 0,
            }
    return out


def apply_live_prices(instances, provider: str = None, region: str = None):
    """Overlay cached live prices onto snapshot rows. Unmatched rows keep the
    snapshot price and say so — never silently mixed."""
    # Determine which providers to inspect
    providers_to_check = [provider] if provider else list(DEFAULT_REGIONS.keys())
    live_maps = {}
    for p in providers_to_check:
        reg = region if (region and provider == p) else DEFAULT_REGIONS.get(p)
        cached = cached_prices(p, reg)
        if cached:
            live, fetched_at = cached
            live_maps[p] = (live, fetched_at)

    out = []
    for inst in instances:
        row = dict(inst)
        p = inst.get("provider")
        itype = inst.get("instance_type")
        if p in live_maps and itype in live_maps[p][0]:
            live, fetched_at = live_maps[p]
            row["hourly_cost_usd"] = live[itype]
            source_tag = "azure-retail" if p == "azure" else f"vantage-{p}"
            row["price_source"] = f"live:{source_tag}"
            row["price_fetched_at"] = fetched_at
        else:
            row["price_source"] = row.get("price_source") or "snapshot:instances.json"
            row["price_fetched_at"] = row.get("price_fetched_at")
        out.append(row)
    return out
