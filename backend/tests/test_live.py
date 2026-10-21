"""Live-layer guards. No network: the HTTP fetch is stubbed, the cache is the
test DB from conftest, and telemetry refusals need no credentials."""
import pytest

import database
import pricing
import telemetry


def test_azure_meter_derivation():
    assert pricing.azure_meter("D2s_v5") == "D2s v5"
    assert pricing.azure_meter("B2s") == "B2s"


def test_fetch_azure_price_picks_linux_ondemand(monkeypatch):
    page = {"Items": [
        {"meterName": "D2s v5 Spot", "productName": "Virtual Machines Dsv5 Series", "unitPrice": 0.020266},
        {"meterName": "D2s v5", "productName": "Virtual Machines Dsv5 Series Windows", "unitPrice": 0.188},
        {"meterName": "D2s v5", "productName": "Virtual Machines Dsv5 Series", "unitPrice": 0.096},
    ]}
    monkeypatch.setattr(pricing, "_get", lambda url, timeout=30: page)
    assert pricing.fetch_azure_price("D2s_v5", "eastus") == 0.096


def test_fetch_azure_price_missing_meter_raises(monkeypatch):
    monkeypatch.setattr(pricing, "_get", lambda url, timeout=30: {"Items": []})
    with pytest.raises(pricing.PricingError):
        pricing.fetch_azure_price("Nope_v9", "eastus")


def test_cache_roundtrip_and_overlay():
    database.save_price_snapshots("azure", "eastus", {"D2s_v5": 0.096}, source="test")
    cached, fetched_at = pricing.cached_prices("azure", "eastus")
    assert cached == {"D2s_v5": 0.096} and fetched_at
    rows = [{"id": "x", "provider": "azure", "instance_type": "D2s_v5", "hourly_cost_usd": 0.5},
            {"id": "y", "provider": "aws", "instance_type": "m5.large", "hourly_cost_usd": 0.096}]
    out = pricing.apply_live_prices(rows)
    assert out[0]["hourly_cost_usd"] == 0.096
    assert out[0]["price_source"] == "live:azure-retail"
    assert out[1]["price_source"] == "snapshot:instances.json"


def test_status_reports_snapshot_for_aws_gcp():
    status = pricing.get_status()
    assert status["aws"]["mode"] == "snapshot_only"
    assert status["gcp"]["mode"] == "snapshot_only"
    assert status["azure"]["mode"] in ("live", "live-stale", "snapshot_only")


def test_telemetry_refuses_without_credentials():
    with pytest.raises(telemetry.TelemetryError, match="not configured"):
        telemetry.fetch("aws")
    with pytest.raises(telemetry.TelemetryError, match="not configured"):
        telemetry.fetch("azure")
    with pytest.raises(telemetry.TelemetryError):
        telemetry.fetch("wat")


async def test_pricing_status_endpoint(client):
    resp = await client.get("/api/pricing/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["providers"]["aws"]["mode"] == "snapshot_only"


async def test_telemetry_fetch_aws_is_400_not_fake(client):
    resp = await client.post("/api/telemetry/fetch", json={"provider": "aws"})
    assert resp.status_code == 400
    assert "not configured" in resp.json()["error"]


def test_fetch_aws_prices(monkeypatch):
    mock_data = [
        {
            "instance_type": "m5.large",
            "family": "m5",
            "pricing": {"us-east-1": {"linux": {"ondemand": "0.096"}}},
        },
        {
            "instance_type": "m5.xlarge",
            "family": "m5",
            "pricing": {"us-east-1": {"linux": {"ondemand": "0.192"}}},
        },
    ]
    monkeypatch.setattr(pricing, "_get", lambda url, timeout=30: mock_data)
    prices = pricing.fetch_aws_prices(["m5.large", "m5.xlarge"], "us-east-1")
    assert prices == {"m5.large": 0.096, "m5.xlarge": 0.192}


def test_fetch_gcp_prices(monkeypatch):
    mock_data = [
        {
            "instance_type": "e2-standard-2",
            "pricing": {"us-central1": {"linux": {"ondemand": "0.067011"}}},
        }
    ]
    monkeypatch.setattr(pricing, "_get", lambda url, timeout=30: mock_data)
    prices = pricing.fetch_gcp_prices(["e2-standard-2"], "us-central1")
    assert prices == {"e2-standard-2": 0.067}


def test_telemetry_parse_labeled_lines():
    raw = "CPU: 10, 20, 30\nMemory: 40, 50, 60\nNetwork: 5, 8, 12"
    parsed = telemetry.parse_telemetry(raw)
    assert parsed["cpu"] == [10.0, 20.0, 30.0]
    assert parsed["memory"] == [40.0, 50.0, 60.0]
    assert parsed["network"] == [5.0, 8.0, 12.0]
    assert parsed["sample_count"] == 3


def test_telemetry_parse_csv_table():
    raw = "timestamp,cpu_pct,memory_pct,net_mbps\n1,15.5,55.0,10.0\n2,20.0,60.0,12.5"
    parsed = telemetry.parse_telemetry(raw)
    assert parsed["cpu"] == [15.5, 20.0]
    assert parsed["memory"] == [55.0, 60.0]
    assert parsed["network"] == [10.0, 12.5]


def test_telemetry_parse_json():
    raw = '{"cpu": [10, 25], "memory": [50, 60], "network": [5, 10]}'
    parsed = telemetry.parse_telemetry(raw)
    assert parsed["cpu"] == [10.0, 25.0]
    assert parsed["memory"] == [50.0, 60.0]


def test_telemetry_parse_empty_raises():
    with pytest.raises(telemetry.TelemetryError):
        telemetry.parse_telemetry("   \n\t")


async def test_cloud_status_endpoint(client):
    resp = await client.get("/api/cloud/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "providers" in body
    assert "aws" in body["providers"]
    assert "azure" in body["providers"]
    assert "gcp" in body["providers"]


async def test_telemetry_parse_endpoint(client):
    resp = await client.post("/api/telemetry/parse", json={"raw_data": "CPU: 12, 14\nMemory: 30, 40"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cpu"] == [12.0, 14.0]
    assert data["memory"] == [30.0, 40.0]
