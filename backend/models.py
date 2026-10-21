from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class InstanceType(BaseModel):
    id: str = Field(..., description="Unique instance identifier, e.g. aws-m5-xlarge")
    provider: str = Field(..., description="Cloud provider: aws, azure, or gcp")
    family: str = Field(..., description="Instance family: general, compute, memory, storage, or burstable")
    instance_type: str = Field(..., description="Provider-native instance type name")
    vcpu: int = Field(..., description="Number of vCPUs")
    memory_gb: float = Field(..., description="Memory in GB")
    storage_gb: Optional[float] = Field(None, description="Local storage in GB, null if none")
    network_gbps: float = Field(..., description="Network bandwidth in Gbps")
    hourly_cost_usd: float = Field(..., description="On-demand hourly cost in USD")
    region: str = Field(..., description="Region the price applies to")
    description: str = Field("", description="Short description of the instance class")
    price_source: Optional[str] = Field(None, description="Provenance: live:azure-retail or snapshot:instances.json")
    price_fetched_at: Optional[str] = Field(None, description="UTC timestamp of the live fetch, null for snapshot")


class InstanceList(BaseModel):
    count: int = Field(..., description="Number of instances returned")
    instances: List[InstanceType] = Field(..., description="Matching instances")


class WorkloadRequest(BaseModel):
    workload_type: str = Field("web", description="Workload type label, e.g. web, database, batch")
    concurrent_users: int = Field(..., ge=1, le=1000000, description="Number of concurrent users")
    cpu_per_user: float = Field(..., ge=0, le=100, description="vCPU required per user")
    memory_per_user_mb: float = Field(..., ge=0, le=1048576, description="Memory required per user in MB")
    storage_gb: float = Field(0, ge=0, le=1048576, description="Required storage in GB")
    bandwidth_mbps: float = Field(0, ge=0, le=100000, description="Required bandwidth in Mbps")
    growth_factor: float = Field(1.0, ge=1.0, le=100, description="Expected growth multiplier, e.g. 1.5 for 50% growth")
    headroom_percent: float = Field(20.0, ge=0, le=100, description="Headroom buffer percent on top of requirements")


class CostRequest(BaseModel):
    instance_ids: List[str] = Field(..., min_length=1, max_length=50, description="Instance IDs to estimate cost for")
    pricing_model: str = Field("ondemand", description="Pricing model: ondemand, reserved_1yr, reserved_3yr, or spot")
    hours_per_month: float = Field(730, gt=0, le=744, description="Running hours per month")
    storage_gb: float = Field(0, ge=0, le=16384, description="Additional SSD storage in GB per instance")
    data_egress_gb: float = Field(0, ge=0, le=1048576, description="Data egress in GB per instance per month")


class RightSizeRequest(BaseModel):
    current_instance_id: str = Field(..., description="Instance ID currently in use")
    cpu_utilization: List[float] = Field(..., min_length=1, max_length=10000, description="CPU utilization percent samples")
    memory_utilization: List[float] = Field(..., min_length=1, max_length=10000, description="Memory utilization percent samples")
    network_utilization: List[float] = Field(..., min_length=1, max_length=10000, description="Network utilization percent samples")

    @field_validator("cpu_utilization", "memory_utilization", "network_utilization")
    @classmethod
    def _check_utilization_range(cls, v):
        if any(x < 0 or x > 100 for x in v):
            raise ValueError("utilization samples must be between 0 and 100")
        return v


class FleetVM(BaseModel):
    instance_id: str = Field(..., description="Instance ID")
    count: int = Field(..., ge=1, le=10000, description="Number of VMs of this type")


class FleetRequest(BaseModel):
    vms: List[FleetVM] = Field(..., min_length=1, max_length=100, description="VMs in the fleet")
    hours_per_month: float = Field(730, gt=0, le=744, description="Running hours per month")


class ExportRequest(BaseModel):
    data: List[dict] = Field(..., min_length=1, description="Rows to export as CSV")
    filename: str = Field("export.csv", description="Suggested filename for the CSV")


class CompareRequest(BaseModel):
    instance_ids: List[str] = Field(..., min_length=1, max_length=50, description="Instance IDs to compare")


class SaveScenarioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Scenario name")
    request_type: str = Field(..., min_length=1, max_length=100, description="Type of request, e.g. calculate, compare, cost")
    request: dict = Field(..., description="The request payload that produced the result")
    result: dict = Field(..., description="The result payload to save")


class BulkCompareRequest(BaseModel):
    vcpu_range: List[float] = Field(..., min_length=2, max_length=2, description="[min, max] vCPU range")
    memory_range: List[float] = Field(..., min_length=2, max_length=2, description="[min, max] memory range in GB")

    @field_validator("vcpu_range")
    @classmethod
    def _check_vcpu_range(cls, v):
        if v[0] < 1 or v[1] > 512 or v[0] > v[1]:
            raise ValueError("vcpu_range must be [min >= 1, max <= 512] with min <= max")
        return v

    @field_validator("memory_range")
    @classmethod
    def _check_memory_range(cls, v):
        if v[0] < 0.5 or v[1] > 4096 or v[0] > v[1]:
            raise ValueError("memory_range must be [min >= 0.5, max <= 4096] with min <= max")
        return v


class OptimizeRequest(BaseModel):
    current_instance_id: str = Field(..., min_length=1, description="Instance ID currently in use")
    cpu_utilization: List[float] = Field(..., min_length=1, max_length=10000, description="CPU utilization percent samples")
    memory_utilization: List[float] = Field(..., min_length=1, max_length=10000, description="Memory utilization percent samples")

    @field_validator("cpu_utilization", "memory_utilization")
    @classmethod
    def _check_utilization_range(cls, v):
        if any(x < 0 or x > 100 for x in v):
            raise ValueError("utilization samples must be between 0 and 100")
        return v
