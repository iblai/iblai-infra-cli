"""Step 4 — Domain, DNS, and Certificate configuration."""

from __future__ import annotations

from pathlib import Path

import questionary

from iblai_infra import ui
from iblai_infra.models import (
    AWSCredentials,
    CertificateConfig,
    CertMethod,
    DNSConfig,
    IBL_SUBDOMAINS,
)
from iblai_infra.providers.aws import get_session, list_hosted_zones

TOTAL_STEPS = 5


def prompt_dns_and_certs(credentials: AWSCredentials) -> tuple[DNSConfig, CertificateConfig]:
    """Prompt for domain, DNS provider, and certificate source."""

    ui.step_header(4, TOTAL_STEPS, "Domain & Certificates")

    # ----- base domain -----
    base_domain = questionary.text(
        "Base domain:",
        validate=lambda v: _validate_domain(v) or "Enter a valid domain (e.g. example.com)",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if base_domain is None:
        ui.abort()
    base_domain = base_domain.strip().lower()

    # ----- Route53 check -----
    session = get_session(credentials)
    zones = list_hosted_zones(session)

    # Find zones matching the domain
    matching_zones = [z for z in zones if base_domain.endswith(z.name) or z.name == base_domain]

    if matching_zones:
        ui.success(f"Found Route53 hosted zone(s) matching [highlight]{base_domain}[/highlight]")

        use_route53 = questionary.select(
            "DNS & Certificate strategy:",
            choices=[
                questionary.Choice(
                    "Use Route53 + ACM (auto-managed DNS & certificates)",
                    value="route53",
                ),
                questionary.Choice(
                    "Upload my own certificate files",
                    value="upload",
                ),
                questionary.Choice(
                    "Skip HTTPS for now (HTTP only)",
                    value="none",
                ),
            ],
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
    ).ask()
        if use_route53 is None:
            ui.abort()
    else:
        if zones:
            ui.muted(f"No Route53 hosted zone found for {base_domain}")
            ui.muted(f"Available zones: {', '.join(z.name for z in zones)}")
        else:
            ui.muted("No Route53 hosted zones found in this account")

        use_route53 = questionary.select(
            "Certificate strategy:",
            choices=[
                questionary.Choice(
                    "Upload my own certificate files (PEM format)",
                    value="upload",
                ),
                questionary.Choice(
                    "Skip HTTPS for now (HTTP only — ALB will listen on port 80)",
                    value="none",
                ),
            ],
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
    ).ask()
        if use_route53 is None:
            ui.abort()

    # ----- handle each path -----
    hosted_zone_id = None
    cert_config: CertificateConfig

    if use_route53 == "route53":
        # Let user pick the zone if multiple
        if len(matching_zones) == 1:
            zone = matching_zones[0]
            ui.info(f"Using zone: [highlight]{zone.name}[/highlight] ({zone.zone_id})")
        else:
            zone_selection = questionary.select(
                "Select hosted zone:",
                choices=[
                    questionary.Choice(
                        f"{z.name} ({z.zone_id}, {z.record_count} records)",
                        value=z,
                    )
                    for z in matching_zones
                ],
                style=ui.PROMPT_STYLE,
                qmark=ui.QMARK,
    ).ask()
            if zone_selection is None:
                ui.abort()
            zone = zone_selection

        hosted_zone_id = zone.zone_id

        # Show subdomains that will be created
        subdomains = [s.format(domain=base_domain) for s in IBL_SUBDOMAINS]
        ui.newline()
        ui.info("The following DNS A-records will be created (aliased to the ALB):")
        for sd in subdomains:
            ui.muted(f"  {sd}")

        ui.newline()
        ui.warning(
            "If any of these records already exist in the hosted zone "
            "(A, CNAME, or other types), they will be replaced."
        )

        confirm_dns = questionary.confirm(
            "Proceed with these DNS records?",
            default=True,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
        ).ask()
        if not confirm_dns:
            ui.abort("Aborted — no DNS records will be created.")

        cert_config = CertificateConfig(
            method=CertMethod.ACM,
            hosted_zone_id=hosted_zone_id,
        )

    elif use_route53 == "upload":
        cert_config = _prompt_cert_upload()

    else:  # none
        ui.newline()
        ui.warning("ALB will only have an HTTP listener (port 80)")
        proceed = questionary.confirm(
            "Proceed without HTTPS?",
            default=False,
            style=ui.PROMPT_STYLE,
            qmark=ui.QMARK,
    ).ask()
        if not proceed:
            ui.abort("Aborted — please prepare certificate files and try again.")

        cert_config = CertificateConfig(method=CertMethod.NONE)

    dns_config = DNSConfig(
        base_domain=base_domain,
        use_route53=(use_route53 == "route53"),
        hosted_zone_id=hosted_zone_id,
    )

    return dns_config, cert_config


# ---------------------------------------------------------------------------
# Certificate upload sub-flow
# ---------------------------------------------------------------------------

def _prompt_cert_upload() -> CertificateConfig:
    """Prompt for certificate file paths and read their contents."""

    ui.newline()
    ui.info("Provide PEM-encoded certificate files for ALB HTTPS termination")
    ui.newline()

    cert_path = questionary.path(
        "Certificate file (.pem):",
        validate=lambda p: (
            Path(p).expanduser().exists() or "File not found"
        ),
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if cert_path is None:
        ui.abort()

    key_path = questionary.path(
        "Private key file (.pem):",
        validate=lambda p: (
            Path(p).expanduser().exists() or "File not found"
        ),
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()
    if key_path is None:
        ui.abort()

    chain_path = questionary.text(
        "Certificate chain file (.pem) [optional, press Enter to skip]:",
        default="",
        style=ui.PROMPT_STYLE,
        qmark=ui.QMARK,
    ).ask()

    cert_body = Path(cert_path).expanduser().read_text()
    cert_key = Path(key_path).expanduser().read_text()

    cert_chain = None
    if chain_path and chain_path.strip():
        chain_p = Path(chain_path).expanduser()
        if chain_p.exists():
            cert_chain = chain_p.read_text()
        else:
            ui.warning(f"Chain file not found: {chain_path} — skipping")

    ui.success("Certificate files loaded")

    return CertificateConfig(
        method=CertMethod.UPLOAD,
        cert_body=cert_body,
        cert_private_key=cert_key,
        cert_chain=cert_chain,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_domain(value: str) -> bool:
    """Basic domain name validation."""
    v = value.strip().lower()
    if not v or "." not in v:
        return False
    parts = v.split(".")
    return all(
        part and part.replace("-", "").isalnum()
        for part in parts
    )
