import pytest

pytestmark = pytest.mark.asyncio

VALID_WORKLOAD = {
    "workload_type": "web",
    "concurrent_users": 100,
    "cpu_per_user": 5.0,
    "memory_per_user_mb": 256.0,
    "storage_gb": 100.0,
    "bandwidth_mbps": 50.0,
    "growth_factor": 1.5,
    "headroom_percent": 20.0,
}


class TestInstances:
    async def test_list_instances(self, client):
        resp = await client.get("/api/instances")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == len(body["instances"])
        assert body["count"] > 0

    async def test_list_instances_provider_filter(self, client):
        resp = await client.get("/api/instances", params={"provider": "aws"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] > 0
        assert all(i["provider"] == "aws" for i in body["instances"])

    async def test_list_instances_family_filter(self, client):
        resp = await client.get("/api/instances", params={"family": "compute"})
        assert resp.status_code == 200
        body = resp.json()
        assert all(i["family"] == "compute" for i in body["instances"])

    async def test_list_instances_vcpu_range_filter(self, client):
        resp = await client.get(
            "/api/instances", params={"min_vcpu": 4, "max_vcpu": 8}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(4 <= i["vcpu"] <= 8 for i in body["instances"])

    async def test_list_instances_ram_range_filter(self, client):
        resp = await client.get(
            "/api/instances", params={"min_ram": 16, "max_ram": 64}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(16 <= i["memory_gb"] <= 64 for i in body["instances"])

    async def test_get_instance_by_id(self, client):
        resp = await client.get("/api/instances/aws-m5-large")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "aws-m5-large"
        assert body["provider"] == "aws"
        assert body["vcpu"] == 2

    async def test_get_instance_not_found(self, client):
        resp = await client.get("/api/instances/no-such-instance")
        assert resp.status_code == 404

    async def test_list_providers(self, client):
        resp = await client.get("/api/providers")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["providers"]]
        assert names == ["aws", "azure", "gcp"]


class TestHealth:
    async def test_health_returns_ok(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["instances_count"] == 61
        assert body["providers"] == ["aws", "azure", "gcp"]


class TestCalculate:
    async def test_calculate_valid_input(self, client):
        resp = await client.post("/api/calculate", json=VALID_WORKLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["resources"]["required_vcpu"] == 9.0
        assert body["resources"]["required_ram_gb"] == 45.0
        assert set(body["recommendations"].keys()) == {"aws", "azure", "gcp"}

    async def test_calculate_defaults_applied(self, client):
        resp = await client.post(
            "/api/calculate",
            json={"concurrent_users": 50, "cpu_per_user": 4.0, "memory_per_user_mb": 128.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resources"]["growth_factor"] == 1.0
        assert body["resources"]["headroom_percent"] == 20.0

    async def test_calculate_invalid_input_rejected(self, client):
        bad = dict(VALID_WORKLOAD, concurrent_users=0)
        resp = await client.post("/api/calculate", json=bad)
        assert resp.status_code == 400

    async def test_calculate_missing_required_field(self, client):
        resp = await client.post("/api/calculate", json={"cpu_per_user": 5.0})
        assert resp.status_code == 400


class TestCompare:
    async def test_compare_valid_input(self, client):
        resp = await client.post(
            "/api/compare",
            json={"instance_ids": ["aws-m5-large", "aws-m5-xlarge", "gcp-e2-medium"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["instances"]) == 3
        assert body["cheapest_id"] == "gcp-e2-medium"
        assert body["comparison"]["max_vcpu"] == 4
        assert body["comparison"]["min_hourly_cost_usd"] == 0.0335  # gcp-e2-medium, shared-core rate

    async def test_compare_unknown_instance_returns_400(self, client):
        resp = await client.post(
            "/api/compare", json={"instance_ids": ["no-such-instance"]}
        )
        assert resp.status_code == 400

    async def test_compare_across_providers(self, client):
        resp = await client.post(
            "/api/compare/providers", params={"vcpu": 4, "memory_gb": 16}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["requirements"] == {"vcpu": 4, "memory_gb": 16}
        assert set(body["equivalents"].keys()) == {"aws", "azure", "gcp"}
        assert all(e is not None for e in body["equivalents"].values())


class TestCost:
    async def test_cost_valid_input(self, client):
        resp = await client.post(
            "/api/cost",
            json={
                "instance_ids": ["aws-m5-large"],
                "pricing_model": "ondemand",
                "hours_per_month": 730,
                "storage_gb": 100,
                "data_egress_gb": 500,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pricing_model"] == "ondemand"
        # compute 0.096*730 = 70.08 | storage 100GB @ aws gp3 0.08 = 8.00 | egress 500GB @ 0.09 = 45.00
        assert body["grand_monthly_total"] == 123.08
        assert body["grand_yearly_total"] == 1476.96

    async def test_cost_reserved_pricing(self, client):
        resp = await client.post(
            "/api/cost",
            json={
                "instance_ids": ["aws-m5-large"],
                "pricing_model": "reserved_3yr",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["grand_monthly_total"] == 31.54

    async def test_cost_invalid_pricing_model_returns_400(self, client):
        resp = await client.post(
            "/api/cost",
            json={"instance_ids": ["aws-m5-large"], "pricing_model": "free"},
        )
        assert resp.status_code == 400

    async def test_cost_unknown_instance_returns_400(self, client):
        resp = await client.post(
            "/api/cost", json={"instance_ids": ["no-such-instance"]}
        )
        assert resp.status_code == 400


class TestFleet:
    async def test_fleet_valid_input(self, client):
        resp = await client.post(
            "/api/fleet",
            json={
                "vms": [
                    {"instance_id": "aws-m5-large", "count": 3},
                    {"instance_id": "gcp-e2-medium", "count": 2},
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_vms"] == 5
        assert body["total_vcpu"] == 10
        assert body["total_memory_gb"] == 32.0
        assert len(body["breakdown"]) == 2
        assert body["total_hourly_cost_usd"] == 0.355  # 3*0.096 + 2*0.0335

    async def test_fleet_unknown_instance_returns_400(self, client):
        resp = await client.post(
            "/api/fleet",
            json={"vms": [{"instance_id": "no-such-instance", "count": 1}]},
        )
        assert resp.status_code == 400

    async def test_fleet_empty_vms_rejected(self, client):
        resp = await client.post("/api/fleet", json={"vms": []})
        assert resp.status_code == 400


class TestRightSize:
    async def test_rightsize_valid_input(self, client):
        resp = await client.post(
            "/api/rightsize",
            json={
                "current_instance_id": "aws-m5-2xlarge",
                "cpu_utilization": [20.0],
                "memory_utilization": [30.0],
                "network_utilization": [5.0],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["classification"] == "OVER_PROVISIONED"
        assert body["recommendation"]["instance_id"] == "aws-t3-xlarge"

    async def test_rightsize_under_provisioned(self, client):
        resp = await client.post(
            "/api/rightsize",
            json={
                "current_instance_id": "aws-m5-2xlarge",
                "cpu_utilization": [90.0],
                "memory_utilization": [95.0],
                "network_utilization": [80.0],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["classification"] == "UNDER_PROVISIONED"

    async def test_rightsize_unknown_instance_returns_400(self, client):
        resp = await client.post(
            "/api/rightsize",
            json={
                "current_instance_id": "no-such-instance",
                "cpu_utilization": [50.0],
                "memory_utilization": [50.0],
                "network_utilization": [50.0],
            },
        )
        assert resp.status_code == 400


class TestScenarios:
    async def test_save_and_list(self, client):
        save_resp = await client.post(
            "/api/scenarios",
            json={
                "name": "web-prod",
                "request_type": "calculate",
                "request": VALID_WORKLOAD,
                "result": {"required_vcpu": 9.0},
            },
        )
        assert save_resp.status_code == 200
        scenario_id = save_resp.json()["id"]

        list_resp = await client.get("/api/scenarios")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) == 1
        assert items[0]["id"] == scenario_id
        assert items[0]["name"] == "web-prod"
        assert items[0]["request_type"] == "calculate"

    async def test_get_scenario(self, client):
        save_resp = await client.post(
            "/api/scenarios",
            json={
                "name": "db-batch",
                "request_type": "cost",
                "request": {"instance_ids": ["aws-m5-large"]},
                "result": {},
            },
        )
        scenario_id = save_resp.json()["id"]

        resp = await client.get("/api/scenarios/{}".format(scenario_id))
        assert resp.status_code == 200
        assert resp.json()["name"] == "db-batch"

    async def test_get_missing_scenario_returns_404(self, client):
        resp = await client.get("/api/scenarios/9999")
        assert resp.status_code == 404

    async def test_delete_scenario(self, client):
        save_resp = await client.post(
            "/api/scenarios",
            json={
                "name": "temp",
                "request_type": "calculate",
                "request": VALID_WORKLOAD,
                "result": {},
            },
        )
        scenario_id = save_resp.json()["id"]

        del_resp = await client.delete("/api/scenarios/{}".format(scenario_id))
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"

        list_resp = await client.get("/api/scenarios")
        assert list_resp.json() == []

    async def test_delete_missing_scenario_returns_404(self, client):
        resp = await client.delete("/api/scenarios/9999")
        assert resp.status_code == 404

    async def test_save_scenario_requires_name(self, client):
        resp = await client.post(
            "/api/scenarios",
            json={"request_type": "calculate", "request": {}, "result": {}},
        )
        assert resp.status_code == 400


class TestBulkCompare:
    async def test_bulk_compare_valid_range(self, client):
        resp = await client.post(
            "/api/compare/bulk",
            json={"vcpu_range": [4, 8], "memory_range": [8, 32]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == len(body["instances"])
        assert body["count"] > 0
        assert all(4 <= i["vcpu"] <= 8 and 8 <= i["memory_gb"] <= 32 for i in body["instances"])
        costs = [i["hourly_cost_usd"] for i in body["instances"]]
        assert costs == sorted(costs)

    async def test_bulk_compare_invalid_range_rejected(self, client):
        resp = await client.post(
            "/api/compare/bulk",
            json={"vcpu_range": [8, 4], "memory_range": [8, 32]},
        )
        assert resp.status_code == 400


class TestOptimize:
    async def test_optimize_returns_cheapest_match(self, client):
        resp = await client.post(
            "/api/optimize",
            json={
                "current_instance_id": "aws-m5-2xlarge",
                "cpu_utilization": [20.0],
                "memory_utilization": [30.0],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_instance"]["id"] == "aws-m5-2xlarge"
        assert body["p95_cpu_utilization"] == 20.0
        assert body["recommended_instance"]["vcpu"] >= body["required_vcpu_with_headroom"]
        assert body["recommended_instance"]["memory_gb"] >= body["required_memory_gb_with_headroom"]
        assert body["monthly_savings"] > 0

    async def test_optimize_unknown_instance_returns_400(self, client):
        resp = await client.post(
            "/api/optimize",
            json={
                "current_instance_id": "no-such-instance",
                "cpu_utilization": [50.0],
                "memory_utilization": [50.0],
            },
        )
        assert resp.status_code == 400


class TestExport:
    async def test_export_csv(self, client):
        resp = await client.post(
            "/api/export/csv",
            json={
                "data": [
                    {"instance": "aws-m5-large", "cost": 0.096},
                    {"instance": "gcp-e2-medium", "cost": 0.067},
                ],
                "filename": "instances.csv",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.text.splitlines()[0] == "instance,cost"
        assert len(resp.text.splitlines()) == 3
