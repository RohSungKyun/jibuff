from typing import Annotated

import typer

app = typer.Typer(
    name="jibuff",
    help="Absorb the jitter in your requirements. Spec first, code second, verify always.",
    no_args_is_help=True,
)


@app.command()
def run(
    request: Annotated[str, typer.Argument(help="What you want to build")],
    mode: Annotated[str, typer.Option(help="Workflow mode: quick | rtc")] = "quick",
) -> None:
    """Run the jibuff workflow for a given request."""
    typer.echo(f"[jibuff] mode={mode} | {request}")
    typer.echo("(not yet implemented — Phase 2)")


@app.command()
def interview(
    request: Annotated[str, typer.Argument(help="Your initial idea or feature request")],
    mode: Annotated[str, typer.Option(help="Workflow mode: quick | rtc")] = "quick",
) -> None:
    """Start an interview session to clarify requirements."""
    typer.echo(f"[jibuff interview] mode={mode} | {request}")
    typer.echo("(not yet implemented — Phase 1)")


@app.command()
def status() -> None:
    """Show current loop state."""
    typer.echo("[jibuff status]")
    typer.echo("(not yet implemented — Phase 2)")
