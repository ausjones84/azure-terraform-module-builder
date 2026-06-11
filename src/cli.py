#!/usr/bin/env python3
"""
cli.py - Azure Terraform Module Builder - Main CLI Entry Point
==============================================================
Reads DR discovery tool output and generates Terraform module
scaffolds and deployment folders for missing resources.

Usage:
  # Auth check only
  python src/cli.py --auth-check

  # Dry-run (default) - plan only, write nothing
  python src/cli.py --input ./reports/discovery_20260610.json \
    --output ./generated \
    --module-root ./terraform-modules \
    --env-path edav/dev

  # Generate files (requires --write)
  python src/cli.py --input ./reports/discovery_20260610.json \
    --output ./generated \
    --write

  # Generate for a specific service
  python src/cli.py --input ./reports/discovery_20260610.xlsx \
    --output ./generated \
    --service ai_search \
    --write

  # Generate both module scaffold AND deployment folder
  python src/cli.py --input ./reports/discovery.json \
    --output ./generated \
    --generate-modules --generate-deployments \
    --write

  # Skip auth check (not recommended)
  python src/cli.py --input ./reports/discovery.json --skip-auth-check

SAFETY:
  Default mode is DRY-RUN. No files written unless --write is passed.
  terraform apply is NEVER run.
  Azure is NEVER modified.
  Authentication pre-flight runs by default to verify Azure access.
  --write operations require valid Azure CLI login.
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
from auth_guard import enforce_auth_guard, run_auth_guard, print_auth_guard_result

console = Console()
logger = logging.getLogger(__name__)

BANNER = """
[bold blue]Azure Terraform Module Builder[/bold blue]
[dim]Reads DR discovery output. Generates safe, reviewable Terraform scaffolds.[/dim]
[dim]Default mode: DRY-RUN only. Use --write to generate files.[/dim]
"""


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
    skip_auth_check: bool = False,
) -> BuildReport:
    """
    Core module builder pipeline.

    Steps:
    0. Auth pre-flight check
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

    # -----------------------------------------------------------------------
    # Step 0: Authentication Pre-flight
    # -----------------------------------------------------------------------
    if not skip_auth_check:
        # For write mode: require Azure login
        # For dry-run: warn but allow if not logged in
        require_login = write  # Enforce login when writing files
        guard_result = run_auth_guard(
            check_azure=True,
            check_terraform=True,
            require_azure_login=require_login,
            require_tf_auth=False,  # TF auth only needed for apply, which we never do
        )
        print_auth_guard_result(guard_result)
        console.print()

        if require_login and not guard_result.passed:
            console.print("[bold red]Auth pre-flight failed. Aborting.[/bold red]")
            console.print("[dim]Use --skip-auth-check to bypass (not recommended for write mode).[/dim]")
            sys.exit(1)
        elif not guard_result.az_logged_in:
            console.print("[yellow]WARNING: Not logged in to Azure CLI. Dry-run mode only.[/yellow]")
            console.print("[dim]Run az login before using --write mode.[/dim]")
            console.print()
    else:
        console.print("[yellow]WARNING: Auth pre-flight skipped (--skip-auth-check).[/yellow]")
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

        task2 = progress.add_task("[cyan]Filtering actionable resources...", total=None)
        actionable = filter_actionable(all_resources)
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

        task4 = progress.add_task("[cyan]Validating plans...", total=None)
        validation_results = validate_all_plans(report.plans)
        progress.update(task4, description="[green]Validation complete")
        progress.stop_task(task4)

        if write:
            task5 = progress.add_task("[cyan]Writing files...", total=None)
            for i, plan in enumerate(report.plans):
                if generate_modules and getattr(plan, "module_output_path", None):
                    updated = module_gen.execute(plan)
                    report.plans[i] = updated
                elif generate_deployments and getattr(plan, "deployment_output_path", None):
                    updated = deploy_gen.execute(plan)
                    report.plans[i] = updated
            progress.update(task5, description="[green]Files written")
            progress.stop_task(task5)

    if not write:
        print_dry_run_summary(report, validation_results)
    else:
        print_write_summary(report)

    report.next_steps = _build_next_steps(report, write)
    report.summary_lines = _build_summary(report, actionable)
    return report


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


@click.command()
@click.option("--input", "-i", "input_file", default=None,
    help="Path to DR discovery output file (.json or .xlsx).")
@click.option("--output", "-o", default="./generated", show_default=True)
@click.option("--module-root", "-m", default="./terraform-modules", show_default=True)
@click.option("--env-path", default="edav/dev", show_default=True)
@click.option("--service", "-s", multiple=True,
    type=click.Choice([
        "ai_search", "openai", "ai_foundry", "private_endpoint",
        "diagnostic_setting", "rbac", "generic"
    ], case_sensitive=False),
    help="Filter to specific service type(s). Repeatable.")
@click.option("--generate-modules/--no-modules", default=True)
@click.option("--generate-deployments/--no-deployments", default=True)
@click.option("--write", is_flag=True, default=False,
    help="[EXPLICIT] Write generated files to disk. Default is dry-run only.")
@click.option("--config", "-c", default=None)
@click.option("--templates-dir", default=None)
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.option("--skip-auth-check", is_flag=True, default=False,
    help="Skip authentication pre-flight check. Not recommended.")
@click.option("--auth-check", is_flag=True, default=False,
    help="Run auth check only without performing any generation.")
def main(
    input_file, output, module_root, env_path,
    service, generate_modules, generate_deployments,
    write, config, templates_dir, verbose,
    skip_auth_check, auth_check,
):
    """
    Azure Terraform Module Builder

    Reads discovery tool output and generates Terraform module scaffolds
    and deployment folders for resources needing Terraform management.

    Default mode is DRY-RUN. Pass --write to generate files.
    terraform apply is NEVER run by this tool.
    Authentication pre-flight runs by default.
    Use --auth-check to verify authentication without generation.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Auth-check-only mode
    if auth_check:
        console.print(Panel.fit(BANNER, border_style="blue"))
        console.print("[bold]Authentication Check Mode[/bold]")
        console.print()
        enforce_auth_guard(require_azure_login=False, require_tf_auth=False)
        sys.exit(0)

    if not input_file:
        console.print("[red]Error: --input is required for generation. Use --auth-check for auth only.[/red]")
        sys.exit(1)

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
        skip_auth_check=skip_auth_check,
    )


if __name__ == "__main__":
    main()
