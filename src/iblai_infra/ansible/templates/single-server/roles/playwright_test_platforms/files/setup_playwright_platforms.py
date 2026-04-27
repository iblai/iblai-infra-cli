#!/usr/bin/env python
"""
Idempotent setup of Playwright test platforms + UserPlatformLinks in DM.

Creates 8 spa-tests-* platforms (4 primary + 4 secondary) via run_launch_steps,
then upserts 12 UserPlatformLinks wiring 4 browser superusers + 1 student
user (already created in LMS and synced to DM by the surrounding ansible
tasks) to those platforms.

Run inside the DM container after `ibl edx sync-with-manager --users`:

    docker exec -w /code/dl_manager ibl_dm_pro_web \
        python utilities/setup_playwright_platforms.py --env-prefix stg1

Idempotency:
- Platforms: skipped if `Platform.objects.filter(key=...).exists()` already.
  `run_launch_steps` is NOT idempotent on its own (re-running creates dupes).
- UPLs: `update_or_create` against (user_id, platform_id) — safe to re-run.

Only generated secrets are platform-admin passwords (one per fresh platform
launch). They go to stdout; the surrounding ansible task wraps that output
in a debug print so the operator sees them once. Browser/student passwords
are NOT generated here — those are set by the LMS-side ansible task using
the project-wide test password convention.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys

import django

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_django_settings_env = os.getenv("DJANGO_SETTINGS_ENV", "")
_default_settings = (
    f"dl_manager.settings.{_django_settings_env}"
    if _django_settings_env
    else "dl_manager.settings.base"
)
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.getenv("DJANGO_SETTINGS_MODULE", _default_settings),
)
django.setup()

from core.models import Platform, UserPlatformLink  # noqa: E402
from core.models.users import User  # noqa: E402
from dl_iblai_services_app.services.launchers import run_launch_steps  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


BROWSERS = ("chrome", "edge", "firefox", "safari")
SUFFIXES = ("", "-secondary")  # "" = primary, "-secondary" = secondary


def platform_configs():
    """Yield 8 platform configs: 4 browsers x (primary, secondary)."""
    for browser in BROWSERS:
        for suffix in SUFFIXES:
            key = f"spa-tests-{browser}{suffix}"
            admin_username = f"spatests{browser}{suffix.replace('-', '')}"
            yield {
                "key": key,
                "org": key,
                "name": key,
                "admin_username": admin_username,
                "admin_email": f"{admin_username}@ibleducation.local",
                "admin_firstname": f"SPA-{browser.capitalize()}",
                "admin_lastname": "AdminSecondary" if suffix else "Admin",
            }


def ensure_platform(pcfg):
    """Idempotent platform launch. Returns (admin_password|None, was_launched)."""
    if Platform.objects.filter(key=pcfg["key"]).exists():
        log.info("Platform %s already exists — skipping launch", pcfg["key"])
        return None, False
    password = secrets.token_hex(16)
    launch_data = {
        "username": pcfg["admin_username"],
        "email": pcfg["admin_email"],
        "firstname": pcfg["admin_firstname"],
        "lastname": pcfg["admin_lastname"],
        "password": password,
        "role": "org-instructor",
        "org": pcfg["org"],
        "key": pcfg["key"],
        "name": pcfg["name"],
        "lms_url": f"https://{pcfg['key']}.lms.iblai.app",
        "cms_url": f"https://{pcfg['key']}.cms.iblai.app",
        "portal_url": f"https://{pcfg['key']}.portal.iblai.app",
    }
    log.info("Launching platform %s (admin=%s)", pcfg["key"], pcfg["admin_username"])
    resp = run_launch_steps(launch_data)
    if not resp.get("success"):
        log.error(
            "Launch failed for %s: %s", pcfg["key"], resp.get("traceback")
        )
        raise RuntimeError(
            f"run_launch_steps failed for {pcfg['key']}: {resp.get('message')}"
        )
    return password, True


def ensure_upl(username, platform_key, *, is_admin, is_staff):
    """Idempotent UserPlatformLink upsert. Returns "created"/"updated"/"skipped"."""
    try:
        u = User.objects.get(username=username)
    except User.DoesNotExist:
        log.warning(
            "Cannot UPL: DM user %s missing (sync-with-manager didn't propagate?)",
            username,
        )
        return "skipped"
    try:
        p = Platform.objects.get(key=platform_key)
    except Platform.DoesNotExist:
        log.warning("Cannot UPL: platform %s missing", platform_key)
        return "skipped"
    _link, created = UserPlatformLink.objects.update_or_create(
        user_id=u.pk,
        platform_id=p.pk,
        defaults={"is_admin": is_admin, "is_staff": is_staff, "active": True},
    )
    return "created" if created else "updated"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-prefix",
        default="test",
        help="prefix for the student user (e.g. stg1 → stg1user_ibleducation_com)",
    )
    args = parser.parse_args()

    summary = {
        "platforms_created": [],
        "platforms_existing": [],
        "platform_admin_passwords": {},  # only fresh launches
        "upls": {"created": [], "updated": [], "skipped": []},
    }

    # 1. Platforms (idempotent)
    for pcfg in platform_configs():
        password, launched = ensure_platform(pcfg)
        if launched:
            summary["platforms_created"].append(pcfg["key"])
            summary["platform_admin_passwords"][pcfg["admin_username"]] = password
        else:
            summary["platforms_existing"].append(pcfg["key"])

    # 2. Browser-superuser UPLs: each iblaiuser<browser>new → its 2 platforms
    #    with is_admin=True, is_staff=True (matches stg1 canonical state)
    for browser in BROWSERS:
        username = f"iblaiuser{browser}new"
        for suffix in SUFFIXES:
            pkey = f"spa-tests-{browser}{suffix}"
            outcome = ensure_upl(username, pkey, is_admin=True, is_staff=True)
            summary["upls"][outcome].append(f"{username}@{pkey}")

    # 3. Student-user UPLs: <env>user_ibleducation_com → 4 primary platforms
    #    (no secondary, no admin/staff — student-level access only)
    student_username = f"{args.env_prefix}user_ibleducation_com"
    for browser in BROWSERS:
        pkey = f"spa-tests-{browser}"
        outcome = ensure_upl(
            student_username, pkey, is_admin=False, is_staff=False
        )
        summary["upls"][outcome].append(f"{student_username}@{pkey}")

    print("=" * 72)
    print("PLAYWRIGHT TEST PLATFORM SETUP — SAVE THIS OUTPUT")
    print("(platform-admin passwords are only shown once)")
    print("=" * 72)
    print(json.dumps(summary, indent=2, default=str))
    print("=" * 72)


if __name__ == "__main__":
    main()
