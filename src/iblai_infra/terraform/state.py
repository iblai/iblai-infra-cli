"""Project state persistence — tracks infrastructure lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from iblai_infra.models import InfraConfig, ProjectState

WORKSPACE_ROOT = Path.home() / ".iblai-infra" / "projects"


def workspace_dir(config: InfraConfig) -> Path:
    """Return the workspace directory for a given configuration."""
    return WORKSPACE_ROOT / config.resource_prefix


def save_state(state: ProjectState) -> Path:
    """Save project state to disk."""
    ws = Path(state.workspace_path)
    ws.mkdir(parents=True, exist_ok=True)
    state_file = ws / "state.json"
    state.updated_at = datetime.now(timezone.utc)
    state_file.write_text(state.model_dump_json(indent=2))
    return state_file


def load_state(name: str) -> ProjectState | None:
    """Load project state by name. Searches all workspaces."""
    for ws in list_workspaces():
        state_file = ws / "state.json"
        if state_file.exists():
            state = ProjectState.model_validate_json(state_file.read_text())
            if state.name == name:
                return state
    return None


def list_workspaces() -> list[Path]:
    """List all project workspace directories."""
    if not WORKSPACE_ROOT.exists():
        return []
    return sorted(
        [d for d in WORKSPACE_ROOT.iterdir() if d.is_dir() and (d / "state.json").exists()]
    )


def list_all_states() -> list[ProjectState]:
    """Load all project states."""
    states = []
    for ws in list_workspaces():
        state_file = ws / "state.json"
        try:
            states.append(ProjectState.model_validate_json(state_file.read_text()))
        except Exception:
            continue
    return states
