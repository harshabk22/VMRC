import json
import os
from contextlib import asynccontextmanager

import calculator as calc
import cloud_status
import comparator as comparator
import cost_engine
import database as db
import pricing as pricing_svc
import rightsizer
import telemetry as telemetry_svc
from exporter import export_csv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from models import (
    BulkCompareRequest,
    CompareRequest,
    CostRequest,
    ExportRequest,
    FleetRequest,
    InstanceList,
    InstanceType,
    OptimizeRequest,
    RightSizeRequest,
    SaveScenarioRequest,
    WorkloadRequest,
)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


app = FastAPI(
    title="VM Resources Calculator API",
    description="Calculate, compare, cost-estimate, and right-size cloud VMs across AWS, Azure, and GCP.",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "details": ""},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "validation_error", "details": str(exc.errors())},
    )


@app.exception_handler(ResponseValidationError)
async def response_validation_exception_handler(request: Request, exc: ResponseValidationError):
    # A bad catalog row (e.g. provider: null) fails here, inside serialize_response,
    # after the endpoint has already returned. That is a server-side data fault,
    # so 500 — but the body must still be the documented envelope. Without this
    # handler the only thing that catches it is the generic Exception handler,
    # which leaks an absolute traceback path and does not say which fault it was.
    # loc keeps the offending row: ('response', 'instances', 3, 'provider').
    return JSONResponse(
        status_code=500,
        content={"error": "response_validation_error", "details": str(exc.errors())},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "details": str(exc)},
    )


class SaveCalculationRequest(BaseModel):
    name: str
    request: dict
    result: dict


@app.get("/api/health", summary="Health check", tags=["health"])
def health():
    instances = calc.load_instances()
    return {
        "status": "ok",
        "instances_count": len(instances),
        "providers": sorted({i["provider"] for i in instances}),
    }


@app.get("/api/instances", summary="List instances", tags=["instances"])
def list_instances(
    provider: str = Query(None, description="Filter by provider: aws, azure, gcp"),
    family: str = Query(None, description="Filter by family: general, compute, memory, storage, burstable"),
    min_vcpu: int = Query(None, ge=1, le=512, description="Minimum vCPUs"),
    max_vcpu: int = Query(None, ge=1, le=512, description="Maximum vCPUs"),
    min_ram: float = Query(None, ge=0, le=4096, description="Minimum memory in GB"),
    max_ram: float = Query(None, ge=0, le=4096, description="Maximum memory in GB"),
    region: str = Query(None, description="Filter by priced region, e.g. eastus"),
    live: bool = Query(False, description="Overlay cached live Azure prices when available"),
) -> InstanceList:
    instances = calc.load_instances(region=region, live=live)
    filtered = [
        i for i in instances
        if (provider is None or i["provider"] == provider)
        and (family is None or i["family"] == family)
        and (min_vcpu is None or i["vcpu"] >= min_vcpu)
        and (max_vcpu is None or i["vcpu"] <= max_vcpu)
        and (min_ram is None or i["memory_gb"] >= min_ram)
        and (max_ram is None or i["memory_gb"] <= max_ram)
    ]
    return {"count": len(filtered), "instances": filtered}


@app.get("/api/instances/{instance_id}", summary="Get instance by ID", tags=["instances"])
def get_instance(instance_id: str) -> InstanceType:
    for inst in calc.load_instances():
        if inst["id"] == instance_id:
            return inst
    raise HTTPException(status_code=404, detail="instance not found")


@app.get("/api/cloud/status", summary="Detect local CLI session for AWS, Azure, and GCP", tags=["cloud"])
def get_cloud_status():
    return cloud_status.get_cloud_status()


@app.get("/api/pricing/status", summary="Live-pricing provenance per provider", tags=["pricing"])
def pricing_status():
    return {"providers": pricing_svc.get_status()}


@app.api_route("/api/pricing/refresh", methods=["GET", "POST"], summary="Refresh live prices from Microsoft Azure and Vantage open feeds", tags=["pricing"])
def pricing_refresh(
    provider: str = Query("all", description="Provider to refresh: 'all', 'azure', 'aws', 'gcp'"),
    region: str = Query(None, description="Optional target region, e.g. eastus, us-east-1, us-central1"),
):
    instances = calc.load_instances()
    if provider == "all":
        try:
            return pricing_svc.refresh_all(instances)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="live price refresh failed: {}".format(exc))
    elif provider == "azure":
        reg = region or "eastus"
        azure_types = sorted({i["instance_type"] for i in instances if i["provider"] == "azure"})
        try:
            prices = pricing_svc.refresh_azure(azure_types, reg)
            return {"provider": "azure", "region": reg, "refreshed": len(prices), "source": "azure-retail-live"}
        except Exception as exc:
            raise HTTPException(status_code=502, detail="azure live price fetch failed: {}".format(exc))
    elif provider == "aws":
        reg = region or "us-east-1"
        aws_types = sorted({i["instance_type"] for i in instances if i["provider"] == "aws"})
        try:
            prices = pricing_svc.refresh_aws(aws_types, reg)
            return {"provider": "aws", "region": reg, "refreshed": len(prices), "source": "vantage-ec2-live"}
        except Exception as exc:
            raise HTTPException(status_code=502, detail="aws live price fetch failed: {}".format(exc))
    elif provider == "gcp":
        reg = region or "us-central1"
        gcp_types = sorted({i["instance_type"] for i in instances if i["provider"] == "gcp"})
        try:
            prices = pricing_svc.refresh_gcp(gcp_types, reg)
            return {"provider": "gcp", "region": reg, "refreshed": len(prices), "source": "vantage-gcp-live"}
        except Exception as exc:
            raise HTTPException(status_code=502, detail="gcp live price fetch failed: {}".format(exc))
    else:
        raise HTTPException(status_code=400, detail="unknown provider: {!r}".format(provider))


@app.get("/api/providers", summary="List providers and families", tags=["instances"])
def list_providers():
    instances = calc.load_instances()
    providers = {}
    for inst in instances:
        families = providers.setdefault(inst["provider"], {})
        families.setdefault(inst["family"], 0)
        families[inst["family"]] += 1
    return {
        "providers": [
            {"name": p, "families": [{"name": f, "count": c} for f, c in sorted(fams.items())]}
            for p, fams in sorted(providers.items())
        ]
    }


@app.post("/api/calculate", summary="Recommend instances for a workload", tags=["calculator"])
def calculate(workload: WorkloadRequest):
    return calc.recommend_for_workload(workload)


@app.post("/api/compare", summary="Compare instances side by side", tags=["comparison"])
def compare(req: CompareRequest):
    try:
        return comparator.compare_instances(req.instance_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/compare/providers", summary="Compare equivalent instances across providers", tags=["comparison"])
def compare_providers(vcpu: int = Query(..., ge=1, le=512), memory_gb: float = Query(..., ge=0, le=4096)):
    return comparator.compare_across_providers(vcpu, memory_gb)


@app.post("/api/compare/bulk", summary="Bulk cost comparison across all providers", tags=["comparison"])
def compare_bulk(req: BulkCompareRequest):
    vcpu_min, vcpu_max = req.vcpu_range
    mem_min, mem_max = req.memory_range
    matches = [
        i for i in calc.load_instances()
        if vcpu_min <= i["vcpu"] <= vcpu_max and mem_min <= i["memory_gb"] <= mem_max
    ]
    matches.sort(key=lambda i: i["hourly_cost_usd"])
    return {
        "count": len(matches),
        "vcpu_range": req.vcpu_range,
        "memory_range": req.memory_range,
        "instances": matches,
    }


@app.post("/api/cost", summary="Estimate cost of instances", tags=["cost"])
def cost(req: CostRequest):
    try:
        return cost_engine.estimate_cost(
            req.instance_ids, req.pricing_model, req.hours_per_month, req.storage_gb, req.data_egress_gb
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/fleet", summary="Fleet planning and cost", tags=["fleet"])
def fleet(req: FleetRequest):
    instances = {i["id"]: i for i in calc.load_instances()}
    hours = req.hours_per_month
    missing = [v.instance_id for v in req.vms if v.instance_id not in instances]
    if missing:
        raise HTTPException(status_code=400, detail="unknown instance ids: {}".format(", ".join(missing)))
    total_vcpu = sum(instances[v.instance_id]["vcpu"] * v.count for v in req.vms)
    total_ram = sum(instances[v.instance_id]["memory_gb"] * v.count for v in req.vms)
    total_cost = sum(instances[v.instance_id]["hourly_cost_usd"] * v.count for v in req.vms)
    breakdown = [
        {
            "instance_id": v.instance_id,
            "instance_type": instances[v.instance_id]["instance_type"],
            "provider": instances[v.instance_id]["provider"],
            "count": v.count,
            "vcpu": instances[v.instance_id]["vcpu"] * v.count,
            "memory_gb": instances[v.instance_id]["memory_gb"] * v.count,
            "hourly_cost_usd": round(instances[v.instance_id]["hourly_cost_usd"] * v.count, 4),
            "monthly_cost_usd": round(instances[v.instance_id]["hourly_cost_usd"] * v.count * hours, 2),
        }
        for v in req.vms
    ]
    return {
        "total_vms": sum(v.count for v in req.vms),
        "total_vcpu": total_vcpu,
        "total_memory_gb": total_ram,
        "total_hourly_cost_usd": round(total_cost, 4),
        "total_monthly_cost_usd": round(total_cost * hours, 2),
        "breakdown": breakdown,
    }


@app.post("/api/rightsize", summary="Right-size from utilization data", tags=["rightsize"])
def rightsize(req: RightSizeRequest):
    try:
        result = rightsizer.analyze_utilization(
            req.current_instance_id, req.cpu_utilization, req.memory_utilization, req.network_utilization
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


class TelemetryFetchRequest(BaseModel):
    provider: str
    project: str = ""
    instance: str = ""
    zone: str = ""
    days: int = 14


@app.post("/api/telemetry/fetch", summary="Fetch live utilization from the provider", tags=["telemetry"])
def telemetry_fetch(req: TelemetryFetchRequest):
    try:
        if req.provider == "gcp":
            return telemetry_svc.fetch_gcp(req.project, req.instance, req.zone, req.days)
        return telemetry_svc.fetch(req.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="telemetry upstream failed: {}".format(exc))


class TelemetryParseRequest(BaseModel):
    raw_data: str


@app.post("/api/telemetry/parse", summary="Parse telemetry metrics from CSV, JSON, or text", tags=["telemetry"])
def telemetry_parse(req: TelemetryParseRequest):
    try:
        return telemetry_svc.parse_telemetry(req.raw_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/optimize", summary="Cheapest instance meeting p95 utilization with 20% headroom", tags=["optimize"])
def optimize(req: OptimizeRequest):
    try:
        return rightsizer.recommend_optimization(
            req.current_instance_id, req.cpu_utilization, req.memory_utilization
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/export/csv", summary="Export data as CSV", tags=["export"])
def export(req: ExportRequest):
    try:
        csv_text = export_csv(req.data, req.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    safe_name = os.path.basename(req.filename)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename={}".format(safe_name)},
    )


@app.post("/api/calculations", summary="Save a calculation (deprecated, delegates to scenarios)", tags=["saved"])
def save_calculation_endpoint(req: SaveCalculationRequest):
    return save_scenario_endpoint(SaveScenarioRequest(name=req.name, request_type="calculation", request=req.request, result=req.result))


@app.get("/api/calculations", summary="List saved calculations (deprecated, delegates to scenarios)", tags=["saved"])
def list_saved_calculations():
    return list_scenarios()


@app.get("/api/calculations/{calc_id}", summary="Get a saved calculation (deprecated, delegates to scenarios)", tags=["saved"])
def get_saved_calculation(calc_id: int):
    return get_scenario(calc_id)


@app.delete("/api/calculations/{calc_id}", summary="Delete a saved calculation (deprecated, delegates to scenarios)", tags=["saved"])
def delete_saved_calculation(calc_id: int):
    return delete_scenario(calc_id)


@app.post("/api/scenarios", summary="Save a scenario", tags=["scenarios"])
def save_scenario_endpoint(req: SaveScenarioRequest):
    scenario_id = db.save_scenario(req.name, req.request_type, req.request, req.result)
    return {"id": scenario_id, "status": "saved"}


@app.get("/api/scenarios", summary="List saved scenarios", tags=["scenarios"])
def list_scenarios():
    return db.get_scenarios()


@app.get("/api/scenarios/{scenario_id}", summary="Get a saved scenario", tags=["scenarios"])
def get_scenario(scenario_id: int):
    row = db.get_scenario(scenario_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return row


@app.delete("/api/scenarios/{scenario_id}", summary="Delete a saved scenario", tags=["scenarios"])
def delete_scenario(scenario_id: int):
    if not db.delete_scenario(scenario_id):
        raise HTTPException(status_code=404, detail="scenario not found")
    return {"id": scenario_id, "status": "deleted"}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
