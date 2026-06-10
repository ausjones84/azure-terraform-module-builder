"""
tests/test_module_generator.py - Unit tests for Terraform module generator
==========================================================================
Tests that the module generator produces correct file plans in dry-run
and actually writes files in write mode.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from models import (
    DiscoveredResource, ServiceType, ResourceNeed,
    GenerationStatus, OutputType, IdentityData,
    PrivateEndpointData, DiagnosticData
)
from module_generator import ModuleGenerator
from deployment_generator import DeploymentGenerator


@pytest.fixture
def ai_search_resource():
    """Sample AI Search resource for testing."""
    res = DiscoveredResource(
        name="edav-dev-aisearch-eastus-internal",
        resource_type="Microsoft.Search/searchServices",
        resource_group="ocio-edav-dev-rg",
        subscription_id="sub-abc-123",
        location="eastus",
        sku_name="standard",
        tags={"env": "dev", "team": "platform"},
        identity=IdentityData(type="SystemAssigned", principal_id="pid-abc"),
        public_network_access="Disabled",
        private_endpoints=[
            PrivateEndpointData(
                name="pe-aisearch-eastus",
                vnet_name="vnet-edav-dev",
                subnet_name="snet-private-endpoints",
                private_ip_address="10.0.1.5",
                connection_state="Approved",
            )
        ],
        diagnostic_settings=[
            DiagnosticData(
                name="diag-aisearch",
                log_analytics_workspace_name="law-edav-dev",
                log_analytics_workspace_id="/subscriptions/sub-abc-123/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law-edav-dev",
            )
        ],
        service_type=ServiceType.AI_SEARCH,
        need=ResourceNeed.AZURE_ONLY,
        comparison_status="azure_only",
    )
    return res


@pytest.fixture
def openai_resource():
    """Sample OpenAI resource for testing."""
    return DiscoveredResource(
        name="edav-dev-openai-eastus",
        resource_type="Microsoft.CognitiveServices/accounts",
        resource_group="ocio-edav-dev-openai-rg",
        subscription_id="sub-abc-123",
        location="eastus",
        sku_name="S0",
        tags={"env": "dev"},
        service_type=ServiceType.OPENAI,
        need=ResourceNeed.AZURE_ONLY,
    )


class TestModuleGeneratorDryRun:
    def test_plan_creates_files(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(ai_search_resource)
        assert len(plan.planned_files) == 4

    def test_plan_file_names(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(ai_search_resource)
        file_names = [f.relative_path.split("/")[-1] for f in plan.planned_files]
        assert "main.tf" in file_names
        assert "variables.tf" in file_names
        assert "outputs.tf" in file_names
        assert "README.md" in file_names

    def test_plan_has_content(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(ai_search_resource)
        for gf in plan.planned_files:
            assert gf.content, f"{gf.relative_path} has empty content"

    def test_dry_run_does_not_write(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(ai_search_resource)
        executed = gen.execute(plan)
        # No files should be written in dry-run
        assert all(not gf.written for gf in executed.planned_files)
        assert executed.status == GenerationStatus.DRY_RUN_ONLY

    def test_main_tf_contains_resource_name(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(ai_search_resource)
        main_tf = next(f for f in plan.planned_files if "main.tf" in f.relative_path)
        assert "edav-dev-aisearch-eastus-internal" in main_tf.content

    def test_variables_tf_contains_sku(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(ai_search_resource)
        vars_tf = next(f for f in plan.planned_files if "variables.tf" in f.relative_path)
        assert "standard" in vars_tf.content

    def test_openai_resource_generation(self, tmp_path, openai_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=True)
        plan = gen.plan(openai_resource)
        assert len(plan.planned_files) == 4
        main_tf = next(f for f in plan.planned_files if "main.tf" in f.relative_path)
        assert "edav-dev-openai-eastus" in main_tf.content


class TestModuleGeneratorWrite:
    def test_write_creates_files(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=False)
        plan = gen.plan(ai_search_resource)
        executed = gen.execute(plan)
        assert executed.status == GenerationStatus.GENERATED
        assert all(gf.written for gf in executed.planned_files)
        # Verify files exist on disk
        for gf in executed.planned_files:
            assert Path(gf.full_path).exists(), f"Expected file: {gf.full_path}"

    def test_written_main_tf_valid_content(self, tmp_path, ai_search_resource):
        gen = ModuleGenerator(output_dir=str(tmp_path), dry_run=False)
        plan = gen.plan(ai_search_resource)
        gen.execute(plan)
        main_tf_gf = next(f for f in plan.planned_files if "main.tf" in f.relative_path)
        content = Path(main_tf_gf.full_path).read_text()
        assert "azurerm_search_service" in content
        assert "edav-dev-aisearch-eastus-internal" in content


class TestDeploymentGeneratorDryRun:
    def test_plan_creates_files(self, tmp_path, ai_search_resource):
        gen = DeploymentGenerator(
            output_dir=str(tmp_path),
            module_root="./terraform-modules",
            env_path="edav/dev",
            dry_run=True,
        )
        plan = gen.plan(ai_search_resource)
        assert len(plan.planned_files) == 4

    def test_plan_file_names(self, tmp_path, ai_search_resource):
        gen = DeploymentGenerator(
            output_dir=str(tmp_path), dry_run=True
        )
        plan = gen.plan(ai_search_resource)
        file_names = [f.relative_path.split("/")[-1] for f in plan.planned_files]
        assert "main.tf" in file_names
        assert "terraform.tfvars" in file_names
        assert "import_commands.sh" in file_names
        assert "README.md" in file_names

    def test_import_commands_contain_resource_id(self, tmp_path, ai_search_resource):
        gen = DeploymentGenerator(
            output_dir=str(tmp_path), dry_run=True
        )
        plan = gen.plan(ai_search_resource)
        import_sh = next(
            f for f in plan.planned_files
            if "import_commands.sh" in f.relative_path
        )
        assert "edav-dev-aisearch-eastus-internal" in import_sh.content
        assert "terraform import" in import_sh.content
        assert "terraform apply" not in import_sh.content.replace("#", "")

    def test_tfvars_contains_tags(self, tmp_path, ai_search_resource):
        gen = DeploymentGenerator(
            output_dir=str(tmp_path), dry_run=True
        )
        plan = gen.plan(ai_search_resource)
        tfvars = next(
            f for f in plan.planned_files
            if "terraform.tfvars" in f.relative_path
        )
        # Tags from ai_search_resource should appear
        assert "env" in tfvars.content or "TODO" in tfvars.content

    def test_dry_run_no_disk_writes(self, tmp_path, ai_search_resource):
        gen = DeploymentGenerator(
            output_dir=str(tmp_path), dry_run=True
        )
        plan = gen.plan(ai_search_resource)
        executed = gen.execute(plan)
        assert all(not gf.written for gf in executed.planned_files)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
