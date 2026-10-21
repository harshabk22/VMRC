import pytest

import rightsizer
from rightsizer import (NET_SATURATION_PERCENT, analyze_utilization,
                         percentile)

CURRENT = "aws-m5-2xlarge"  # 8 vcpu, 32 GB, $0.384/hr


class TestPercentile:
    def test_median_of_odd_list(self):
        assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0

    def test_interpolated_percentile(self):
        assert percentile([1.0, 2.0, 3.0, 4.0], 95) == 3.85

    def test_single_value(self):
        assert percentile([42.0], 95) == 42.0

    def test_empty_list_returns_zero(self):
        assert percentile([], 95) == 0.0

    def test_uniform_list(self):
        assert percentile([7.5] * 20, 90) == 7.5


class TestClassification:
    def test_idle(self):
        result = analyze_utilization(CURRENT, [3.0], [8.0], [1.0])
        assert result["classification"] == "IDLE"
        assert result["recommendation"] is None

    def test_over_provisioned(self):
        result = analyze_utilization(CURRENT, [20.0], [30.0], [5.0])
        assert result["classification"] == "OVER_PROVISIONED"

    def test_under_provisioned_high_cpu(self):
        result = analyze_utilization(CURRENT, [90.0], [50.0], [10.0])
        assert result["classification"] == "UNDER_PROVISIONED"

    def test_under_provisioned_high_memory(self):
        result = analyze_utilization(CURRENT, [50.0], [90.0], [10.0])
        assert result["classification"] == "UNDER_PROVISIONED"

    def test_optimized(self):
        result = analyze_utilization(CURRENT, [50.0], [60.0], [40.0])
        assert result["classification"] == "OPTIMIZED"
        assert result["recommendation"] is None

    def test_optimized_lower_boundary(self):
        result = analyze_utilization(CURRENT, [30.0], [50.0], [10.0])
        assert result["classification"] == "OPTIMIZED"


class TestRecommendation:
    def test_over_provisioned_suggests_smaller_instance(self):
        result = analyze_utilization(CURRENT, [20.0], [30.0], [5.0])
        rec = result["recommendation"]
        assert rec is not None
        assert rec["instance_id"] == "aws-t3-xlarge"
        assert rec["vcpu"] == 4
        assert rec["memory_gb"] == 16
        assert rec["vcpu"] < 8

    def test_savings_calculation(self):
        result = analyze_utilization(CURRENT, [20.0], [30.0], [5.0])
        rec = result["recommendation"]
        assert rec["monthly_savings"] == 158.85
        assert rec["yearly_savings"] == 1906.18

    def test_required_capacity_includes_headroom(self):
        result = analyze_utilization(CURRENT, [20.0], [30.0], [5.0])
        assert result["required_vcpu_with_headroom"] == 1.92
        assert result["required_memory_gb_with_headroom"] == 11.52

    def test_no_recommendation_when_under_provisioned(self):
        result = analyze_utilization(CURRENT, [90.0], [95.0], [80.0])
        assert result["recommendation"] is None


class TestUtilizationStats:
    def test_stats_percentiles_present_and_ordered(self):
        samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        result = analyze_utilization(CURRENT, samples, samples, samples)
        for metric in ("cpu", "memory", "network"):
            stats = result["utilization"][metric]
            assert stats["p50"] <= stats["p90"] <= stats["p95"] <= stats["p99"]

    def test_current_instance_echoed(self):
        result = analyze_utilization(CURRENT, [50.0], [50.0], [50.0])
        assert result["current_instance"]["id"] == CURRENT
        assert result["current_instance"]["vcpu"] == 8
        assert result["current_instance"]["memory_gb"] == 32


class TestErrorHandling:
    def test_unknown_instance_raises(self):
        with pytest.raises(ValueError, match="unknown instance id"):
            analyze_utilization("no-such-instance", [50.0], [50.0], [50.0])


# cpu 3 / mem 8 are below the IDLE cut-offs (cpu<5 and mem<10), so these
# samples make the classification depend on the network check alone.
IDLE_CPU = [3.0]
IDLE_MEM = [8.0]

_BASE_ROW = {
    "provider": "aws", "family": "general", "instance_type": "m5.2xlarge",
    "vcpu": 8, "memory_gb": 32, "storage_gb": None,
    "hourly_cost_usd": 0.384, "region": "us-east-1",
    "description": "General purpose",
}
_DECLARED_LINK = dict(_BASE_ROW, id="test-declared-link", network_gbps=10)
_ZERO_LINK = dict(_BASE_ROW, id="test-zero-link", network_gbps=0)
_NO_LINK_FIELD = {k: v for k, v in _BASE_ROW.items() if k != "network_gbps"}
_NO_LINK_FIELD["id"] = "test-missing-link"


class TestNetworkSaturation:
    """network_utilization samples are PERCENT OF LINK -- models.py's
    RightSizeRequest validator rejects anything outside 0-100 -- so they are
    compared against a percent threshold. network_gbps is only a >0 gate on
    whether a percent of link means anything; it is never multiplied in."""

    def test_threshold_is_eighty_percent_of_link(self):
        assert NET_SATURATION_PERCENT == 80.0

    def test_network_alone_marks_under_provisioned_with_idle_cpu_and_mem(self):
        result = analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [95.0])
        assert result["utilization"]["network"]["p95"] == 95.0
        assert result["classification"] == "UNDER_PROVISIONED"
        assert result["recommendation"] is None

    def test_network_is_classified_before_the_idle_branch(self):
        # Same cpu/mem, opposite network. The IDLE verdict on cpu/mem alone must
        # not be able to pre-empt a saturated link.
        assert analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [1.0])["classification"] == "IDLE"
        assert analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [95.0])["classification"] == "UNDER_PROVISIONED"

    def test_network_below_threshold_is_not_under_provisioned(self):
        assert analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [40.0])["classification"] == "IDLE"
        # 65% of link is comfortably under 80% but over 50%, so it also pins
        # the threshold value rather than just the comparison direction.
        assert analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [65.0])["classification"] == "IDLE"

    def test_exact_threshold_is_not_saturated(self):
        result = analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [80.0])
        assert result["utilization"]["network"]["p95"] == 80.0
        assert result["classification"] == "IDLE"  # strict >, not >=

    @pytest.mark.parametrize("p95", [80.01, 81.0, 95.0, 100.0])
    def test_just_over_the_threshold_is_saturated(self, p95):
        result = analyze_utilization(CURRENT, IDLE_CPU, IDLE_MEM, [p95])
        assert result["utilization"]["network"]["p95"] == p95
        assert result["classification"] == "UNDER_PROVISIONED"

    def test_saturation_needs_a_declared_bandwidth(self, monkeypatch):
        # A percent of link is meaningless when the catalog declares no link,
        # so those rows must fall through to the cpu/mem verdict (IDLE here)
        # instead of reporting a saturated network. The declared row in the same
        # catalog proves the gate is the only difference.
        monkeypatch.setattr(rightsizer, "load_instances",
                            lambda: [_DECLARED_LINK, _ZERO_LINK, _NO_LINK_FIELD])
        for instance_id in ("test-zero-link", "test-missing-link"):
            result = analyze_utilization(instance_id, IDLE_CPU, IDLE_MEM, [95.0])
            assert result["classification"] == "IDLE", instance_id
        assert analyze_utilization("test-declared-link", IDLE_CPU, IDLE_MEM, [95.0])["classification"] == "UNDER_PROVISIONED"
