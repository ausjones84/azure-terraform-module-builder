# Azure Terraform Module Builder

> **Reads output from [azure-terraform-dr-discovery-tool](https://github.com/ausjones84/azure-terraform-dr-discovery-tool) and generates safe, reviewable Terraform module scaffolds and deployment folders.**
>
> Default mode is **DRY-RUN**. Nothing is written unless `--write` is explicitly passed.
> `terraform apply` is **NEVER** run. Azure is **NEVER** modified.

---

## How It Fits Into the Workflow

```
Azure Environment
       │
       ▼
azure-terraform-dr-discovery-tool  ←── Step 1: Discover & compare
  Outputs: discovery_YYYYMMDD.json / .xlsx
       │
       ▼
azure-terraform-module-builder     ←── Step 2: Generate scaffolds (this repo)
  Outputs: ./generated/modules/     (Terraform module scaffolds)
           ./generated/deployments/ (Deployment folder with module calls)
       │
       ▼
Human Review + Fill TODO values
       │
       ▼
Copy to terraform-scripts / terraform-modules repos
       │
       ▼
terraform init → plan → import → apply (with approval)
```

---

## Purpose

This tool takes the JSON/Excel output from the DR discovery tool and:

- **Reads** resource details (name, location, SKU, tags, private endpoints, diagnostics, identity)
- **Identifies** resources marked `azure_only`, `module_missing`, or `deployment_missing`
- **Generates** complete Terraform module scaffolds with known values pre-filled
- **Generates** deployment folders that call the module with environment-specific values
- **Marks** all unknown values with `# TODO` comments for human completion
- **Never** runs `terraform apply`, `terraform destroy`, or modifies Azure

---

## Supported Resource Types

| Service | Azure Resource Type | Terraform Resource |
|---------|-------------------|--------------------|
| AI Search | `Microsoft.Search/searchServices` | `azurerm_search_service` |
| Azure OpenAI | `Microsoft.CognitiveServices/accounts` (OpenAI) | `azurerm_cognitive_account` |
| AI Foundry | `Microsoft.CognitiveServices/accounts` (AIServices) | `azurerm_cognitive_account` |
| Private Endpoint | `Microsoft.Network/privateEndpoints` | `azurerm_private_endpoint` |
| Diagnostic Settings | `Microsoft.Insights/diagnosticSettings` | `azurerm_monitor_diagnostic_setting` |
| RBAC | `Microsoft.Authorization/roleAssignments` | `azurerm_role_assignment` |

---

## Safety Rules

| Rule | Detail |
|------|--------|
| **Default dry-run** | No files written unless `--write` is passed |
| **No terraform commands** | `apply`, `destroy`, `plan` are never executed |
| **No Azure changes** | No Azure CLI write commands |
| **Output isolation** | All output goes to `./generated/` for review |
| **Import warnings** | Every generated folder includes `import_commands.sh` with clear DO NOT RUN warnings |
| **TODO markers** | All unknown values clearly marked `# TODO` |
| **Safety headers** | Every generated file has a safety header explaining it is a draft |

---

## Setup

### Prerequisites

- Python 3.9+
- Output files from `azure-terraform-dr-discovery-tool` (`.json` or `.xlsx`)

### Installation

```bash
git clone https://github.com/ausjones84/azure-terraform-module-builder.git
cd azure-terraform-module-builder

python -m venv .venv
source .venv/bin/activate     # Linux/macOS
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

---

## Usage

### Step 1 — Dry-run (default): see what would be generated

```bash
python src/cli.py \
  --input ./reports/discovery_20260610.json \
  --output ./generated \
  --module-root ./terraform-modules \
  --env-path edav/dev
```

### Step 2 — Write: generate files into ./generated

```bash
python src/cli.py \
  --input ./reports/discovery_20260610.json \
  --output ./generated \
  --module-root ./terraform-modules \
  --env-path edav/dev \
  --write
```

### Generate for a specific service only

```bash
python src/cli.py \
  --input ./reports/discovery_20260610.xlsx \
  --output ./generated \
  --service ai_search \
  --write
```

### Generate module scaffolds only (no deployment folders)

```bash
python src/cli.py \
  --input ./reports/discovery.json \
  --output ./generated \
  --generate-modules --no-deployments \
  --write
```

### Use a config file

```bash
cp examples/sample_config.yaml my_config.yaml
# Edit my_config.yaml
python src/cli.py --config my_config.yaml --write
```

---

## Generated Output Structure

After running with `--write`, the `./generated/` folder contains:

```
generated/
├── modules/
│   ├── ai_search_service/
│   │   ├── main.tf          ← Resource definitions
│   │   ├── variables.tf     ← Input variables (known values pre-filled)
│   │   ├── outputs.tf       ← Output values
│   │   └── README.md        ← Module documentation
│   ├── cognitive_account/
│   │   └── ...
│   └── private_endpoint/
│       └── ...
└── deployments/
    └── edav/dev/
        ├── ai_search_service/
        │   ├── main.tf              ← Module call with pre-filled values
        │   ├── terraform.tfvars     ← Variable values (fill TODOs)
        │   ├── import_commands.sh   ← Import examples (review before running)
        │   └── README.md
        └── cognitive_account/
            └── ...
```

---

## Input File Formats

This tool reads output from `azure-terraform-dr-discovery-tool`.

### JSON format

Accepts the full JSON report output:
```json
{
  "azure_resources": [...],
  "comparison_results": [...]
}
```

Or a flat list of resource objects:
```json
[
  { "name": "...", "resource_type": "...", ... }
]
```

### Excel format

Reads the **Azure Resources** sheet. Enriches with data from the **Risks** sheet if present.

---

## What Gets Generated Per Resource

### Module Scaffold (`generated/modules/<module_name>/`)

| File | Content |
|------|---------|
| `main.tf` | `azurerm_*` resource block with known values pre-filled, `# TODO` for unknowns |
| `variables.tf` | All input variables; discovered values set as defaults |
| `outputs.tf` | Standard output values (id, name, endpoint, keys marked sensitive) |
| `README.md` | Module documentation with usage example |

### Deployment Folder (`generated/deployments/<env_path>/<module_name>/`)

| File | Content |
|------|---------|
| `main.tf` | Terraform provider block + module call with pre-filled values |
| `terraform.tfvars` | Variable overrides — fill `# TODO` values before use |
| `import_commands.sh` | Example import commands with DO NOT RUN warnings |
| `README.md` | Step-by-step deployment instructions |

---

## After Generation: What To Do Next

1. **Review** all generated files in `./generated/`
2. **Fill** all `# TODO` values (private IPs, workspace IDs, group IDs, etc.)
3. **Copy** reviewed files into your `terraform-scripts` and `terraform-modules` repos
4. **Run** `terraform init` in the deployment folder
5. **Run** `terraform plan -var-file=terraform.tfvars` — review **all** planned changes
6. **Run** import commands from `import_commands.sh` for resources that already exist in Azure
7. **Get team approval** before `terraform apply`

---

## CLI Reference

```
Options:
  -i, --input PATH           DR discovery output file (.json or .xlsx) [required]
  -o, --output PATH          Output directory [default: ./generated]
  -m, --module-root PATH     Terraform modules repository [default: ./terraform-modules]
  --env-path TEXT            Environment folder path [default: edav/dev]
  -s, --service CHOICE       Filter by service (repeatable):
                             ai_search, openai, ai_foundry,
                             private_endpoint, diagnostic_setting, rbac
  --generate-modules         Generate module scaffolds [default: on]
  --no-modules               Skip module generation
  --generate-deployments     Generate deployment folders [default: on]
  --no-deployments           Skip deployment generation
  --write                    Write files to disk [default: dry-run only]
  -c, --config PATH          YAML config file
  --templates-dir PATH       Custom Jinja2 templates directory
  -v, --verbose              Enable debug logging
  --help                     Show this message
```

---

## Repository Structure

```
azure-terraform-module-builder/
├── README.md
├── requirements.txt
├── .gitignore
├── src/
│   ├── cli.py                    # CLI entry point
│   ├── discovery_reader.py       # Reads DR tool JSON/Excel output
│   ├── module_generator.py       # Generates terraform-modules/ scaffolds
│   ├── deployment_generator.py   # Generates terraform-scripts/ deployment folders
│   ├── stub_validator.py         # Dry-run validation + rich terminal output
│   └── models.py                 # Dataclasses and enums
├── templates/
│   ├── module_ai_search_main.tf.j2
│   ├── module_ai_search_variables.tf.j2
│   ├── module_openai_main.tf.j2
│   ├── module_private_endpoint_main.tf.j2
│   └── ...                       # Additional service templates
├── examples/
│   └── sample_config.yaml
├── generated/                    # Output directory (review before use)
└── tests/
    ├── test_discovery_reader.py
    └── test_module_generator.py
```

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Relationship to azure-terraform-dr-discovery-tool

This tool is the **second step** in a two-tool workflow:

| Tool | Role |
|------|------|
| `azure-terraform-dr-discovery-tool` | Step 1: Discover Azure resources, compare vs Terraform, generate reports |
| `azure-terraform-module-builder` | Step 2: Read reports, generate Terraform module scaffolds and deployment folders |

Both tools are read-only/dry-run by default and never modify Azure or Terraform state.

---

> **All generated files are DRAFTS requiring human review. This tool never runs terraform apply, never modifies Azure, and never changes Terraform state.**
