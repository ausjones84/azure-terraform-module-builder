"""
stub_validator.py - Dry-Run Validator and Plan Printer
======================================================
Validates GenerationPlans in dry-run mode and produces
human-readable summaries of what WOULD be generated.

SAFETY: Never writes files. Never runs Terraform.
"""

import logging
import re
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from models import (
    GenerationPlan, GeneratedFile, GenerationStatus,
    BuildReport, ResourceNeed, ServiceType, OutputType
)

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

TODO_PATTERN = re.compile(r'#\s*TODO', re.IGNORECASE)


class ValidationResult:
    """Result of validating a single GenerationPlan."""

    def __init__(self, plan: GenerationPlan):
        self.plan = plan
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.todo_counts: Dict[str, int] = {}  # filename -> TODO count

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def total_todos(self) -> int:
        return sum(self.todo_counts.values())


def validate_plan(plan: GenerationPlan) -> ValidationResult:
    """
    Validate a GenerationPlan in dry-run mode.

    Checks:
    - All planned files have content
    - No placeholder [REPLACE_ME] markers remain
    - TODO count per file
    - Safety warnings for missing private IP, workspace ID, etc.

    Args:
        plan: A GenerationPlan created by module or deployment generator.

    Returns:
        ValidationResult with warnings and error lists.
    """
    result = ValidationResult(plan)

    if not plan.planned_files:
        result.errors.append("No files planned for generation")
        return result

    resource = plan.resource
    rn = resource.name if resource else "unknown"

    for gf in plan.planned_files:
        filename = gf.relative_path.split("/")[-1]

        # Check content exists
        if not gf.content or not gf.content.strip():
            result.errors.append(f"{filename}: Empty content")
            continue

        # Count TODOs
        todos = len(TODO_PATTERN.findall(gf.content))
        result.todo_counts[filename] = todos
        if todos > 0:
            result.warnings.append(f"{filename}: {todos} TODO item(s) require manual review")

        # Check for unreplaced placeholders
        if "[REPLACE_ME]" in gf.content or "<REPLACE_ME>" in gf.content:
            result.errors.append(f"{filename}: Contains unreplaced placeholder markers")

    # Resource-level checks
    if resource:
        # Public network access check
        if (resource.public_network_access or "").lower() == "enabled":
            result.warnings.append(
                f"[{rn}] Public network access is ENABLED - "
                "confirm this is intentional before deploying"
            )

        # Private endpoint warnings
        for pe in resource.private_endpoints:
            if not pe.private_ip_address:
                result.warnings.append(
                    f"[{rn}] Private endpoint '{pe.name}' has no private IP address - "
                    "TODO value required in tfvars"
                )
            if pe.connection_state and pe.connection_state.lower() != "approved":
                result.warnings.append(
                    f"[{rn}] Private endpoint '{pe.name}' connection state: "
                    f"'{pe.connection_state}' - may need re-approval after apply"
                )

        # Diagnostic settings check
        if resource.diagnostic_settings:
            for ds in resource.diagnostic_settings:
                if not ds.log_analytics_workspace_id:
                    result.warnings.append(
                        f"[{rn}] Diagnostic setting '{ds.name}' "
                        "missing Log Analytics workspace ID - TODO required"
                    )

        # Identity check
        if not resource.identity or resource.identity.type == "None":
            result.warnings.append(
                f"[{rn}] No managed identity configured in Azure - "
                "verify identity_type variable value"
            )

        # Import warning
        if resource.need.value not in ("module_missing", "deployment_missing"):
            result.warnings.append(
                f"[{rn}] Resource EXISTS in Azure - run import_commands.sh "
                "BEFORE terraform apply to prevent resource recreation"
            )

    return result


def validate_all_plans(plans: List[GenerationPlan]) -> List[ValidationResult]:
    """Validate all plans in a build run."""
    return [validate_plan(p) for p in plans]


# ---------------------------------------------------------------------------
# Rich terminal output
# ---------------------------------------------------------------------------

def print_dry_run_summary(report: BuildReport, results: List[ValidationResult]):
    """
    Print a rich terminal dry-run summary table.
    Shows what WOULD be generated without writing anything.
    """
    console.print()
    console.print(Panel.fit(
        "[bold yellow]DRY RUN SUMMARY[/bold yellow]\n"
        "[dim]No files were written. Review the plan below before running --write.[/dim]",
        border_style="yellow",
    ))
    console.print()

    # Overview table
    tbl = Table(
        title="[bold]Build Overview[/bold]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on blue",
    )
    tbl.add_column("Field", style="bold", width=25)
    tbl.add_column("Value", width=50)
    tbl.add_row("Input File", report.input_file)
    tbl.add_row("Output Dir", report.output_dir)
    tbl.add_row("Mode", "[yellow]DRY RUN[/yellow] - no files written")
    tbl.add_row("Env Path", report.env_path)
    tbl.add_row("Total Resources Read", str(report.total_resources))
    tbl.add_row("Resources Needing Action", str(report.resources_needing_action))
    tbl.add_row("Resources Skipped", str(report.resources_skipped))
    tbl.add_row("Plans Created", str(len(report.plans)))
    total_files = sum(p.file_count for p in report.plans)
    tbl.add_row("Files That Would Be Written", str(total_files))
    console.print(tbl)
    console.print()

    # Per-plan table
    if results:
        plan_tbl = Table(
            title="[bold]Planned Files per Resource[/bold]",
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold white on dark_blue",
        )
        plan_tbl.add_column("Resource", min_width=30)
        plan_tbl.add_column("Service", min_width=15)
        plan_tbl.add_column("Files", justify="center")
        plan_tbl.add_column("TODOs", justify="center")
        plan_tbl.add_column("Warnings", justify="center")
        plan_tbl.add_column("Valid", justify="center")

        for vr in results:
            plan = vr.plan
            rn = plan.resource.name if plan.resource else "N/A"
            svc = plan.service_type.value if plan.service_type else "?"
            files = str(plan.file_count)
            todos = str(vr.total_todos)
            warns = str(len(vr.warnings))
            valid_text = Text("YES", style="green") if vr.is_valid else Text("NO", style="red")
            plan_tbl.add_row(rn, svc, files, todos, warns, valid_text)
        console.print(plan_tbl)
        console.print()

    # File listing
    for vr in results:
        plan = vr.plan
        rn = plan.resource.name if plan.resource else "?"
        console.print(f"[bold cyan]{rn}[/bold cyan] — {plan.description}")
        for gf in plan.planned_files:
            fname = gf.relative_path.split("/")[-1]
            todos = vr.todo_counts.get(fname, 0)
            todo_str = f" [yellow]({todos} TODOs)[/yellow]" if todos else ""
            console.print(f"  [green]+[/green] {gf.relative_path}{todo_str}")
        if vr.warnings:
            for w in vr.warnings:
                console.print(f"  [yellow]⚠[/yellow]  {w}")
        if vr.errors:
            for e in vr.errors:
                console.print(f"  [red]✗[/red]  {e}")
        console.print()

    # Next steps
    console.print("[bold]Next Steps:[/bold]")
    console.print("  [cyan]1.[/cyan] Review the plan output above")
    console.print("  [cyan]2.[/cyan] Run with [bold]--write[/bold] to generate all files into ./generated")
    console.print("  [cyan]3.[/cyan] Review generated files and fill all TODO items")
    console.print("  [cyan]4.[/cyan] Copy reviewed files into your terraform-scripts/terraform-modules repos")
    console.print("  [cyan]5.[/cyan] Run terraform init → plan → import (if needed) → apply (with approval)")
    console.print()


def print_write_summary(report: BuildReport):
    """Print a rich summary after file writing."""
    console.print()
    console.print(Panel.fit(
        f"[bold green]GENERATION COMPLETE[/bold green]\n"
        f"[dim]{report.total_files_written} file(s) written to: {report.output_dir}[/dim]",
        border_style="green",
    ))
    console.print()
    for plan in report.plans:
        rn = plan.resource.name if plan.resource else "?"
        if plan.status == GenerationStatus.GENERATED:
            console.print(f"[green]✓[/green] {rn}")
            for gf in plan.planned_files:
                if gf.written:
                    console.print(f"    [dim]{gf.full_path}[/dim]")
        elif plan.status == GenerationStatus.ERROR:
            console.print(f"[red]✗[/red] {rn} — Errors:")
            for err in plan.errors:
                console.print(f"    [red]{err}[/red]")
    console.print()
    console.print("[bold yellow]IMPORTANT:[/bold yellow]")
    console.print("  All generated files are DRAFTS. Do NOT terraform apply without:")
    console.print("  1. Filling all TODO values")
    console.print("  2. Running terraform plan and reviewing output")
    console.print("  3. Running import commands for existing Azure resources")
    console.print()
