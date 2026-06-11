"""
auth_guard.py - Authentication Guard for Azure Terraform Module Builder
======================================================================
Pre-flight authentication validation before any file generation or write
operations. Integrates Azure CLI auth and Terraform ARM env var checks.

SAFETY:
- NEVER logs, stores, or transmits credentials or secret values.
- Only checks PRESENCE of env vars, never reads actual values.
- Read-only environment inspection only.
- All writes are blocked until auth guard passes (in --require-auth mode).
""" 

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass
class AuthGuardResult:
    """Combined result of Azure CLI + Terraform auth checks."""
    # Azure CLI
    az_available: bool = False
    az_logged_in: bool = False
    az_account_name: str = ""
    az_account_type: str = ""
    az_tenant_id: str = ""
    az_subscription_id: str = ""
    az_subscription_name: str = ""

    # Terraform ARM env vars (presence only)
    tf_auth_method: str = "none"  # sp / msi / cli / oidc / none
    tf_arm_client_id_set: bool = False
    tf_arm_client_secret_set: bool = False
    tf_arm_tenant_id_set: bool = False
    tf_arm_subscription_id_set: bool = False
    tf_arm_use_msi: bool = False
    tf_arm_use_cli: bool = False
    tf_arm_use_oidc: bool = False
    tf_is_ready: bool = False

    # Terraform binary
    tf_binary_available: bool = False
    tf_binary_version: str = ""

    # Overall
    passed: bool = False
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_az_silent(args: List[str]) -> Optional[Dict]:
    """Run az CLI command silently, return parsed JSON or None."""
    import json
    cmd = ["az"] + args + ["--output", "json"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return None
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _az_cli_available() -> bool:
    """Check if az CLI is on the PATH."""
    try:
        r = subprocess.run(["az", "--version"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _terraform_available() -> tuple:
    """Check if terraform is available. Returns (available, version)."""
    try:
        import json
        r = subprocess.run(
            ["terraform", "version", "-json"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            return True, data.get("terraform_version", "")
        r2 = subprocess.run(
            ["terraform", "version"],
            capture_output=True, text=True, timeout=10
        )
        if r2.returncode == 0:
            lines = r2.stdout.strip().splitlines()
            return True, lines[0] if lines else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return False, "not installed"


def _mask(value: str) -> str:
    """Mask sensitive value - first/last 4 chars only."""
    if not value or len(value) < 8:
        return "***"
    return value[:4] + "****" + value[-4:]


# ---------------------------------------------------------------------------
# Main guard check
# ---------------------------------------------------------------------------

def run_auth_guard(
    check_azure: bool = True,
    check_terraform: bool = True,
    require_azure_login: bool = True,
    require_tf_auth: bool = False,
) -> AuthGuardResult:
    """
    Run pre-flight authentication checks.

    Args:
        check_azure: Whether to check Azure CLI auth.
        check_terraform: Whether to check Terraform ARM env vars.
        require_azure_login: If True, fail when not logged in to Azure CLI.
        require_tf_auth: If True, fail when no TF auth method is configured.

    Returns:
        AuthGuardResult with all check results.
    """
    result = AuthGuardResult()

    # --- Azure CLI checks ---
    if check_azure:
        result.az_available = _az_cli_available()
        if not result.az_available:
            result.errors.append(
                "Azure CLI (az) is not installed or not on PATH. "
                "Install from: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
            )
        else:
            account = _run_az_silent(["account", "show"])
            if account:
                result.az_logged_in = True
                result.az_account_name = account.get("user", {}).get("name", "")
                result.az_account_type = account.get("user", {}).get("type", "user")
                result.az_tenant_id = account.get("tenantId", "")
                result.az_subscription_id = account.get("id", "")
                result.az_subscription_name = account.get("name", "")
            else:
                result.az_logged_in = False
                result.errors.append("Not logged in to Azure CLI. Run: az login")

    # --- Terraform ARM env var checks ---
    if check_terraform:
        result.tf_arm_client_id_set = bool(os.environ.get("ARM_CLIENT_ID"))
        result.tf_arm_client_secret_set = bool(os.environ.get("ARM_CLIENT_SECRET"))
        result.tf_arm_tenant_id_set = bool(os.environ.get("ARM_TENANT_ID"))
        result.tf_arm_subscription_id_set = bool(os.environ.get("ARM_SUBSCRIPTION_ID"))
        result.tf_arm_use_msi = os.environ.get("ARM_USE_MSI", "").lower() in ("1", "true", "yes")
        result.tf_arm_use_cli = os.environ.get("ARM_USE_CLI", "").lower() in ("1", "true", "yes")
        result.tf_arm_use_oidc = os.environ.get("ARM_USE_OIDC", "").lower() in ("1", "true", "yes")

        sp_complete = (
            result.tf_arm_client_id_set
            and result.tf_arm_client_secret_set
            and result.tf_arm_tenant_id_set
            and result.tf_arm_subscription_id_set
        )

        if sp_complete:
            result.tf_auth_method = "sp"
            result.tf_is_ready = True
        elif result.tf_arm_use_msi:
            result.tf_auth_method = "msi"
            result.tf_is_ready = True
        elif result.tf_arm_use_oidc:
            result.tf_auth_method = "oidc"
            result.tf_is_ready = True
        elif result.tf_arm_use_cli or result.az_available:
            result.tf_auth_method = "cli"
            result.tf_is_ready = True
        else:
            result.tf_auth_method = "none"
            result.tf_is_ready = False

        if result.tf_arm_client_id_set and not result.tf_arm_client_secret_set:
            result.warnings.append("ARM_CLIENT_ID is set but ARM_CLIENT_SECRET is missing.")
        if not result.tf_arm_subscription_id_set:
            result.warnings.append("ARM_SUBSCRIPTION_ID not set - required for TF provider.")
        if not result.tf_is_ready and require_tf_auth:
            result.errors.append(
                "No Terraform auth method configured. "
                "Set ARM_CLIENT_ID/SECRET/TENANT_ID/SUBSCRIPTION_ID or ARM_USE_CLI=true."
            )

    # --- Terraform binary check ---
    if check_terraform:
        result.tf_binary_available, result.tf_binary_version = _terraform_available()
        if not result.tf_binary_available:
            result.warnings.append(
                "Terraform binary not found. Install from https://developer.hashicorp.com/terraform/install"
            )

    # --- Overall pass/fail ---
    critical_errors = []
    if require_azure_login and check_azure and not result.az_logged_in:
        critical_errors.append("Azure CLI login required")
    if require_tf_auth and check_terraform and not result.tf_is_ready:
        critical_errors.append("Terraform auth required")

    result.passed = len(critical_errors) == 0
    return result


# ---------------------------------------------------------------------------
# Rich output
# ---------------------------------------------------------------------------

def print_auth_guard_result(result: AuthGuardResult):
    """Print authentication guard results as a rich table."""
    table = Table(
        title="[bold]Module Builder - Auth Pre-flight[/bold]",
        box=box.ROUNDED,
        header_style="bold white on dark_blue",
        show_header=True,
    )
    table.add_column("Check", style="bold", width=35)
    table.add_column("Status", width=55)

    def ok(msg): return f"[green]PASS[/green]  {msg}"
    def warn(msg): return f"[yellow]WARN[/yellow]  {msg}"
    def fail(msg): return f"[red]FAIL[/red]  {msg}"

    # Azure CLI
    table.add_row("Azure CLI installed",
        ok("az found") if result.az_available else fail("az not installed")
    )
    table.add_row("Azure CLI login",
        ok(f"Signed in as {result.az_account_name} ({result.az_account_type})")
        if result.az_logged_in else fail("Not logged in - run: az login")
    )
    if result.az_logged_in:
        sub_display = _mask(result.az_subscription_id) if result.az_subscription_id else "unknown"
        table.add_row("Active subscription",
            ok(f"{result.az_subscription_name} ({sub_display})")
        )

    # Terraform
    method_labels = {
        "sp": "Service Principal (ARM_CLIENT_ID/SECRET/TENANT/SUBSCRIPTION)",
        "msi": "Managed Identity (ARM_USE_MSI=true)",
        "cli": "Azure CLI (ARM_USE_CLI or az available)",
        "oidc": "OIDC / Federated Identity",
        "none": "NONE - not configured",
    }
    table.add_row("Terraform auth method",
        ok(method_labels.get(result.tf_auth_method, result.tf_auth_method))
        if result.tf_is_ready else warn(method_labels.get(result.tf_auth_method, "none"))
    )
    table.add_row("Terraform binary",
        ok(f"terraform {result.tf_binary_version}")
        if result.tf_binary_available else warn("not found")
    )
    table.add_row("ARM_SUBSCRIPTION_ID",
        ok("Set") if result.tf_arm_subscription_id_set else warn("Not set - needed for TF provider")
    )

    console.print(table)

    if result.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]~[/yellow] {w}")
    if result.errors:
        console.print("[red]Errors:[/red]")
        for e in result.errors:
            console.print(f"  [red]![/red] {e}")


# ---------------------------------------------------------------------------
# Guard enforcer
# ---------------------------------------------------------------------------

def enforce_auth_guard(
    require_azure_login: bool = True,
    require_tf_auth: bool = False,
    check_azure: bool = True,
    check_terraform: bool = True,
) -> AuthGuardResult:
    """
    Run auth guard and print results. Exit with error if critical checks fail.

    Args:
        require_azure_login: Abort if not logged in to Azure CLI.
        require_tf_auth: Abort if no Terraform auth method configured.
        check_azure: Run Azure CLI checks.
        check_terraform: Run Terraform ARM env checks.

    Returns:
        AuthGuardResult (only returns if guard passed).
    """
    console.print("[bold]Running authentication pre-flight check...[/bold]")
    console.print()

    result = run_auth_guard(
        check_azure=check_azure,
        check_terraform=check_terraform,
        require_azure_login=require_azure_login,
        require_tf_auth=require_tf_auth,
    )

    print_auth_guard_result(result)
    console.print()

    if not result.passed:
        console.print(Panel(
            "[bold red]Authentication pre-flight FAILED.[/bold red]\n"
            "Fix the errors above before proceeding.\n"
            "Run: bash scripts/setup_auth.sh --check  for guidance.",
            title="Auth Guard: BLOCKED",
            border_style="red",
        ))
        sys.exit(1)

    console.print("[bold green]Auth pre-flight: PASSED[/bold green]")
    return result


if __name__ == "__main__":
    enforce_auth_guard(require_azure_login=True, require_tf_auth=False)
