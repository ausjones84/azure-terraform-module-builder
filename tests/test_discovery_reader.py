"""
tests/test_discovery_reader.py - Unit tests for discovery input parser
======================================================================
Tests for reading and filtering DR discovery tool output.
"""

import json
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from models import (
    DiscoveredResource, ServiceType, ResourceNeed,
    PrivateEndpointData, DiagnosticData
)
from discovery_reader import (
    read_json_report, filter_actionable,
    _classify_service_type, _classify_need, _parse_raw_resource
)


@pytest.fixture
def sample_json_report(tmp_path):
    """Create a minimal DR tool JSON report for testing."""
    data = {
        "service": "ai_search",
        "resource_name": "test-search",
        "subscription_id": "sub-123",
        "azure_resources": [
            {
                "name": "edav-dev-aisearch-eastus-internal",
                "resource_type": "Microsoft.Search/searchServices",
                "resource_group": "ocio-edav-dev-rg",
                "subscription_id": "sub-123",
                "location": "eastus",
                "sku_name": "standard",
                "tags": {"env": "dev", "team": "platform"},
                "identity": {"type": "SystemAssigned", "principal_id": "pid-123"},
                "public_network_access": "Disabled",
                "private_endpoints": [
                    {
                        "name": "pe-aisearch-eastus",
                        "resource_group": "ocio-edav-dev-rg",
                        "vnet_name": "vnet-edav-dev",
                        "subnet_name": "snet-private-endpoints",
                        "subnet_id": "/subscriptions/sub-123/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/snet",
                        "private_ip_address": "10.0.1.5",
                        "nic_name": "pe-aisearch-nic",
                        "connection_state": "Approved",
                        "group_ids": ["searchService"],
                        "dns_configs": [{"fqdn": "edav-dev-aisearch.search.windows.net", "private_ip": "10.0.1.5"}]
                    }
                ],
                "diagnostic_settings": [
                    {
                        "name": "diag-aisearch",
                        "log_analytics_workspace_id": "/subscriptions/sub-123/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law-edav-dev",
                        "log_analytics_workspace_name": "law-edav-dev"
                    }
                ],
                "_comparison_status": "azure_only",
                "_risk_level": "high"
            },
            {
                "name": "edav-dev-openai-eastus",
                "resource_type": "Microsoft.CognitiveServices/accounts",
                "resource_group": "ocio-edav-dev-openai-rg",
                "subscription_id": "sub-123",
                "location": "eastus",
                "sku_name": "S0",
                "tags": {},
                "public_network_access": "Disabled",
                "_comparison_status": "terraform_managed"
            }
        ]
    }
    report_file = tmp_path / "test_report.json"
    report_file.write_text(json.dumps(data))
    return str(report_file)


class TestReadJsonReport:
    def test_reads_resources(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        assert len(resources) == 2

    def test_resource_name_parsed(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        names = [r.name for r in resources]
        assert "edav-dev-aisearch-eastus-internal" in names

    def test_private_endpoint_parsed(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        search = next(r for r in resources if "aisearch" in r.name)
        assert len(search.private_endpoints) == 1
        pe = search.private_endpoints[0]
        assert pe.name == "pe-aisearch-eastus"
        assert pe.vnet_name == "vnet-edav-dev"
        assert pe.private_ip_address == "10.0.1.5"
        assert pe.connection_state == "Approved"

    def test_diagnostic_setting_parsed(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        search = next(r for r in resources if "aisearch" in r.name)
        assert len(search.diagnostic_settings) == 1
        assert "law-edav-dev" in search.diagnostic_settings[0].log_analytics_workspace_name

    def test_comparison_status_parsed(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        search = next(r for r in resources if "aisearch" in r.name)
        assert search.comparison_status == "azure_only"

    def test_service_type_classified(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        search = next(r for r in resources if "aisearch" in r.name)
        assert search.service_type == ServiceType.AI_SEARCH

    def test_need_classified(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        search = next(r for r in resources if "aisearch" in r.name)
        assert search.need == ResourceNeed.AZURE_ONLY

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            read_json_report("/nonexistent/path/report.json")

    def test_invalid_json(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {")
        with pytest.raises(ValueError):
            read_json_report(str(bad_file))


class TestFilterActionable:
    def test_filters_terraform_managed(self, sample_json_report):
        resources = read_json_report(sample_json_report)
        actionable = filter_actionable(resources)
        # Only azure_only should be actionable, not terraform_managed
        names = [r.name for r in actionable]
        assert "edav-dev-aisearch-eastus-internal" in names
        assert "edav-dev-openai-eastus" not in names

    def test_empty_list(self):
        assert filter_actionable([]) == []


class TestClassifyServiceType:
    def test_ai_search(self):
        res = DiscoveredResource(resource_type="Microsoft.Search/searchServices")
        assert _classify_service_type(res) == ServiceType.AI_SEARCH

    def test_cognitive_services(self):
        res = DiscoveredResource(resource_type="Microsoft.CognitiveServices/accounts")
        svc = _classify_service_type(res)
        assert svc in (ServiceType.OPENAI, ServiceType.AI_FOUNDRY)

    def test_private_endpoint(self):
        res = DiscoveredResource(resource_type="Microsoft.Network/privateEndpoints")
        assert _classify_service_type(res) == ServiceType.PRIVATE_ENDPOINT

    def test_unknown_falls_to_generic(self):
        res = DiscoveredResource(resource_type="Microsoft.Unknown/resources")
        assert _classify_service_type(res) == ServiceType.GENERIC


class TestParseRawResource:
    def test_identity_parsed(self):
        raw = {
            "name": "test",
            "identity": {"type": "SystemAssigned", "principal_id": "abc"},
        }
        res = _parse_raw_resource(raw)
        assert res.identity is not None
        assert res.identity.type == "SystemAssigned"
        assert res.identity.principal_id == "abc"

    def test_tags_parsed(self):
        raw = {
            "name": "test",
            "tags": {"env": "dev", "owner": "platform"},
        }
        res = _parse_raw_resource(raw)
        assert res.tags["env"] == "dev"
        assert res.tags["owner"] == "platform"

    def test_empty_resource(self):
        res = _parse_raw_resource({})
        assert res.name == ""
        assert res.tags == {}
        assert res.private_endpoints == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
