"""Post-provision runtime IAM helper.

Two distinct AWS credential sets are needed on the running platform:

  1. **S3 access** to the dm-media, dm-static, and backups buckets
     Terraform just created in the **operator's own AWS account**. The
     operator mints these themselves by attaching the policy this module
     generates to a scoped IAM user — one-time, post-provision.
  2. **ECR pulls** against IBL's image registry. These credentials are
     **provided separately by IBL** (out-of-band) and are NOT in scope
     for this module. The post-provision instructions intentionally do
     not mention them — operators should follow IBL's hand-off
     procedure for the ECR keys.

The policy here is therefore **S3-only**: scoped to the literal bucket
ARNs Terraform created, with the verbs the platform actually uses (no
`s3:*`, no bucket-policy mutation, no lifecycle config — Terraform
configured those at provision time and the platform never revisits).

The JSON is also written to the project workspace
(`<workspace>/runtime-iam-policy.json`) so the operator can pipe it
directly into the CLI:

    aws iam put-user-policy \\
        --user-name <name>-s3-runtime \\
        --policy-name iblai-s3-runtime \\
        --policy-document file://<workspace>/runtime-iam-policy.json
"""

from __future__ import annotations

import json
from pathlib import Path

from iblai_infra import ui
from iblai_infra.models import DeploymentType, InfraConfig

# Tight S3 verbs the platform actually uses at runtime. Notably excludes
# bucket-policy / ACL mutations, lifecycle config, encryption config — all
# of which Terraform set up at provision time and the platform never
# revisits.
_S3_OBJECT_ACTIONS = [
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:GetObjectAcl",
    "s3:PutObjectAcl",
]
_S3_BUCKET_ACTIONS = [
    "s3:ListBucket",
    "s3:GetBucketLocation",
]

POLICY_FILENAME = "runtime-iam-policy.json"


def build_runtime_iam_policy(bucket_names: list[str]) -> dict:
    """Build the **S3-only** IAM policy JSON document for the runtime user.

    `bucket_names` must be the literal S3 bucket names Terraform created
    (the values of `s3_bucket_*` outputs). Returns a dict ready to
    `json.dumps()` — no formatting opinions baked in here.

    ECR access is intentionally not included: the IBL provider hands off
    those credentials separately (see module docstring).
    """
    if not bucket_names:
        raise ValueError("at least one S3 bucket name is required")

    bucket_arns = [f"arn:aws:s3:::{b}" for b in bucket_names]
    object_arns = [f"arn:aws:s3:::{b}/*" for b in bucket_names]

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PlatformBucketObjects",
                "Effect": "Allow",
                "Action": _S3_OBJECT_ACTIONS,
                "Resource": object_arns,
            },
            {
                "Sid": "PlatformBucketList",
                "Effect": "Allow",
                "Action": _S3_BUCKET_ACTIONS,
                "Resource": bucket_arns,
            },
        ],
    }


def extract_bucket_names(outputs: dict) -> list[str]:
    """Pull bucket names out of a terraform outputs dict.

    Reads the three `s3_bucket_{backups,media,static}` outputs that the
    single-server template emits. Returns an empty list when none are
    present (e.g. call-server, which has no buckets).
    """
    keys = ("s3_bucket_backups", "s3_bucket_media", "s3_bucket_static")
    return [outputs[k] for k in keys if outputs.get(k)]


def render_runtime_access_instructions(
    config: InfraConfig,
    outputs: dict,
    ws: Path,
) -> None:
    """Print post-provision IAM-user setup instructions to the operator.

    Skips silently for `DeploymentType.CALL` (no S3 buckets and the call
    stack uses its own credentials flow). Writes the policy JSON to the
    workspace at `runtime-iam-policy.json` so the operator can pipe it
    into `aws iam put-user-policy --policy-document file://...`.
    """
    if config.deployment_type == DeploymentType.CALL:
        return

    bucket_names = extract_bucket_names(outputs)
    if not bucket_names:
        # No buckets in outputs — terraform template might not have run S3
        # creation, or the operator pointed at a deployment shape we don't
        # cover. Surface a soft note instead of printing a half-policy.
        ui.muted(
            "Skipping runtime IAM instructions: no S3 buckets in terraform "
            "outputs."
        )
        return

    policy = build_runtime_iam_policy(bucket_names)
    policy_path = ws / POLICY_FILENAME
    policy_path.write_text(json.dumps(policy, indent=2) + "\n")

    user_name = f"{config.project_name}-{config.environment.value}-s3-runtime"

    ui.newline()
    ui.console.rule("[bold yellow]Next: create the S3 IAM user[/]")
    ui.console.print(
        "The platform server reads / writes the three S3 buckets Terraform\n"
        "just created in [bold]your own AWS account[/bold]. Create a scoped IAM user\n"
        "with the policy below and paste its access key into [highlight].env.setup[/highlight].\n"
    )
    ui.console.print(
        "  [muted]The policy has already been saved to:[/muted]\n"
        f"  [highlight]{policy_path}[/highlight]\n"
    )

    # Show the policy verbatim so the operator can sanity-check before
    # creating anything. Indented blob renders monospace via the IBL theme.
    ui.console.rule("[muted]runtime-iam-policy.json[/muted]")
    ui.console.print(json.dumps(policy, indent=2))
    ui.console.rule()
    ui.newline()

    ui.console.print("  [bold]One-time setup — copy/paste into your shell:[/]\n")
    ui.console.print(
        f"  [highlight]aws iam create-user --user-name {user_name}[/highlight]\n"
        f"  [highlight]aws iam put-user-policy \\\n"
        f"      --user-name {user_name} \\\n"
        f"      --policy-name iblai-s3-runtime \\\n"
        f"      --policy-document file://{policy_path}[/highlight]\n"
        f"  [highlight]aws iam create-access-key --user-name {user_name}[/highlight]\n"
    )
    ui.console.print(
        "  Copy the [bold]AccessKeyId[/bold] + [bold]SecretAccessKey[/bold] from the last command into your\n"
        "  [highlight].env.setup[/highlight] as [highlight]AWS_ACCESS_KEY_ID[/highlight] and [highlight]AWS_SECRET_ACCESS_KEY[/highlight], then run:\n"
    )
    ui.console.print(
        f"  [brand]iblai infra setup-env {config.project_name} -f .env.setup[/brand]\n"
    )
    ui.muted(
        "  For ECR images, use AWS credentials provided by ibl.ai — "
        "or contact us at https://ibl.ai/contact"
    )
    ui.newline()
