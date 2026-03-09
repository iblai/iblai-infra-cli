"""CLI entry point — `iblai infra <command>` structure.

Root:  iblai --version | --help
Group: iblai infra provision | destroy | status | list
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from iblai_infra import __version__, ui
from iblai_infra.terraform.state import list_all_states, load_state

# ---------------------------------------------------------------------------
# Root app: `iblai`
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="iblai",
    help="ibl.ai CLI — Infrastructure, deployment, and platform management.",
    no_args_is_help=True,
    add_completion=False,
)


def version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"[brand]iblai[/brand] v{__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """ibl.ai CLI — Infrastructure, deployment, and platform management."""


# ---------------------------------------------------------------------------
# Subcommand group: `iblai infra`
# ---------------------------------------------------------------------------

infra_app = typer.Typer(
    name="infra",
    help="Infrastructure provisioning and management for AWS.",
    no_args_is_help=True,
)

app.add_typer(infra_app)


@infra_app.command()
def provision() -> None:
    """Launch the interactive provisioning wizard."""
    from iblai_infra.app import run_provision_wizard

    try:
        run_provision_wizard()
    except KeyboardInterrupt:
        ui.newline()
        ui.abort("Interrupted.")


@infra_app.command()
def destroy(
    name: str = typer.Argument(help="Project name to destroy"),
) -> None:
    """Destroy existing infrastructure."""
    import questionary

    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    if state.status == "destroyed":
        ui.warning(f"Infrastructure '{name}' is already destroyed.")
        raise typer.Exit(0)

    ui.banner()
    ui.warning(
        f"This will permanently destroy ALL infrastructure for: [highlight]{name}[/highlight]"
    )

    if state.outputs:
        rows = []
        for k, v in state.outputs.items():
            if isinstance(v, str) and v:
                rows.append((k, v))
        if rows:
            ui.summary_panel("Resources to Destroy", rows[:10])

    confirm = questionary.confirm(
        "Are you sure you want to destroy this infrastructure?",
        default=False,
        style=ui.PROMPT_STYLE,
    ).ask()

    if not confirm:
        ui.abort("Cancelled.")

    # Double confirm for production
    if state.config.environment.value == "prod":
        confirm2 = questionary.text(
            f'Type "{name}" to confirm production destruction:',
            style=ui.PROMPT_STYLE,
        ).ask()
        if confirm2 != name:
            ui.abort("Name did not match. Cancelled.")

    from iblai_infra.terraform.runner import TerraformRunner

    runner = TerraformRunner(state.config)
    runner.ws = Path(state.workspace_path)
    runner.state = state
    runner.destroy()

    ui.newline()


@infra_app.command()
def status(
    name: str = typer.Argument(help="Project name"),
) -> None:
    """Show infrastructure status and workspace details for a project."""
    state = load_state(name)
    if state is None:
        ui.error(f"No infrastructure found with name: {name}")
        raise typer.Exit(1)

    status_colors = {
        "created": "#3ECF6E",
        "initialized": "#F0A830",
        "failed": "#E85454",
        "destroyed": "dim",
    }
    sc = status_colors.get(state.status, "white")

    rows = [
        ("", "[bold]General[/bold]"),
        ("Name", state.name),
        ("Provider", state.provider.upper()),
        ("Status", f"[{sc}]{state.status.upper()}[/{sc}]"),
        ("Environment", state.config.environment.value.capitalize()),
        ("Region", state.config.credentials.region),
        ("Domain", state.config.dns.base_domain),
        ("Created", state.created_at.strftime("%Y-%m-%d %H:%M UTC")),
        ("Updated", state.updated_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]

    # Workspace info
    ws = Path(state.workspace_path)
    rows.append(("", ""))
    rows.append(("", "[bold]Workspace[/bold]"))
    rows.append(("Directory", str(ws)))

    if ws.exists():
        files = sorted(f for f in ws.iterdir() if f.is_file())
        file_names = ", ".join(f.name for f in files[:8])
        if len(files) > 8:
            file_names += f" (+{len(files) - 8} more)"
        rows.append(("Files", file_names))
    else:
        rows.append(("Files", "[dim]Directory not found[/dim]"))

    # SSH key
    if state.config.ssh.private_key_path:
        rows.append(("SSH key", str(state.config.ssh.private_key_path)))

    # Outputs
    if state.outputs:
        rows.append(("", ""))
        rows.append(("", "[bold]Outputs[/bold]"))
        for k, v in state.outputs.items():
            if isinstance(v, str) and v:
                label = k.replace("_", " ").capitalize()
                rows.append((label, v))

    ui.summary_panel(f"Infrastructure: {state.name}", rows)


@infra_app.command(name="list")
def list_cmd() -> None:
    """List all provisioned environments."""
    states = list_all_states()

    if not states:
        ui.newline()
        ui.info("No managed infrastructure found.")
        ui.muted("Run [bold]iblai infra provision[/bold] to create your first environment.")
        ui.newline()
        return

    ui.newline()

    table = Table(
        title=f"[bold {ui.IBL_BLUE}]Managed Environments[/]",
        border_style=ui.IBL_NAVY,
        header_style=f"bold {ui.IBL_BLUE_LIGHT}",
        padding=(0, 1),
    )
    table.add_column("Name", style="bold white", min_width=16)
    table.add_column("Environment", min_width=12)
    table.add_column("Region", min_width=14)
    table.add_column("Domain", min_width=16)
    table.add_column("Status", min_width=12, justify="center")
    table.add_column("Workspace", style="dim", min_width=20)
    table.add_column("Created", min_width=10)

    status_colors = {
        "created": "#3ECF6E",
        "initialized": "#F0A830",
        "failed": "#E85454",
        "destroyed": "dim",
    }

    for s in states:
        sc = status_colors.get(s.status, "white")

        ws_path = s.workspace_path
        home = str(Path.home())
        if ws_path.startswith(home):
            ws_path = "~" + ws_path[len(home):]

        table.add_row(
            s.name,
            s.config.environment.value.capitalize(),
            s.config.credentials.region,
            s.config.dns.base_domain,
            f"[{sc}]{s.status}[/{sc}]",
            ws_path,
            s.created_at.strftime("%Y-%m-%d"),
        )

    ui.console.print(table)
    ui.newline()
    ui.muted(
        f"  {len(states)} environment(s) found."
        " Use [bold]iblai infra status <name>[/bold] for details."
    )
    ui.newline()
