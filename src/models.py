"""
models.py - Data models for Azure Terraform Module Builder
==========================================================
Defines enumerations and dataclasses used throughout the tool.
These models represent resources read from DR discovery output
and track the code generation lifecycle.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class GenerationStatus(str, Enum):
    """Lifecycle state of a resource through the generator pipeline."""
    PENDING = "pending"
    DRY_RUN_ONLY = "dry_run_only"
    GENERATED = "generated"
    SKIPPED = "skipped"
    ERROR = "error"


class ResourceNeed(str, Enum):
    """Why a resource needs module builder attention."""
    MODULE_MISSING = "module_missing"
    DEPLOYMENT_MISSING = "deployment_missing"
    MODULE_AVAILABLE = "module_available_but_not_instantiated"
    AZURE_ONLY = "azure_only"
    POSSIBLE_MATCH = "possible_match"
    TERRAFORM_MANAGED = "terraform_managed"
    UNKNOWN = "unknown"


class ServiceType(str, Enum):
    """Supported Azure service types."""
    AI_SEARCH = "ai_search"
    OPENAI = "openai"
    AI_FOUNDRY = "ai_foundry"
    PRIVATE_ENDPOINT = "private_endpoint"
    DIAGNOSTIC_SETTING = "diagnostic_setting"
    RBAC = "rbac"
    GENERIC = "generic"


class OutputType(str, Enum):
    """Types of generated output artifacts."""
    MODULE = "module"
    DEPLOYMENT = "deployment"
    IMPORT_COMMANDS = "import_commands"
    README = "readme"
    VARIABLES = "variables"
    OUTPUTS = "outputs"
    TFVARS = "tfvars"


# ---------------------------------------------------------------------------
# Discovery input models (parsed from DR tool output)
# ---------------------------------------------------------------------------

@dataclass
class PrivateEndpointData:
    """Private endpoint data parsed from discovery output."""
    name: str = ""
    resource_group: str = ""
    vnet_name: str = ""
    subnet_name: str = ""
    subnet_id: str = ""
    private_ip_address: str = ""
    nic_name: str = ""
    connection_state: str = ""
    group_ids: List[str] = field(default_factory=list)
    dns_fqdn: str = ""
    private_link_service_id: str = ""


@dataclass
class DiagnosticData:
    """Diagnostic settings data from discovery output."""
    name: str = ""
    log_analytics_workspace_id: str = ""
    log_analytics_workspace_name: str = ""
    storage_account_id: str = ""
    log_categories: List[str] = field(default_factory=list)
    metric_categories: List[str] = field(default_factory=list)


@dataclass
class IdentityData:
    """Identity data from discovery output."""
    type: str = "None"
    principal_id: str = ""
    user_assigned_identities: List[str] = field(default_factory=list)


@dataclass
class DiscoveredResource:
    """
    A single Azure resource parsed from DR tool discovery output.
    This is the primary input to the module builder.
    """
    name: str = ""
    resource_type: str = ""
    resource_group: str = ""
    subscription_id: str = ""
    location: str = ""
    sku_name: str = ""
    sku_tier: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    identity: Optional[IdentityData] = None
    public_network_access: str = ""
    private_endpoints: List[PrivateEndpointData] = field(default_factory=list)
    diagnostic_settings: List[DiagnosticData] = field(default_factory=list)
    # DR tool comparison fields
    comparison_status: str = ""
    risk_level: str = ""
    tf_matches: int = 0
    recommended_action: str = ""
    confidence: str = ""
    # Derived
    service_type: ServiceType = ServiceType.GENERIC
    need: ResourceNeed = ResourceNeed.UNKNOWN
    extras: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Generation plan and output models
# ---------------------------------------------------------------------------

@dataclass
class GeneratedFile:
    """A single generated file artifact."""
    output_type: OutputType = OutputType.MODULE
    relative_path: str = ""     # relative to the output root
    full_path: str = ""
    content: str = ""
    is_dry_run: bool = True
    written: bool = False
    error: Optional[str] = None


@dataclass
class GenerationPlan:
    """
    Plan for generating module/deployment files for a single resource.
    Created in dry-run; executed in write mode.
    """
    resource: Optional[DiscoveredResource] = None
    service_type: ServiceType = ServiceType.GENERIC
    need: ResourceNeed = ResourceNeed.UNKNOWN
    # Paths
    module_output_path: str = ""
    deployment_output_path: str = ""
    # Files to generate
    planned_files: List[GeneratedFile] = field(default_factory=list)
    # Execution results
    status: GenerationStatus = GenerationStatus.PENDING
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Human-readable plan description
    description: str = ""

    @property
    def file_count(self) -> int:
        return len(self.planned_files)

    @property
    def written_count(self) -> int:
        return sum(1 for f in self.planned_files if f.written)


@dataclass
class BuildReport:
    """
    Top-level report for a full module builder run.
    """
    input_file: str = ""
    output_dir: str = ""
    module_root: str = ""
    env_path: str = ""
    dry_run: bool = True
    run_timestamp: str = ""

    total_resources: int = 0
    resources_needing_action: int = 0
    resources_skipped: int = 0
    plans: List[GenerationPlan] = field(default_factory=list)

    summary_lines: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)

    @property
    def generated_count(self) -> int:
        return sum(
            1 for p in self.plans
            if p.status == GenerationStatus.GENERATED
        )

    @property
    def total_files_written(self) -> int:
        return sum(p.written_count for p in self.plans)
