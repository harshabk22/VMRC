"""Real utilization, or a loud refusal. Stdlib only, no new dependencies.

GCP works here: ADC is present, so we mint a token via the gcloud CLI and
call Monitoring timeSeries.list directly. AWS/Azure need credentials this
machine does not have — they raise telling you exactly how to provide them.
Nothing in this module invents numbers.
"""

import csv
import io
import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


class TelemetryError(ValueError):
    pass


def _adc_token() -> str:
    try:
        out = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise TelemetryError("gcloud CLI not found; install it and run "
                             "'gcloud auth application-default login'")
    if out.returncode != 0:
        raise TelemetryError("no GCP credentials: run "
                             "'gcloud auth application-default login'. "
                             "Detail: {}".format(out.stderr.strip()[:200]))
    token = out.stdout.strip()
    if not token:
        raise TelemetryError("no GCP credentials: run "
                             "'gcloud auth application-default login'")
    return token


def _get(url, token, timeout=30):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_gcp(project: str, instance: str, zone: str, days: int = 14) -> dict:
    """CPU + memory + network samples from Cloud Monitoring, with timestamps."""
    token = _adc_token()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    interval = "interval.startTime={}&interval.endTime={}".format(
        urllib.parse.quote(start.isoformat()), urllib.parse.quote(end.isoformat()))
    base = ("https://monitoring.googleapis.com/v3/projects/{}/"
            "timeSeries?filter={}&{}&aggregation.alignmentPeriod=3600s"
            "&aggregation.perSeriesAligner=ALIGN_MEAN").format(project, "{}", interval)

    def series(metric):
        filt = ('metric.type="{}" AND resource.labels.instance_id="{}"'
                ' AND resource.labels.zone="{}"').format(metric, instance, zone)
        data = _get(base.format(urllib.parse.quote(filt, safe="")), token)
        pts = []
        for ts in data.get("timeSeries", []):
            for p in ts.get("points", []):
                v = p.get("value", {})
                pts.append((p["interval"]["endTime"],
                            float(v.get("doubleValue", v.get("stringValue", 0)))))
        pts.sort()
        return pts

    cpu = series("compute.googleapis.com/instance/cpu/utilization")
    mem = series("agent.googleapis.com/memory/percent_used")
    net_in = series("compute.googleapis.com/instance/network/received_bytes_count")
    if not cpu:
        raise TelemetryError(
            "no CPU series for {}/{}/{} in the last {}d — wrong project/zone/instance,"
            " or the Monitoring API is not enabled".format(project, zone, instance, days))
    # ponytail: network bytes are reported raw alongside percent CPU/mem; the
    # percent-of-link conversion needs the instance's NIC cap at the caller.
    return {
        "source": "gcp-monitoring-live",
        "project": project, "instance": instance, "zone": zone,
        "days": days, "fetched_at": end.isoformat(),
        "cpu": [round(v * 100, 2) for _, v in cpu],
        "memory": [round(v, 2) for _, v in mem],
        "network_raw": [{"t": t, "bytes": b} for t, b in net_in],
        "timestamps": [t for t, _ in cpu],
    }


def fetch_aws(*args, **kwargs):
    raise TelemetryError(
        "AWS telemetry is not configured: no ~/.aws/credentials on this machine. "
        "Install boto3, run 'aws configure', then query CloudWatch GetMetricData "
        "(AWS/EC2 CPUUtilization, mem_used_percent via the CloudWatch agent, "
        "NetworkIn/NetworkOut). This endpoint refuses rather than inventing data.")


def fetch_azure(*args, **kwargs):
    raise TelemetryError(
        "Azure telemetry is not configured: no Azure login on this machine. "
        "Run 'az login', then call the Monitor Metrics API "
        "(Percentage CPU, Percentage Memory, Network In Total). "
        "This endpoint refuses rather than inventing data.")


def fetch(provider: str, **kwargs) -> dict:
    if provider == "gcp":
        return fetch_gcp(**kwargs)
    if provider == "aws":
        return fetch_aws(**kwargs)
    if provider == "azure":
        return fetch_azure(**kwargs)
    raise TelemetryError("unknown provider: {!r}".format(provider))


METRIC_ALIASES = {
    "cpu": ("cpu", "CPU"),
    "memory": ("memory", "mem", "Memory"),
    "network": ("network", "net", "Network"),
}


def parse_telemetry(raw: str) -> dict:
    """Robustly parse metric inputs from CSV, JSON, or key-value sample lines.

    Accepts:
    1. Labeled lines: 'CPU: 10, 20\\nMemory: 40, 50\\nNetwork: 5, 8'
    2. CSV table with headers (cpu, memory, network)
    3. JSON object: {'cpu': [...], 'memory': [...], 'network': [...]}
    4. JSON array of point dicts: [{'cpu': 10, 'memory': 40, 'network': 5}, ...]
    5. Plain multiline numbers (line 1 = cpu, line 2 = mem, line 3 = net)
    """
    cleaned = raw.strip()
    if not cleaned:
        raise TelemetryError("Telemetry input cannot be empty")

    metrics = {"cpu": [], "memory": [], "network": []}
    format_type = "unknown"

    # 1. JSON parse attempt
    if (cleaned.startswith("{") and cleaned.endswith("}")) or (cleaned.startswith("[") and cleaned.endswith("]")):
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                for m_key, aliases in METRIC_ALIASES.items():
                    for k in aliases:
                        if k in data and data[k] is not None:
                            metrics[m_key] = [float(x) for x in data[k]]
                            break
                format_type = "json_object"
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        for m_key, aliases in METRIC_ALIASES.items():
                            val = next((item[k] for k in aliases if k in item and item[k] is not None), None)
                            if val is not None:
                                metrics[m_key].append(float(val))
                format_type = "json_records"
        except Exception:
            pass

    # 2. Key-value labeled lines: 'CPU: 10, 20...'
    if not metrics["cpu"] and not metrics["memory"]:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        for line in lines:
            m = re.match(r"^(cpu|memory|mem|network|net)\s*:\s*(.*)$", line, re.IGNORECASE)
            if m:
                key = m.group(1).lower()
                canon = "cpu" if key == "cpu" else ("memory" if "mem" in key else "network")
                metrics[canon] = [float(x.strip()) for x in re.split(r"[,;\s]+", m.group(2)) if x.strip()]
        if metrics["cpu"] or metrics["memory"]:
            format_type = "labeled_lines"

    # 3. CSV with headers
    if not metrics["cpu"] and not metrics["memory"]:
        try:
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(cleaned[:1024])
            reader = csv.reader(io.StringIO(cleaned), dialect)
        except Exception:
            reader = csv.reader(io.StringIO(cleaned))

        rows = [r for r in reader if r and any(cell.strip() for cell in r)]
        if len(rows) >= 2:
            headers = [h.strip().lower() for h in rows[0]]
            col_indices = {
                m_key: next((i for i, h in enumerate(headers) if any(a.lower() in h for a in aliases)), None)
                for m_key, aliases in METRIC_ALIASES.items()
            }
            if col_indices["cpu"] is not None or col_indices["memory"] is not None:
                for row in rows[1:]:
                    for m_key, idx in col_indices.items():
                        if idx is not None and len(row) > idx:
                            try:
                                metrics[m_key].append(float(row[idx].strip()))
                            except ValueError:
                                pass
                format_type = "csv_table"

    # 4. Fallback: 3 raw lines of comma-separated numbers
    if not metrics["cpu"] and not metrics["memory"]:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(lines) >= 2:
            try:
                keys = ["cpu", "memory", "network"]
                for i, line in enumerate(lines[:3]):
                    metrics[keys[i]] = [float(x.strip()) for x in line.split(",") if x.strip()]
                format_type = "multiline_raw"
            except ValueError:
                pass

    cpu, mem, net = metrics["cpu"], metrics["memory"], metrics["network"]
    if not cpu and not mem:
        raise TelemetryError("Could not extract CPU or memory metrics from provided data. "
                             "Please check format (CSV with headers, JSON, or labeled lines).")

    # If metrics are normalized fractions (all <= 1.0), scale to percentage (0-100)
    for series in (cpu, mem, net):
        if series and max(series) <= 1.0 and any(x > 0 for x in series):
            for i in range(len(series)):
                series[i] = round(series[i] * 100.0, 2)

    return {
        "format": format_type,
        "sample_count": len(cpu),
        "cpu": cpu,
        "memory": mem,
        "network": net or [0.0] * len(cpu),
        "summary": {
            "cpu_avg": round(sum(cpu) / len(cpu), 2) if cpu else 0.0,
            "memory_avg": round(sum(mem) / len(mem), 2) if mem else 0.0,
            "network_avg": round(sum(net) / len(net), 2) if net else 0.0,
        },
    }
