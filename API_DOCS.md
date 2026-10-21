# VMRC API Documentation

Base URL: `http://localhost:8000`
Content type: `application/json` (except CSV export)
Interactive OpenAPI docs: `http://localhost:8000/docs`

## Conventions

### Error format

All errors return a JSON body:

```json
{ "error": "<message>", "details": "" }
```

Validation errors (HTTP 400) include the parser details:

```json
{ "error": "validation_error", "details": "[{\"loc\": [\"body\", \"concurrent_users\"], ...}]" }
```

### Status codes

| Code | Meaning |
|------|---------|
| 200  | Success |
| 400  | Validation error or bad request (unknown IDs, unknown pricing model, invalid ranges) |
| 404  | Resource not found |
| 500  | Unhandled server error |

---

## Health

### GET /api/health

Service health and catalog summary.

**Response 200**

```json
{
  "status": "ok",
  "instances_count": 61,
  "providers": ["aws", "azure", "gcp"]
}
```

---

## Instances

### GET /api/instances

List instance catalog entries.

**Query parameters** (all optional)

| Param | Type | Description |
|-------|------|-------------|
| `provider` | string | Filter by provider: `aws`, `azure`, `gcp` |
| `family` | string | Filter by family: `general`, `compute`, `memory`, `storage`, `burstable` |
| `min_vcpu` | int | Minimum vCPUs (1–512) |
| `max_vcpu` | int | Maximum vCPUs (1–512) |
| `min_ram` | float | Minimum memory GB (0–4096) |
| `max_ram` | float | Maximum memory GB (0–4096) |

**Response 200**

```json
{
  "count": 1,
  "instances": [
    {
      "id": "aws-m5-large",
      "provider": "aws",
      "family": "general",
      "instance_type": "m5.large",
      "vcpu": 2,
      "memory_gb": 8.0,
      "storage_gb": null,
      "network_gbps": 10.0,
      "hourly_cost_usd": 0.096,
      "region": "us-east-1",
      "description": "General purpose"
    }
  ]
}
```

**Example**

```bash
curl "http://localhost:8000/api/instances?provider=aws&min_vcpu=4"
```

---

### GET /api/instances/{instance_id}

Get a single instance by ID.

**Response 200** — the instance object (shape as above).

**Error 404**

```json
{ "error": "instance not found", "details": "" }
```

---

### GET /api/providers

List providers and the instance families available in each.

**Response 200**

```json
{
  "providers": [
    {
      "name": "aws",
      "families": [
        { "name": "burstable", "count": 3 },
        { "name": "compute", "count": 5 },
        { "name": "general", "count": 6 },
        { "name": "memory", "count": 5 },
        { "name": "storage", "count": 2 }
      ]
    }
  ]
}
```

---

## Live pricing

Prices come from two places, and every response says which. Azure is fetched
live from the unauthenticated Retail Prices API and cached for 24h; AWS and GCP
stay on the verified snapshot (`instances.json`) because neither exposes an
unauthenticated price feed.

### GET /api/pricing/status

Provenance per provider: `live`, `live-stale`, or `snapshot_only`.

**Response 200**

```json
{
  "providers": {
    "azure": { "mode": "live", "region": "eastus", "source": "azure-retail-live", "fetched_at": "2026-10-08T04:13:13+00:00", "count": 20 },
    "aws": { "mode": "snapshot_only", "region": "us-east-1", "source": "verified-snapshot instances.json (no unauthenticated feed)", "fetched_at": null, "count": 0 }
  }
}
```

### POST /api/pricing/refresh?region=eastus

Re-fetch all 20 Azure SKU prices and re-cache them. Returns
`{"region": "eastus", "refreshed": 20, "source": "azure-retail-live"}`.
Upstream failure returns 502; the snapshot keeps serving.

`GET /api/instances` accepts `?region=` (filter by priced region) and
`?live=true` (overlay cached live Azure prices; rows carry `price_source` and
`price_fetched_at`).

## Telemetry

### POST /api/telemetry/fetch

Fetch live utilization from the provider — or refuse loudly. GCP uses your ADC
(`gcloud auth application-default login`); AWS/Azure return 400 naming exactly
what to configure. Nothing here invents numbers.

```json
{ "provider": "gcp", "project": "my-proj", "instance": "vm-1", "zone": "us-central1-a", "days": 14 }
```

## Calculator

### POST /api/calculate

Calculate required resources for a workload and recommend the cheapest suitable instances per provider.

**Request body** — `WorkloadRequest`

```json
{
  "workload_type": "web",
  "concurrent_users": 100,
  "cpu_per_user": 5.0,
  "memory_per_user_mb": 256.0,
  "storage_gb": 100.0,
  "bandwidth_mbps": 50.0,
  "growth_factor": 1.5,
  "headroom_percent": 20.0
}
```

| Field | Type | Default | Constraints |
|-------|------|---------|-------------|
| `workload_type` | string | `"web"` | free label, e.g. `web`, `database`, `batch` |
| `concurrent_users` | int | **required** | 1 – 1,000,000 |
| `cpu_per_user` | float | **required** | 0 – 100 |
| `memory_per_user_mb` | float | **required** | 0 – 1,048,576 |
| `storage_gb` | float | `0` | 0 – 1,048,576 |
| `bandwidth_mbps` | float | `0` | 0 – 100,000 |
| `growth_factor` | float | `1.0` | 1.0 – 100 |
| `headroom_percent` | float | `20.0` | 0 – 100 |

**Response 200**

```json
{
  "resources": {
    "workload_type": "web",
    "concurrent_users": 100,
    "growth_factor": 1.5,
    "headroom_percent": 20.0,
    "required_vcpu": 9.0,
    "required_ram_gb": 45.0,
    "required_storage_gb": 180.0,
    "required_bandwidth_mbps": 90.0
  },
  "total_matches": 14,
  "recommendations": {
    "aws": [ { "id": "aws-m5-4xlarge", "...": "..." } ],
    "azure": [ "..." ],
    "gcp": [ "..." ]
  }
}
```

Each provider list contains up to 3 instances (full instance objects) sorted by hourly cost.

**Errors** — `400` validation error when constraints are violated (e.g. `concurrent_users: 0`).

---

## Comparison

### POST /api/compare

Compare instances side by side.

**Request body** — `CompareRequest`

```json
{ "instance_ids": ["aws-m5-large", "aws-m5-xlarge", "gcp-e2-medium"] }
```

`instance_ids`: 1 – 50 IDs.

**Response 200**

```json
{
  "instances": [ { "id": "aws-m5-large", "...": "..." } ],
  "cheapest_id": "gcp-e2-medium",
  "comparison": {
    "max_vcpu": 4,
    "max_memory_gb": 16.0,
    "max_hourly_cost_usd": 0.192,
    "min_hourly_cost_usd": 0.067
  }
}
```

**Errors** — `400` `unknown instance ids: ...` for unknown IDs.

---

### POST /api/compare/providers

Find the cheapest instance per provider meeting a spec.

**Query parameters** (required)

| Param | Type | Constraints |
|-------|------|-------------|
| `vcpu` | int | 1 – 512 |
| `memory_gb` | float | 0 – 4096 |

**Response 200**

```json
{
  "requirements": { "vcpu": 4, "memory_gb": 16.0 },
  "equivalents": {
    "aws": { "id": "aws-m5-xlarge", "...": "..." },
    "azure": { "id": "azure-d4s-v5", "...": "..." },
    "gcp": { "id": "gcp-n2-standard-4", "...": "..." }
  }
}
```

A provider value is `null` when no catalog instance meets the spec.

---

### POST /api/compare/bulk

Filter the whole catalog by spec range, sorted by hourly cost.

**Request body** — `BulkCompareRequest`

```json
{ "vcpu_range": [4, 8], "memory_range": [8, 32] }
```

`vcpu_range`: `[min >= 1, max <= 512]`, `min <= max`.
`memory_range`: `[min >= 0.5, max <= 4096]`, `min <= max`.

**Response 200**

```json
{
  "count": 26,
  "vcpu_range": [4, 8],
  "memory_range": [8, 32],
  "instances": [ { "id": "aws-t3-xlarge", "...": "..." } ]
}
```

**Errors** — `400` validation error for inverted or out-of-bounds ranges.

---

## Cost

### POST /api/cost

Estimate monthly and yearly cost for a set of instances under a pricing model.

**Request body** — `CostRequest`

```json
{
  "instance_ids": ["aws-m5-large", "aws-m5-xlarge"],
  "pricing_model": "reserved_1yr",
  "hours_per_month": 730,
  "storage_gb": 100,
  "data_egress_gb": 500
}
```

| Field | Type | Default | Constraints |
|-------|------|---------|-------------|
| `instance_ids` | string[] | **required** | 1 – 50 IDs |
| `pricing_model` | string | `"ondemand"` | `ondemand`, `reserved_1yr`, `reserved_3yr`, `spot` |
| `hours_per_month` | float | `730` | 0 – 744 |
| `storage_gb` | float | `0` | 0 – 16,384 (per instance; AWS gp3 $0.08, Azure Premium SSD v2 $0.079, GCP pd-balanced $0.10 per GB-month) |
| `data_egress_gb` | float | `0` | 0 – 1,048,576 (per instance; AWS $0.09, Azure $0.087, GCP Premium $0.12 per GB) |

**Response 200**

```json
{
  "pricing_model": "reserved_1yr",
  "hours_per_month": 730,
  "storage_gb_per_instance": 100,
  "data_egress_gb_per_instance": 500,
  "instances": [
    {
      "instance_id": "aws-m5-large",
      "instance_type": "m5.large",
      "provider": "aws",
      "pricing_model": "reserved_1yr",
      "compute_monthly": 44.15,
      "storage_monthly": 8.0,
      "egress_monthly": 45.0,
      "monthly_total": 97.15,
      "yearly_total": 1165.8
    },
    {
      "instance_id": "aws-m5-xlarge",
      "instance_type": "m5.xlarge",
      "provider": "aws",
      "pricing_model": "reserved_1yr",
      "compute_monthly": 88.3,
      "storage_monthly": 8.0,
      "egress_monthly": 45.0,
      "monthly_total": 141.3,
      "yearly_total": 1695.61
    }
  ],
  "grand_monthly_total": 238.45,
  "grand_yearly_total": 2861.41
}
```

Both instances are AWS, so each row draws the AWS rates: storage 100 GB x $0.08 = $8.00, egress 500 GB x $0.09 = $45.00. Compute is `hourly_cost_usd x 730 x 0.63` (the `reserved_1yr` multiplier) — $44.15 and $88.30 respectively. The grand totals are the unrounded row sums, not the rounded ones.

**Errors** — `400` for unknown pricing model or unknown instance IDs.

---

## Fleet

### POST /api/fleet

Aggregate capacity and cost across a heterogeneous fleet.

**Request body** — `FleetRequest`

```json
{
  "vms": [
    { "instance_id": "aws-m5-large", "count": 3 },
    { "instance_id": "gcp-e2-medium", "count": 2 }
  ]
}
```

`vms`: 1 – 100 entries; `count`: 1 – 10,000 each.

**Response 200**

```json
{
  "total_vms": 5,
  "total_vcpu": 10,
  "total_memory_gb": 32.0,
  "total_hourly_cost_usd": 0.422,
  "total_monthly_cost_usd": 308.06,
  "breakdown": [
    {
      "instance_id": "aws-m5-large",
      "instance_type": "m5.large",
      "provider": "aws",
      "count": 3,
      "vcpu": 6,
      "memory_gb": 24,
      "hourly_cost_usd": 0.288,
      "monthly_cost_usd": 210.24
    },
    {
      "instance_id": "gcp-e2-medium",
      "instance_type": "e2-medium",
      "provider": "gcp",
      "count": 2,
      "vcpu": 4,
      "memory_gb": 8,
      "hourly_cost_usd": 0.134,
      "monthly_cost_usd": 97.82
    }
  ]
}
```

**Errors** — `400` for unknown instance IDs; `400` validation error for empty `vms`.

---

## Right-sizing

### POST /api/rightsize

Classify current utilization and, when over-provisioned, recommend a smaller instance.

**Request body** — `RightSizeRequest`

```json
{
  "current_instance_id": "aws-m5-2xlarge",
  "cpu_utilization": [12.0, 18.0, 25.0, 31.0, 22.0],
  "memory_utilization": [28.0, 35.0, 41.0, 30.0, 26.0],
  "network_utilization": [4.0, 6.0, 9.0, 5.0, 7.0]
}
```

Utilization samples: 1 – 10,000 values each, all between 0 and 100.

**Classification rules** — evaluated in this order; the first match wins, and the network saturation check short-circuits the rest.

| Order | Classification | Condition |
|-------|----------------|-----------|
| 1 | `UNDER_PROVISIONED` | p95 network > 80% |
| 2 | `IDLE` | p95 CPU < 5% **and** p95 memory < 10% |
| 3 | `UNDER_PROVISIONED` | p95 CPU > 80% **or** p95 memory > 85% |
| 4 | `OVER_PROVISIONED` | p95 CPU < 30% **and** p95 memory < 50% |
| 5 | `OPTIMIZED` | everything else |

Network samples are already percent-of-link, so rule 1 only applies when the catalog declares a non-zero `network_gbps` for the instance; for instances with no declared bandwidth the check is skipped and rule 2 is evaluated first.

**Response 200**

```json
{
  "current_instance": { "id": "aws-m5-2xlarge", "...": "..." },
  "utilization": {
    "cpu":     { "p50": 22.0, "p90": 28.6, "p95": 29.8, "p99": 30.76 },
    "memory":  { "p50": 30.0, "p90": 38.6, "p95": 39.8, "p99": 40.76 },
    "network": { "p50": 6.0,  "p90": 8.2,  "p95": 8.6,  "p99": 8.92 }
  },
  "classification": "OVER_PROVISIONED",
  "required_vcpu_with_headroom": 2.86,
  "required_memory_gb_with_headroom": 15.28,
  "recommendation": {
    "instance_id": "aws-t3-xlarge",
    "instance_type": "t3.xlarge",
    "vcpu": 4,
    "memory_gb": 16.0,
    "hourly_cost_usd": 0.1664,
    "monthly_savings": 158.85,
    "yearly_savings": 1906.18
  }
}
```

`recommendation` is `null` unless the classification is `OVER_PROVISIONED` and a smaller same-provider instance fits the p95 requirement plus 20% headroom.

**Errors** — `400` for unknown instance ID or out-of-range utilization samples.

---

### POST /api/optimize

Find the cheapest instance in the **entire catalog** (any provider) that meets the p95 utilization with 20% headroom.

**Request body** — `OptimizeRequest`

```json
{
  "current_instance_id": "aws-m5-2xlarge",
  "cpu_utilization": [20.0, 22.0, 18.0],
  "memory_utilization": [30.0, 28.0, 33.0]
}
```

**Response 200**

```json
{
  "current_instance": {
    "id": "aws-m5-2xlarge",
    "instance_type": "m5.2xlarge",
    "provider": "aws",
    "vcpu": 8,
    "memory_gb": 32.0,
    "hourly_cost_usd": 0.384
  },
  "p95_cpu_utilization": 21.8,
  "p95_memory_utilization": 32.7,
  "required_vcpu_with_headroom": 2.09,
  "required_memory_gb_with_headroom": 12.56,
  "recommended_instance": {
    "id": "gcp-e2-standard-4",
    "instance_type": "e2-standard-4",
    "provider": "gcp",
    "vcpu": 4,
    "memory_gb": 16,
    "hourly_cost_usd": 0.1341
  },
  "monthly_savings": 182.43,
  "yearly_savings": 2189.12,
  "current_monthly_cost": 280.32,
  "recommended_monthly_cost": 97.89
}
```

**Errors** — `400` for unknown instance ID, out-of-range samples, or when no catalog instance meets the requirement.

---

## Export

### POST /api/export/csv

Export rows as a CSV file download.

**Request body** — `ExportRequest`

```json
{
  "data": [
    { "instance": "aws-m5-large", "vcpu": 2, "cost": 0.096 },
    { "instance": "gcp-e2-medium", "vcpu": 2, "cost": 0.067 }
  ],
  "filename": "instances.csv"
}
```

**Response 200** — `text/csv` attachment:

```csv
instance,vcpu,cost
aws-m5-large,2,0.096
gcp-e2-medium,2,0.067
```

**Errors** — `400` for empty `data`.

---

## Saved Calculations

### POST /api/calculations

Persist a calculation.

**Request body**

```json
{
  "name": "web-prod",
  "request": { "concurrent_users": 100, "cpu_per_user": 5.0, "...": "..." },
  "result": { "required_vcpu": 9.0, "...": "..." }
}
```

**Response 200**

```json
{ "id": 1, "status": "saved" }
```

---

### GET /api/calculations

List saved calculations, newest first.

**Response 200**

```json
[
  {
    "id": 1,
    "name": "web-prod",
    "request_json": "{ \"concurrent_users\": 100, ... }",
    "result_json": "{ \"required_vcpu\": 9.0, ... }",
    "created_at": "2026-09-29T12:00:00+00:00"
  }
]
```

---

### GET /api/calculations/{calc_id}

Fetch one saved calculation. **Error 404** — `calculation not found`.

---

### DELETE /api/calculations/{calc_id}

Delete a saved calculation.

**Response 200**

```json
{ "id": 1, "status": "deleted" }
```

**Error 404** — `calculation not found`.

---

## Saved Scenarios

### POST /api/scenarios

Persist a scenario (any request/result pair, tagged with a type).

**Request body** — `SaveScenarioRequest`

```json
{
  "name": "web-prod",
  "request_type": "calculate",
  "request": { "concurrent_users": 100, "...": "..." },
  "result": { "required_vcpu": 9.0, "...": "..." }
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `name` | string | 1 – 200 chars |
| `request_type` | string | 1 – 100 chars, e.g. `calculate`, `compare`, `cost` |
| `request` | object | any JSON object |
| `result` | object | any JSON object |

**Response 200**

```json
{ "id": 1, "status": "saved" }
```

---

### GET /api/scenarios

List saved scenarios, newest first.

**Response 200**

```json
[
  {
    "id": 1,
    "name": "web-prod",
    "request_type": "calculate",
    "request_json": "{ ... }",
    "result_json": "{ ... }",
    "created_at": "2026-09-29T12:00:00+00:00"
  }
]
```

---

### GET /api/scenarios/{scenario_id}

Fetch one saved scenario. **Error 404** — `scenario not found`.

---

### DELETE /api/scenarios/{scenario_id}

Delete a saved scenario.

**Response 200**

```json
{ "id": 1, "status": "deleted" }
```

**Error 404** — `scenario not found`.
