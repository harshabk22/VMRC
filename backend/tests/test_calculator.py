import pytest
from pydantic import ValidationError

from calculator import (calculate_resources, find_matching_instances,
                        load_instances, recommend_for_workload)
from models import WorkloadRequest


def make_workload(**overrides):
    params = dict(
        workload_type="web",
        concurrent_users=100,
        cpu_per_user=5.0,
        memory_per_user_mb=256.0,
        storage_gb=100.0,
        bandwidth_mbps=50.0,
        growth_factor=1.5,
        headroom_percent=20.0,
    )
    params.update(overrides)
    return WorkloadRequest(**params)


class TestCalculateResources:
    def test_medium_workload_exact_numbers(self):
        result = calculate_resources(make_workload())
        assert result["required_vcpu"] == 9.0
        assert result["required_ram_gb"] == 45.0
        assert result["required_storage_gb"] == 180.0
        assert result["required_bandwidth_mbps"] == 90.0

    def test_small_workload(self):
        result = calculate_resources(make_workload(
            concurrent_users=10, cpu_per_user=2.0, memory_per_user_mb=128.0,
            storage_gb=0, bandwidth_mbps=0, growth_factor=1.0,
        ))
        assert result["required_vcpu"] == 0.24
        assert result["required_ram_gb"] == 1.5
        assert result["required_storage_gb"] == 0.0
        assert result["required_bandwidth_mbps"] == 0.0

    def test_large_workload(self):
        result = calculate_resources(make_workload(
            workload_type="database",
            concurrent_users=10000, cpu_per_user=10.0, memory_per_user_mb=512.0,
            storage_gb=1000.0, bandwidth_mbps=500.0,
            growth_factor=2.0, headroom_percent=50.0,
        ))
        assert result["required_vcpu"] == 3000.0
        assert result["required_ram_gb"] == 15000.0
        assert result["required_storage_gb"] == 3000.0
        assert result["required_bandwidth_mbps"] == 1500.0

    def test_default_growth_and_headroom(self):
        result = calculate_resources(make_workload(
            concurrent_users=50, cpu_per_user=4.0, memory_per_user_mb=128.0,
            growth_factor=1.0, headroom_percent=20.0,
        ))
        assert result["required_vcpu"] == 2.4
        assert result["required_ram_gb"] == 7.5

    def test_zero_headroom(self):
        result = calculate_resources(make_workload(
            concurrent_users=100, cpu_per_user=5.0, memory_per_user_mb=256.0,
            growth_factor=1.0, headroom_percent=0.0,
        ))
        assert result["required_vcpu"] == 5.0
        assert result["required_ram_gb"] == 25.0

    def test_result_echoes_workload_inputs(self):
        result = calculate_resources(make_workload(workload_type="batch"))
        assert result["workload_type"] == "batch"
        assert result["concurrent_users"] == 100
        assert result["growth_factor"] == 1.5
        assert result["headroom_percent"] == 20.0


class TestFindMatchingInstances:
    def test_returns_only_sufficient_instances_sorted_by_cost(self):
        matches = find_matching_instances(4, 16)
        assert len(matches) > 0
        assert all(m["vcpu"] >= 4 and m["memory_gb"] >= 16 for m in matches)
        costs = [m["hourly_cost_usd"] for m in matches]
        assert costs == sorted(costs)

    def test_provider_filter(self):
        matches = find_matching_instances(2, 4, provider="aws")
        assert len(matches) > 0
        assert all(m["provider"] == "aws" for m in matches)

    def test_no_match_returns_empty(self):
        assert find_matching_instances(1000, 100000) == []

    def test_tiny_requirements_match_cheapest(self):
        matches = find_matching_instances(0.1, 0.1)
        cheapest = min(load_instances(), key=lambda i: i["hourly_cost_usd"])
        assert matches[0]["id"] == cheapest["id"]


class TestRecommendForWorkload:
    def test_recommendations_for_all_three_providers(self):
        result = recommend_for_workload(make_workload())
        assert set(result["recommendations"].keys()) == {"aws", "azure", "gcp"}
        for provider, recs in result["recommendations"].items():
            assert len(recs) > 0
            assert len(recs) <= 3
            assert all(r["provider"] == provider for r in recs)

    def test_total_matches_consistent(self):
        result = recommend_for_workload(make_workload())
        recd = sum(len(v) for v in result["recommendations"].values())
        assert result["total_matches"] >= recd

    def test_resources_block_present(self):
        result = recommend_for_workload(make_workload())
        assert result["resources"]["required_vcpu"] == 9.0
        assert result["resources"]["required_ram_gb"] == 45.0

    def test_cheapest_recommended_per_provider(self):
        result = recommend_for_workload(make_workload(concurrent_users=1))
        for recs in result["recommendations"].values():
            assert recs[0]["hourly_cost_usd"] == min(
                r["hourly_cost_usd"] for r in recs
            )


class TestWorkloadValidation:
    def test_zero_users_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(concurrent_users=0)

    def test_negative_users_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(concurrent_users=-5)

    def test_negative_cpu_per_user_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(cpu_per_user=-1.0)

    def test_negative_memory_per_user_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(memory_per_user_mb=-100.0)

    def test_negative_storage_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(storage_gb=-1.0)

    def test_negative_bandwidth_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(bandwidth_mbps=-1.0)

    def test_growth_factor_below_one_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(growth_factor=0.5)

    def test_headroom_above_100_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(headroom_percent=150.0)

    def test_headroom_negative_rejected(self):
        with pytest.raises(ValidationError):
            make_workload(headroom_percent=-10.0)

    def test_minimal_valid_workload(self):
        workload = WorkloadRequest(concurrent_users=1, cpu_per_user=1.0, memory_per_user_mb=128.0)
        assert workload.storage_gb == 0
        assert workload.growth_factor == 1.0
        assert workload.headroom_percent == 20.0
