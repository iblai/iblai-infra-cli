"""Step 5 — Review configuration and confirm."""

from __future__ import annotations

import questionary

from iblai_infra import ui
from iblai_infra.models import CertMethod, InfraConfig, SSHKeyMethod

TOTAL_STEPS = 5


def prompt_review(config: InfraConfig) -> bool:
    """Display a full summary of the configuration and ask for confirmation."""

    ui.step_header(5, TOTAL_STEPS, "Review")

    # ----- Build summary rows -----
    rows: list[tuple[str, str]] = []

    # Project
    rows.append(("", "[bold]Project[/bold]"))
    rows.append(("Name", config.project_name))
    rows.append(("Environment", config.environment.value.capitalize()))
    rows.append(("Resource prefix", config.resource_prefix))

    # AWS
    rows.append(("", ""))
    rows.append(("", "[bold]AWS[/bold]"))
    rows.append(("Region", config.credentials.region))
    rows.append(("Account", config.credentials.account_id or "—"))
    rows.append(("Auth method", config.credentials.method.value))

    # Compute
    rows.append(("", ""))
    rows.append(("", "[bold]Compute[/bold]"))
    rows.append(("Instance type", config.compute.instance_type))
    rows.append(("Volume", f"{config.compute.volume_size} GB {config.compute.volume_type}"))
    rows.append(("OS", "Ubuntu 22.04 LTS"))

    # Network
    rows.append(("", ""))
    rows.append(("", "[bold]Network[/bold]"))
    rows.append(("VPC CIDR", config.network.vpc_cidr))
    rows.append(("Subnets", "2 public (multi-AZ)"))
    rows.append(("SSH access", f"{config.network.vpn_ip}/32 only"))
    rows.append(("Load balancer", "Application LB (internet-facing)"))

    # SSH
    rows.append(("", ""))
    rows.append(("", "[bold]SSH Key[/bold]"))
    if config.ssh.method == SSHKeyMethod.GENERATE:
        rows.append(("Key", f"Generated ({config.ssh.key_name})"))
        if config.ssh.private_key_path:
            rows.append(("Private key", str(config.ssh.private_key_path)))
    elif config.ssh.method == SSHKeyMethod.EXISTING_FILE:
        rows.append(("Key", f"Provided ({config.ssh.key_name})"))
    else:
        rows.append(("Key", f"AWS key pair ({config.ssh.key_name})"))

    # DNS & Certificates
    rows.append(("", ""))
    rows.append(("", "[bold]Domain & Certificates[/bold]"))
    rows.append(("Domain", config.dns.base_domain))
    if config.certificates.method == CertMethod.ACM:
        rows.append(("DNS", "Route53 (auto-managed)"))
        rows.append(("Certificates", "ACM (auto-provisioned)"))
        rows.append(("Subdomains", f"{len(config.dns.subdomains)} records"))
    elif config.certificates.method == CertMethod.UPLOAD:
        rows.append(("DNS", "External (user-managed)"))
        rows.append(("Certificates", "Uploaded (ALB termination)"))
    else:
        rows.append(("DNS", "External (user-managed)"))
        rows.append(("Certificates", "None (HTTP only)"))

    # Storage
    rows.append(("", ""))
    rows.append(("", "[bold]Storage[/bold]"))
    rows.append(("S3 buckets", "3 (backups, media, static)"))

    ui.summary_panel("Infrastructure Summary", rows)

    # ----- Confirm -----
    proceed = questionary.confirm(
        "Proceed with infrastructure creation?",
        default=True,
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    if proceed is None or not proceed:
        ui.abort("Cancelled — no infrastructure was created.")

    return True
