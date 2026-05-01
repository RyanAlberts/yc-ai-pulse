"""Typer CLI. Phase 1 PR #1 ships ``run-coverage`` — fetch + classify + dashboard,
no LLM calls yet. The full ``run`` command (with classifier + researcher) lands
in subsequent PRs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ycai import __version__
from ycai.coverage import compute_coverage, coverage_summary_lines
from ycai.dashboard import render as render_dashboard
from ycai.schemas import RawCompany
from ycai.scraper import UpstreamError, fetch_batch, upstream_age_hours
from ycai.verifier import check_urls, split_by_status

app = typer.Typer(add_completion=False, help="yc-ai-pulse — open-source YC batch analyzer.")
console = Console()


@app.command()
def version() -> None:
    """Print the installed package version."""
    console.print(f"yc-ai-pulse {__version__}")


@app.command("run-coverage")
def run_coverage(
    batch: str | None = typer.Option(None, help="Batch slug e.g. 'winter-2026'. Default: autodetect latest."),
    yc_official_count: int | None = typer.Option(
        None,
        "--yc-official-count",
        help="If known, the actual YC batch size (e.g. 196 for W26 from Demo Day). "
        "Used as the denominator for the headline coverage % — recommended for "
        "credibility when upstream is stale.",
    ),
    output_dir: Path = typer.Option(Path("runs"), help="Where to write the timestamped run directory."),
    skip_link_check: bool = typer.Option(
        False, help="Skip HEAD/GET verification of company websites (faster but Tier A drops to 'unknown')."
    ),
    verbose: bool = typer.Option(False, "-v", help="Verbose logging."),
) -> None:
    """Phase 1 quality probe: fetch the batch, classify by tier, write a coverage dashboard.

    No LLM calls are made — this surfaces upstream and per-company data
    quality issues before any analysis cost is incurred.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    run_dir = output_dir / timestamp
    cache_dir = run_dir / "raw"

    console.print("[cyan]→[/cyan] fetching batch from yc-oss/api…")
    try:
        meta, companies = fetch_batch(slug=batch, cache_dir=cache_dir)
    except UpstreamError as exc:
        console.print(f"[red]✗ upstream error:[/red] {exc}")
        console.print(
            "[red]yc-oss/api is the only sanctioned source for batch listings (ADR 0001).[/red]\n"
            "[red]This is a hard fail — no fallback to scraping ycombinator.com is permitted.[/red]"
        )
        raise typer.Exit(2) from exc

    age = upstream_age_hours(meta.source_last_updated)
    if age is not None and age > 48:
        console.print(f"[yellow]⚠ upstream is stale: yc-oss/api last updated {age}h ago[/yellow]")

    console.print(
        f"[green]✓[/green] fetched {len(companies)} companies for {meta.label} "
        f"(upstream count = {meta.upstream_count})"
    )

    website_statuses: dict[str, str] = {}
    if not skip_link_check:
        urls = [c.website for c in companies if c.website]
        slug_for_url: dict[str, str] = {}
        for c in companies:
            if c.website:
                slug_for_url[c.website] = c.slug

        console.print(f"[cyan]→[/cyan] link-verifying {len(urls)} websites…")
        statuses = check_urls(urls)
        for url, (status, _reason) in statuses.items():
            slug = slug_for_url.get(url)
            if slug:
                website_statuses[slug] = status

        buckets = split_by_status(statuses)
        table = Table(title="Website verification")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for status_label, items in buckets.items():
            table.add_row(status_label, str(len(items)))
        console.print(table)
    else:
        console.print("[yellow]⚠ skipping link check (--skip-link-check)[/yellow]")

    coverage = compute_coverage(
        companies=companies,
        batch_slug=meta.slug,
        batch_label=meta.label,
        upstream_count=meta.upstream_count,
        yc_official_count=yc_official_count,
        website_statuses=website_statuses,
        source="yc-oss/api",
        source_last_updated=meta.source_last_updated,
    )

    for line in coverage_summary_lines(coverage):
        console.print(line)

    coverage_path = run_dir / "coverage.json"
    coverage_path.write_text(coverage.model_dump_json(indent=2))
    console.print(f"[green]✓[/green] wrote coverage.json → {coverage_path}")

    dashboard_path = render_dashboard(
        coverage=coverage,
        companies=companies,
        output_path=run_dir / "dashboard.html",
    )
    console.print(f"[green]✓[/green] wrote dashboard.html → {dashboard_path}")

    csv_path = run_dir / "companies.csv"
    _write_csv(companies, csv_path)
    console.print(f"[green]✓[/green] wrote companies.csv → {csv_path}")

    console.print()
    console.print(
        f"[bold]headline:[/bold] "
        f"{coverage.coverage_pct_of_official or coverage.coverage_pct_of_upstream}% "
        f"of {meta.label} analyzed "
        f"({coverage.analyzable_count} of "
        f"{coverage.yc_official_count or coverage.upstream_company_count} companies)"
    )


def _write_csv(companies: list[RawCompany], path: Path) -> None:
    """Tiny CSV writer that doesn't pull in pandas just for serialization."""
    import csv

    fields = [
        "slug",
        "name",
        "batch",
        "website",
        "one_liner",
        "long_description",
        "industry",
        "industries",
        "subindustry",
        "tags",
        "regions",
        "team_size",
        "status",
        "stage",
        "top_company",
        "url",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for c in companies:
            row = c.model_dump()
            row["industries"] = ";".join(row.get("industries") or [])
            row["tags"] = ";".join(row.get("tags") or [])
            row["regions"] = ";".join(row.get("regions") or [])
            writer.writerow({k: row.get(k, "") for k in fields})


def _entrypoint() -> None:
    """Console-script entrypoint registered in pyproject.toml."""
    app()


if __name__ == "__main__":
    _entrypoint()
