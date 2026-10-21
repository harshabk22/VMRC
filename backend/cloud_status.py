"""Local Cloud CLI session and configuration detector. Stdlib only.

Detects local installation and authentication status for AWS, Azure, and GCP CLIs
with tight timeouts so the API never hangs.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone


def _run_cli(bin_name: str, args: list, timeout: float = 2.5):
    """Run a CLI binary if installed. Returns (binary_path, stdout_or_None)."""
    bin_path = shutil.which(bin_name)
    if not bin_path:
        return None, None
    try:
        proc = subprocess.run(
            [bin_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            return bin_path, proc.stdout
    except Exception:
        pass
    return bin_path, None


def _detect_aws() -> dict:
    bin_path, stdout = _run_cli("aws", ["sts", "get-caller-identity", "--output", "json"])
    if not bin_path:
        return {
            "installed": False,
            "authenticated": False,
            "binary_path": None,
            "identity": None,
            "account": None,
            "region": None,
            "message": "AWS CLI not found. Install via 'brew install awscli' and run 'aws configure'.",
        }

    if stdout:
        try:
            data = json.loads(stdout)
            return {
                "installed": True,
                "authenticated": True,
                "binary_path": bin_path,
                "identity": data.get("Arn"),
                "account": data.get("Account"),
                "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                "message": f"Connected to AWS (Account: {data.get('Account')})",
            }
        except Exception:
            pass

    creds_exist = os.path.exists(os.path.expanduser("~/.aws/credentials"))
    return {
        "installed": True,
        "authenticated": False,
        "binary_path": bin_path,
        "identity": None,
        "account": None,
        "region": None,
        "message": "AWS CLI installed, but session not authenticated. Run 'aws configure' or 'aws sso login'."
        if creds_exist
        else "AWS CLI installed with no credentials. Run 'aws configure' to connect.",
    }


def _detect_azure() -> dict:
    bin_path, stdout = _run_cli("az", ["account", "show", "--output", "json"])
    if not bin_path:
        return {
            "installed": False,
            "authenticated": False,
            "binary_path": None,
            "identity": None,
            "subscription": None,
            "message": "Azure CLI not found. Install via 'brew install azure-cli' and run 'az login'.",
        }

    if stdout:
        try:
            data = json.loads(stdout)
            user = data.get("user", {}).get("name") if isinstance(data.get("user"), dict) else data.get("user")
            return {
                "installed": True,
                "authenticated": True,
                "binary_path": bin_path,
                "identity": user or data.get("name"),
                "subscription": data.get("name") or data.get("id"),
                "message": f"Connected to Azure ({data.get('name') or user})",
            }
        except Exception:
            pass

    return {
        "installed": True,
        "authenticated": False,
        "binary_path": bin_path,
        "identity": None,
        "subscription": None,
        "message": "Azure CLI installed, but no active login. Run 'az login'.",
    }


def _detect_gcp() -> dict:
    bin_path, stdout_acc = _run_cli("gcloud", ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"])
    if not bin_path:
        return {
            "installed": False,
            "authenticated": False,
            "binary_path": None,
            "identity": None,
            "project": None,
            "message": "gcloud CLI not found. Install Google Cloud SDK and run 'gcloud auth login'.",
        }

    account = None
    if stdout_acc:
        val = stdout_acc.strip()
        if val and val.lower() != "account":
            account = val

    _, stdout_proj = _run_cli("gcloud", ["config", "get-value", "project"])
    project = None
    if stdout_proj:
        val = stdout_proj.strip()
        if val and not val.startswith("(") and val.lower() != "project":
            project = val

    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    has_adc = os.path.exists(adc_path)
    authenticated = bool(account or has_adc)
    identity = account or ("ADC credentials file present" if has_adc else None)

    return {
        "installed": True,
        "authenticated": authenticated,
        "binary_path": bin_path,
        "identity": identity,
        "project": project,
        "has_adc": has_adc,
        "message": f"Connected to GCP (Account: {identity}, Project: {project or 'unset'})"
        if authenticated
        else "gcloud installed, but no active login. Run 'gcloud auth application-default login'.",
    }


DETECTORS = {
    "aws": _detect_aws,
    "azure": _detect_azure,
    "gcp": _detect_gcp,
}


def get_cloud_status() -> dict:
    providers = {p: fn() for p, fn in DETECTORS.items()}
    connected = [p for p, s in providers.items() if s.get("authenticated")]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_providers": len(providers),
            "connected_count": len(connected),
            "any_connected": len(connected) > 0,
            "connected_providers": connected,
        },
        "providers": providers,
    }
