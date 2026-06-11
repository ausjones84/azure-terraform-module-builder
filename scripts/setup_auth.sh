#!/usr/bin/env bash
# =============================================================================
# setup_auth.sh - Authentication Setup Helper for azure-terraform-module-builder
# =============================================================================
# PURPOSE:
#   Templates for configuring Azure CLI and Terraform ARM environment
#   variables required by the Module Builder tool.
#
# SAFETY:
#   - NEVER hardcode secrets, passwords, or keys in this file.
#   - NEVER commit this file with real values filled in.
#   - Values must be loaded from a secure vault at runtime.
#   - This is a TEMPLATE only.
#
# USAGE:
#   source scripts/setup_auth.sh           # Load functions into shell
#   bash scripts/setup_auth.sh --check     # Run auth check only
#   bash scripts/setup_auth.sh --auth-only # Run Python auth guard check
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Azure CLI Login
# ---------------------------------------------------------------------------
# Interactive login (dev/local):
# az login

# Service principal login (scripts/pipelines):
# az login --service-principal \
#   --username "${ARM_CLIENT_ID}" \
#   --password "${ARM_CLIENT_SECRET}" \
#   --tenant "${ARM_TENANT_ID}"

# Managed Identity (Azure VMs/AKS/DevOps agents):
# az login --identity

# Set active subscription:
# az account set --subscription "${ARM_SUBSCRIPTION_ID}"

# ---------------------------------------------------------------------------
# Terraform ARM Environment Variables
# ---------------------------------------------------------------------------
# IMPORTANT: Load from secure vault - NEVER hardcode here.
#
# --- Service Principal ---
# export ARM_CLIENT_ID=""
# export ARM_CLIENT_SECRET=""        # Load from vault ONLY
# export ARM_TENANT_ID=""
# export ARM_SUBSCRIPTION_ID=""

# --- Azure CLI Auth (dev only) ---
# export ARM_USE_CLI=true
# export ARM_SUBSCRIPTION_ID=""

# --- Managed Identity (Azure-hosted) ---
# export ARM_USE_MSI=true
# export ARM_SUBSCRIPTION_ID=""

# --- OIDC (GitHub Actions / Azure DevOps pipelines) ---
# export ARM_USE_OIDC=true
# export ARM_CLIENT_ID=""
# export ARM_TENANT_ID=""
# export ARM_SUBSCRIPTION_ID=""

# ---------------------------------------------------------------------------
# Load from Azure Key Vault (recommended)
# ---------------------------------------------------------------------------
# export ARM_CLIENT_SECRET=$(az keyvault secret show \
#   --vault-name "your-vault" \
#   --name "tf-sp-secret" \
#   --query "value" --output tsv)

# ---------------------------------------------------------------------------
# Auth check function
# ---------------------------------------------------------------------------
check_auth() {
    echo "=== Azure CLI Auth ==="
    if ! command -v az &>/dev/null; then
        echo "ERROR: Azure CLI not found."
        echo "       Install: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli"
        return 1
    fi
    local account
    if ! account=$(az account show --output json 2>/dev/null); then
        echo "ERROR: Not logged in. Run: az login"
        return 1
    fi
    local name sub_name
    name=$(echo "${account}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['user']['name'])" 2>/dev/null || echo "unknown")
    sub_name=$(echo "${account}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['name'])" 2>/dev/null || echo "unknown")
    echo "  Logged in as: ${name}"
    echo "  Subscription: ${sub_name}"
    echo ""

    echo "=== Terraform ARM Env ==="
    for var in ARM_CLIENT_ID ARM_CLIENT_SECRET ARM_TENANT_ID ARM_SUBSCRIPTION_ID; do
        if [[ -n "${!var:-}" ]]; then
            echo "  [SET]   ${var}"
        else
            echo "  [MISS]  ${var}"
        fi
    done
    for var in ARM_USE_MSI ARM_USE_CLI ARM_USE_OIDC; do
        if [[ -n "${!var:-}" ]]; then
            echo "  [SET]   ${var}=${!var}"
        fi
    done
    echo ""

    echo "=== Terraform Binary ==="
    if command -v terraform &>/dev/null; then
        echo "  Found: $(terraform version -no-color 2>/dev/null | head -1)"
    else
        echo "  WARNING: terraform not found"
        echo "           Install: https://developer.hashicorp.com/terraform/install"
    fi
    echo ""
    echo "Auth check complete."
}

# Run Python auth guard if available
run_python_auth_guard() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local guard="${script_dir}/../src/auth_guard.py"
    if command -v python3 &>/dev/null && [[ -f "${guard}" ]]; then
        echo "Running Python auth guard..."
        python3 "${guard}"
    else
        echo "Python auth guard not found. Run check_auth for shell-based check."
    fi
}

# Main
case "${1:-}" in
    --check)
        check_auth
        ;;
    --auth-only)
        run_python_auth_guard
        ;;
    "")
        if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
            echo "Auth helper functions loaded. Run: check_auth"
        else
            check_auth
        fi
        ;;
    *)
        echo "Usage: $0 [--check|--auth-only]"
        exit 1
        ;;
esac
