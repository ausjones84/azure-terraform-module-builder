#!/usr/bin/env python3
"""
cli.py - Azure Terraform Module Builder - Main CLI Entry Point
==============================================================
Reads DR discovery tool output and generates Terraform module
scaffolds and deployment folders for missing resources.

Usage:
    # Dry-run (default) - plan only, write nothing
    python src/cli.py --input ./reports/discovery_20260610.json \\
        --output ./generated \\
        --module-root ./terraform-modules \\
        --env-path edav/dev

    # Generate files
    python src/cli.py --input ./reports/discovery_20260610.json \\
        --output ./generated \\
        --write

    # Generate for a specific service only
    python src/cli.py --input ./reports/discovery_20260610.xlsx \\
        --output ./generated \\
        --service ai_search \\
        --write

    # Generate both module scaffold AND deployment folder
    python src/cli.py --input ./reports/discovery.json \\
        --output ./generated \\
        --generate-modules --generate-deployments \\
        --write

SAFETY:
    Default mode is DRY-RUN. No files are written unless --write is passed.
    terraform apply is NEVER run.
    Azure is NEVER modified.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from models import BuildReport, GenerationStatus, ServiceType, ResourceNeed
from discovery_reader import read_discovery_file, filter_actionable
from module_generator import ModuleGenerator
from deployment_generator import DeploymentGenerator
from stub_validator import (
    validate_all_plans, print_dry_run_summary, print_write_summary
)

console = Console()
logger = logging.getLogger(__name__)

BANNER = """
[bold blue]Azure Terraform Module Builder[/bold blue]
[dim]Reads DR discovery output. Generates safe, reviewable Terraform scaffolds.[/dim]
[dim]Default mode: DRY-RUN only. Use --write to generate files.[/dim]
"""


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load optional YAML config file."""
    p = Path(config_path)
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Config load failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_builder(
    input_file: str,
    output_dir: str,
    module_root: str,
    env_path: str,
    services: Optional[List[str]],
    generate_modules: bool,
    generate_deployments: bool,
    write: bool,
    templates_dir: Optional[str],
) -> BuildReport:
    """
    Core module builder pipeline.

    Steps:
    1. Read discovery input file
    2. Filter actionable resources
    3. Plan module and/or deployment generation
    4. Validate plans (dry-run)
    5. Write files if --write mode
    6. Print summary report
    """
    console.print(Panel.fit(BANNER, border_style="blue"))
    mode_label = "[green]WRITE MODE[/green]" if write else "[yellow]DRY-RUN MODE[/yellow]"
    console.print(f"[bold]Mode:[/bold] {mode_label}")
    console.print(f"[bold]Input:[/bold] {input_file}")
    console.print(f"[bold]Output:[/bold] {output_dir}")
    console.print(f"[bold]Env Path:[/bold] {env_path}")
    console.print()

    report = BuildReport(
        input_file=input_file,
        output_dir=output_dir,
        module_root=module_root,
        env_path=env_path,
        dry_run=not write,
        run_timestamp=datetime.now().isoformat(timespec="seconds"),
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:

        # Step 1: Read input
        task1 = progress.add_task("[cyan]Reading discovery file...", total=None)
        try:
            all_resources = read_discovery_file(input_file)
            report.total_resources = len(all_resources)
            progress.update(task1, description=f"[green]Read {len(all_resources)} resources")
        except Exception as exc:
            console.print(f"[red]Failed to read input file: {exc}[/red]")
            logger.exception("Input read error")
            return report
        progress.stop_task(task1)

        # Step 2: Filter
        task2 = progress.add_task("[cyan]Filtering actionable resources...", total=None)
        actionable = filter_actionable(all_resources)
        # Apply service filter
        if services:
            svc_set = {s.lower() for s in services}
            actionable = [r for r in actionable if r.service_type.value in svc_set]
        report.resources_needing_action = len(actionable)
        report.resources_skipped = report.total_resources - len(actionable)
        progress.update(task2, description=f"[green]{len(actionable)} resource(s) need generation")
        progress.stop_task(task2)

        if not actionable:
            console.print("[yellow]No resources require module/deployment generation.[/yellow]")
            console.print("[dim]All discovered resources may already be Terraform managed.[/dim]")
            return report

        # Step 3: Plan
        task3 = progress.add_task("[cyan]Planning generation...", total=None)
        module_gen = ModuleGenerator(
            output_dir=output_dir,
            templates_dir=templates_dir,
            dry_run=not write,
        )
        deploy_gen = DeploymentGenerator(
            output_dir=output_dir,
            module_root=module_root,
            env_path=env_path,
            templates_dir=templates_dir,
            dry_run=not write,
        )

        for resource in actionable:
            if generate_modules:
                plan = module_gen.plan(resource)
                report.plans.append(plan)
            if generate_deployments:
                plan = deploy_gen.plan(resource)
                report.plans.append(plan)
        progress.update(task3, description=f"[green]Planned {len(report.plans)} generation task(s)")
        progress.stop_task(task3)

        # Step 4: Validate
        task4 = progress.add_task("[cyan]Validating plans...", total=None)
        validation_results = validate_all_plans(report.plans)
        progress.update(task4, description="[green]Validation complete")
        progress.stop_task(task4)

        # Step 5: Write (if requested)
        if write:
            task5 = progress.add_task("[cyan]Writing files...", total=None)
            for i, plan in enumerate(report.plans):
                if plan.service_type == _infer_service(plan):
                    pass  # Already typed
                if generate_modules and plan.module_output_path:
                    updated = module_gen.execute(plan)
                    report.plans[i] = updated
                elif generate_deployments and plan.deployment_output_path:
                    updated = deploy_gen.execute(plan)
                    report.plans[i] = updated
            progress.update(task5, description="[green]Files written")
            progress.stop_task(task5)

    # Step 6: Print summary
    if not write:
        print_dry_run_summary(report, validation_results)
    else:
        print_write_summary(report)

    # Next steps
    report.next_steps = _build_next_steps(report, write)
    report.summary_lines = _build_summary(report, actionable)
    return report


def _infer_service(plan):
    return plan.service_type


def _build_summary(report: BuildReport, actionable: list) -> List[str]:
    lines = []
    for res in actionable:
        lines.append(
            f"{res.name} ({res.service_type.value}) "
            f"- Need: {res.need.value} "
            f"- Status: {res.comparison_status}"
        )
    return lines


def _build_next_steps(report: BuildReport, write: bool) -> List[str]:
    steps = []
    if not write:
        steps.append("Run with --write flag to generate files into ./generated")
    else:
        steps.append(f"Review generated files in: {report.output_dir}")
        steps.append("Fill all TODO values in main.tf and terraform.tfvars")
        steps.append("Run terraform init in each deployment folder")
        steps.append("Run terraform plan -var-file=terraform.tfvars and review output")
        steps.append("Run import_commands.sh for resources that already exist in Azure")
        steps.append("Get team approval before terraform apply")
    return steps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--input", "-i", "input_file",
    required=True,
    help="Path to DR discovery output file (.json or .xlsx)."
)
@click.option(
    "--output", "-o",
    default="./generated",
    show_default=True,
    help="Output directory for generated files."
)
@click.option(
    "--module-root", "-m",
    default="./terraform-modules",
    show_default=True,
    help="Path to existing terraform-modules repository."
)
@click.option(
    "--env-path",
    default="edav/dev",
    show_default=True,
    help="Environment path for deployment folders (e.g. edav/dev)."
)
@click.option(
    "--service", "-s",
    multiple=True,
    type=click.Choice(
        ["ai_search", "openai", "ai_foundry", "private_endpoint",
         "diagnostic_setting", "rbac", "generic"],
        case_sensitive=False,
    ),
    help="Filter to specific service type(s) only. Repeatable."
)
@click.option(
    "--generate-modules/--no-modules",
    default=True,
    show_default=True,
    help="Generate Terraform module scaffolds."
)
@click.option(
    "--generate-deployments/--no-deployments",
    default=True,
    show_default=True,
    help="Generate Terraform deployment folders."
)
@click.option(
    "--write",
    is_flag=True,
    default=False,
    help="[EXPLICIT] Write generated files to disk. Default is dry-run only."
)
@click.option(
    "--config", "-c",
    default=None,
    help="Optional YAML config file."
)
@click.option(
    "--templates-dir",
    default=None,
    help="Path to Jinja2 templates directory."
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable verbose debug logging."
)
def main(
    input_file, output, module_root, env_path,
    service, generate_modules, generate_deployments,
    write, config, templates_dir, verbose,
):
    """
    Azure Terraform Module Builder

    Reads discovery tool output and generates Terraform module scaffolds
    and deployment folders for resources needing Terraform management.

    Default mode is DRY-RUN. Pass --write to generate files.
    terraform apply is NEVER run by this tool.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config(config) if config else {}

    run_builder(
        input_file=input_file or cfg.get("input_file", ""),
        output_dir=output or cfg.get("output_dir", "./generated"),
        module_root=module_root or cfg.get("module_root", "./terraform-modules"),
        env_path=env_path or cfg.get("env_path", "edav/dev"),
        services=list(service) or cfg.get("services"),
        generate_modules=generate_modules,
        generate_deployments=generate_deployments,
        write=write,
        templates_dir=templates_dir or cfg.get("templates_dir"),
    )


if __name__ == "__main__":
    main()
