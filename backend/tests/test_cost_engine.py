import pytest

import cost_engine
from cost_engine import (COMPUTE_MULTIPLIERS, EGRESS_COST_PER_GB,
                         KNOWN_PROVIDERS, PRICING_MODELS, SPOT_MULTIPLIER,
                         STORAGE_COST_PER_GB_MONTH, estimate_cost)

M5_LARGE_HOURLY = 0.096
M5_XLARGE_HOURLY = 0.192

# Near-identical on-demand cost, different providers — isolates the spot rate.
AWS_BASE = "aws-m5-large"
GCP_BASE = "gcp-n2-standard-2"


class TestPricingModels:
    def test_on_demand_has_no_discount(self):
        result = estimate_cost(["aws-m5-large"], "ondemand")
        assert result["instances"][0]["compute_monthly"] == round(
            M5_LARGE_HOURLY * 730, 2
        )
        assert result["instances"][0]["monthly_total"] == 70.08

    def test_reserved_1yr_applies_37_percent_discount(self):
        result = estimate_cost(["aws-m5-large"], "reserved_1yr")
        assert result["instances"][0]["compute_monthly"] == 44.15
        assert result["instances"][0]["monthly_total"] == 44.15

    def test_reserved_3yr_applies_55_percent_discount(self):
        result = estimate_cost(["aws-m5-large"], "reserved_3yr")
        assert result["instances"][0]["compute_monthly"] == 31.54
        assert result["instances"][0]["monthly_total"] == 31.54

    def test_spot_applies_60_percent_discount_on_aws(self):
        # AWS spot is a 46-78% band, ~60% off is the midpoint — not the old 0.25.
        result = estimate_cost([AWS_BASE], "spot")
        assert result["instances"][0]["compute_monthly"] == 28.03
        assert result["instances"][0]["monthly_total"] == 28.03

    def test_pricing_multipliers_commitments(self):
        assert COMPUTE_MULTIPLIERS == {
            "ondemand": 1.0,
            "reserved_1yr": 0.63,
            "reserved_3yr": 0.45,
        }
        assert PRICING_MODELS == (
            "ondemand", "reserved_1yr", "reserved_3yr", "spot")

    def test_pricing_multipliers_spot_per_provider(self):
        # GCP spot is only ~40% off (n2-standard-16 $0.4660 vs $0.7769). A single
        # global 0.25 overstated GCP spot savings by ~2x.
        assert SPOT_MULTIPLIER["aws"] == 0.40
        assert SPOT_MULTIPLIER["gcp"] == 0.60
        # Measured 0.2111 across eastus SKUs via the Azure Retail Prices API, not estimated.
        assert SPOT_MULTIPLIER["azure"] == 0.21

    def test_spot_is_not_a_single_global_discount(self):
        aws_on_demand = estimate_cost([AWS_BASE], "ondemand")["grand_monthly_total"]
        gcp_on_demand = estimate_cost([GCP_BASE], "ondemand")["grand_monthly_total"]
        aws_spot = estimate_cost([AWS_BASE], "spot")["grand_monthly_total"]
        gcp_spot = estimate_cost([GCP_BASE], "spot")["grand_monthly_total"]
        # Same on-demand base to within 2%, so the spot gap is the provider rate.
        assert abs(aws_on_demand - gcp_on_demand) / aws_on_demand < 0.02
        assert round(aws_spot / aws_on_demand, 2) == 0.40
        assert round(gcp_spot / gcp_on_demand, 2) == 0.60
        assert aws_spot < gcp_spot


class TestStorageAndEgress:
    def test_storage_cost(self):
        result = estimate_cost([AWS_BASE], "ondemand", storage_gb=100)
        assert result["instances"][0]["storage_monthly"] == 8.0
        assert STORAGE_COST_PER_GB_MONTH["aws"] == 0.08

    def test_storage_rate_is_per_provider(self):
        rates = {
            provider: estimate_cost([iid], "ondemand", storage_gb=100)[
                "instances"][0]["storage_monthly"]
            for provider, iid in (
                ("aws", "aws-m5-large"),
                ("azure", "azure-d2s-v5"),
                ("gcp", "gcp-n2-standard-2"),
            )
        }
        # gp3 / Premium SSD v2 / pd-balanced — one global number cannot fit all three.
        assert rates == {"aws": 8.0, "azure": 7.9, "gcp": 10.0}
        assert len(set(rates.values())) == 3

    def test_data_transfer_cost(self):
        result = estimate_cost([AWS_BASE], "ondemand", data_egress_gb=500)
        assert result["instances"][0]["egress_monthly"] == 45.0
        assert EGRESS_COST_PER_GB["aws"] == 0.09

    def test_egress_rate_is_per_provider(self):
        rates = {
            provider: estimate_cost([iid], "ondemand", data_egress_gb=500)[
                "instances"][0]["egress_monthly"]
            for provider, iid in (
                ("aws", "aws-m5-large"),
                ("azure", "azure-d2s-v5"),
                ("gcp", "gcp-n2-standard-2"),
            )
        }
        # GCP Premium egress is ~33% above AWS; the old flat 0.09 undercharged it.
        assert rates == {"aws": 45.0, "azure": 43.5, "gcp": 60.0}

    def test_storage_and_egress_stack_with_compute(self):
        result = estimate_cost(
            [AWS_BASE], "ondemand", storage_gb=100, data_egress_gb=500
        )
        entry = result["instances"][0]
        assert entry["compute_monthly"] == 70.08
        assert entry["storage_monthly"] == 8.0
        assert entry["egress_monthly"] == 45.0
        assert entry["monthly_total"] == 123.08


class TestTotals:
    def test_monthly_and_yearly_totals(self):
        result = estimate_cost(["aws-m5-large"], "ondemand")
        entry = result["instances"][0]
        assert entry["monthly_total"] == 70.08
        assert entry["yearly_total"] == 840.96
        assert result["grand_monthly_total"] == 70.08
        assert result["grand_yearly_total"] == 840.96

    def test_multiple_instances_grand_totals(self):
        result = estimate_cost(
            ["aws-m5-large", "aws-m5-xlarge"], "ondemand"
        )
        expected_monthly = round(
            (M5_LARGE_HOURLY + M5_XLARGE_HOURLY) * 730, 2
        )
        assert result["grand_monthly_total"] == expected_monthly
        assert result["grand_yearly_total"] == round(expected_monthly * 12, 2)
        assert len(result["instances"]) == 2

    def test_custom_hours_per_month(self):
        result = estimate_cost(["aws-m5-large"], "ondemand", hours_per_month=100)
        assert result["hours_per_month"] == 100
        assert result["instances"][0]["compute_monthly"] == 9.6
        assert result["instances"][0]["yearly_total"] == 115.2

    def test_per_instance_fields(self):
        result = estimate_cost(["aws-m5-xlarge"], "spot")
        entry = result["instances"][0]
        assert entry["instance_id"] == "aws-m5-xlarge"
        assert entry["instance_type"] == "m5.xlarge"
        assert entry["provider"] == "aws"
        assert entry["pricing_model"] == "spot"


class TestErrorHandling:
    def test_unknown_pricing_model_raises(self):
        with pytest.raises(ValueError, match="unknown pricing_model"):
            estimate_cost(["aws-m5-large"], "free_tier")

    def test_unknown_instance_id_raises(self):
        with pytest.raises(ValueError, match="unknown instance ids"):
            estimate_cost(["aws-m5-large", "no-such-instance"], "ondemand")

    def test_single_hour_billing(self):
        result = estimate_cost(["aws-m5-large"], "ondemand", hours_per_month=1)
        assert result["instances"][0]["compute_monthly"] == round(
            M5_LARGE_HOURLY, 2
        )


def _catalog_row(provider):
    row = {
        "id": "aws-m5-large", "provider": provider, "family": "general",
        "instance_type": "m5.large", "vcpu": 2, "memory_gb": 8,
        "storage_gb": None, "network_gbps": 10, "hourly_cost_usd": 0.096,
        "region": "us-east-1", "description": "General purpose",
    }
    return row


class TestUnknownProvider:
    """_rate used to swallow any provider miss and fall back to a "neutral"
    rate. That is worse than a flat constant because it is wrong only sometimes:
    provider "AWS" priced AWS spot compute at 42.05 instead of 28.03 (+50%) and
    still returned a number. A silent wrong rate is the worst failure mode a
    cost calculator has, so an unknown provider must raise instead."""

    @pytest.mark.parametrize("provider", ["AWS", "Aws", "amazone", "amazon", None, ""])
    def test_unknown_provider_raises_for_every_rate_table(self, monkeypatch, provider):
        # ondemand exercises storage + egress, spot exercises the spot table.
        monkeypatch.setattr(cost_engine, "load_instances", lambda: [_catalog_row(provider)])
        for pricing_model in ("ondemand", "reserved_1yr", "spot"):
            with pytest.raises(ValueError, match="unknown provider"):
                estimate_cost(["aws-m5-large"], pricing_model,
                              storage_gb=100, data_egress_gb=500)

    def test_error_names_the_offending_provider(self, monkeypatch):
        monkeypatch.setattr(cost_engine, "load_instances", lambda: [_catalog_row("AWS")])
        with pytest.raises(ValueError) as exc:
            estimate_cost(["aws-m5-large"], "spot")
        assert "'AWS'" in str(exc.value)
        assert "aws, azure, gcp" in str(exc.value)

    def test_known_providers_still_price_at_their_own_rate(self, monkeypatch):
        assert KNOWN_PROVIDERS == frozenset(("aws", "azure", "gcp"))
        expected = {"aws": 8.0, "azure": 7.9, "gcp": 10.0}
        for provider in sorted(KNOWN_PROVIDERS):
            monkeypatch.setattr(cost_engine, "load_instances",
                                lambda p=provider: [_catalog_row(p)])
            result = estimate_cost(["aws-m5-large"], "spot", storage_gb=100)
            entry = result["instances"][0]
            assert entry["provider"] == provider
            assert entry["storage_monthly"] == expected[provider]
            # spot: aws pays 0.096*730*0.40, azure/gcp pay 0.096*730*0.60
            assert entry["compute_monthly"] == 28.03 if provider == "aws" else 42.05

    def test_rate_tables_have_no_silent_default(self):
        # A "default" key is unreachable now; re-adding one is the regression.
        for table in (SPOT_MULTIPLIER, STORAGE_COST_PER_GB_MONTH, EGRESS_COST_PER_GB):
            assert "default" not in table
            assert set(table) == {"aws", "azure", "gcp"}


class TestPerInstanceStorageAndEgress:
    """CostRequest documents storage_gb and data_egress_gb as PER INSTANCE, so
    N instances are charged N times -- each at its own provider's rate, not once
    for the whole fleet."""

    STORAGE = 100
    EGRESS = 500
    MIXED = ["aws-m5-large", "aws-m5-xlarge", "gcp-n2-standard-2"]

    def test_each_instance_pays_its_own_storage_and_egress(self):
        result = estimate_cost(self.MIXED, "ondemand",
                               storage_gb=self.STORAGE, data_egress_gb=self.EGRESS)
        entries = result["instances"]
        assert [e["instance_id"] for e in entries] == self.MIXED
        assert [e["provider"] for e in entries] == ["aws", "aws", "gcp"]

        # explicit arithmetic: volume x that provider's own rate
        aws_storage = self.STORAGE * STORAGE_COST_PER_GB_MONTH["aws"]
        gcp_storage = self.STORAGE * STORAGE_COST_PER_GB_MONTH["gcp"]
        aws_egress = self.EGRESS * EGRESS_COST_PER_GB["aws"]
        gcp_egress = self.EGRESS * EGRESS_COST_PER_GB["gcp"]
        assert (aws_storage, gcp_storage, aws_egress, gcp_egress) == (8.0, 10.0, 45.0, 60.0)

        assert [e["storage_monthly"] for e in entries] == [8.0, 8.0, 10.0]
        assert [e["egress_monthly"] for e in entries] == [45.0, 45.0, 60.0]

        # 3 instances -> 3x, not 1x: 8.0 + 8.0 + 10.0 and 45.0 + 45.0 + 60.0
        assert sum(e["storage_monthly"] for e in entries) == 26.0
        assert sum(e["egress_monthly"] for e in entries) == 150.0

        # 70.08 + 8.0 + 45.0, then 140.16 + 8.0 + 45.0, then 70.88 + 10.0 + 60.0
        assert [e["monthly_total"] for e in entries] == [123.08, 193.16, 140.88]
        for entry in entries:
            assert entry["monthly_total"] == round(
                entry["compute_monthly"] + entry["storage_monthly"] + entry["egress_monthly"], 2)
        assert result["grand_monthly_total"] == 457.12
        assert result["grand_yearly_total"] == 5485.48
        assert result["storage_gb_per_instance"] == 100
        assert result["data_egress_gb_per_instance"] == 500

    def test_repeated_instance_id_still_charges_each_entry(self):
        # CostRequest does not enforce unique ids, so a duplicated id must still
        # produce two entries, each paying storage -- the count is per entry.
        result = estimate_cost(["aws-m5-large", "aws-m5-large"], "ondemand",
                               storage_gb=self.STORAGE, data_egress_gb=self.EGRESS)
        assert len(result["instances"]) == 2
        assert [e["storage_monthly"] for e in result["instances"]] == [8.0, 8.0]
        assert [e["egress_monthly"] for e in result["instances"]] == [45.0, 45.0]
        assert sum(e["storage_monthly"] for e in result["instances"]) == 16.0
        assert result["grand_monthly_total"] == 246.16

    def test_spot_multiplier_is_per_provider_in_a_mixed_fleet(self):
        result = estimate_cost(["aws-m5-large", "gcp-n2-standard-2"], "spot",
                               storage_gb=self.STORAGE, data_egress_gb=self.EGRESS)
        # 0.096 * 730 * 0.40 = 28.03 ; 0.0971 * 730 * 0.60 = 42.5298
        assert [e["compute_monthly"] for e in result["instances"]] == [28.03, 42.53]
        assert [e["monthly_total"] for e in result["instances"]] == [81.03, 112.53]
        assert result["grand_monthly_total"] == 193.56
