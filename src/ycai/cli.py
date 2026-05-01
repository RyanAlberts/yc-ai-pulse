"""Typer CLI. Phase 1 PR #1 ships ``run-coverage`` — fetch + classify + dashboard.
PR #2 adds ``--enrich`` for LLM-based AI/stack/OSS classification.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from ycai import __version__
from ycai.coverage import compute_coverage, coverage_summary_lines
from ycai.dashboard import (
    collect_cited_urls,
    write_broken_links_report,
)
from ycai.dashboard import (
    render as render_dashboard,
)
from ycai.researcher import (
    DEFAULT_MODEL,
    Backend,
    analyze,
    make_default_backend,
)
from ycai.schemas import (
    CompanyAnalysis,
    CoverageTier,
    RawCompany,
)
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
    enrich: bool = typer.Option(
        False,
        "--enrich",
        help="Run LLM-based classification (industry / capability / stack / OSS) on Tier A+B "
        "companies. Costs subscription quota or API tokens depending on backend.",
    ),
    enrich_limit: int = typer.Option(
        0,
        "--enrich-limit",
        help="Cap the number of companies sent through enrichment. 0 = no cap. Useful for smoke runs.",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="Anthropic API key. Falls back to ANTHROPIC_API_KEY env var. "
        "If unset, uses claude-agent-sdk against your subscription. "
        "Never logged, never written to disk.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Sonnet model to use during --enrich."),
    allow_dead_links: bool = typer.Option(
        False,
        "--allow-dead-links",
        help="Render the dashboard even if cited URLs returned 4xx/5xx. "
        "Writes BROKEN_LINKS.md alongside; banner appears on the dashboard. "
        "Default: refuse to write the dashboard so reports never ship dead citations.",
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

    analyses: list[CompanyAnalysis] | None = None
    broken_link_count = 0
    if enrich:
        analyzable_slugs = {r.slug for r in coverage.records if r.tier in (CoverageTier.A, CoverageTier.B)}
        keepers = [c for c in companies if c.slug in analyzable_slugs]
        if enrich_limit > 0:
            keepers = keepers[:enrich_limit]
            console.print(f"[yellow]⚠ enrichment capped at {enrich_limit} companies[/yellow]")
        try:
            backend = make_default_backend(api_key=api_key)
        except RuntimeError as exc:
            console.print(f"[red]✗ no LLM backend available:[/red] {exc}")
            raise typer.Exit(3) from exc
        backend_name = backend.__class__.__name__
        console.print(f"[cyan]→[/cyan] enriching {len(keepers)} companies with {backend_name} (model={model})…")
        analyses = asyncio.run(_run_enrichment(keepers, backend, model=model))
        analyses_path = run_dir / "analyses.json"
        analyses_path.write_text(json.dumps([a.model_dump(mode="json") for a in analyses], indent=2))
        console.print(f"[green]✓[/green] wrote analyses.json → {analyses_path}")
        _print_enrichment_summary(analyses)

        # Publish gate: re-verify every cited URL.
        cited = collect_cited_urls(analyses)
        if cited:
            console.print(f"[cyan]→[/cyan] verifying {len(cited)} cited URL(s) before publish…")
            cite_statuses = check_urls(cited)
            broken: dict[str, tuple[str, str]] = {
                url: (status, reason) for url, (status, reason) in cite_statuses.items() if status == "dead"
            }
            broken_link_count = len(broken)
            if broken:
                console.print(f"[red]✗ {broken_link_count} cited URL(s) returned 4xx/5xx[/red]")
                report_path = write_broken_links_report(run_dir, broken, analyses)
                console.print(f"[red]  details: {report_path}[/red]")
                if not allow_dead_links:
                    console.print(
                        "[red]  refusing to write dashboard. Re-run with --allow-dead-links "
                        "to override (the dashboard will carry a loud banner).[/red]"
                    )
                    raise typer.Exit(4)
                console.print("[yellow]  --allow-dead-links set: writing dashboard with warning banner[/yellow]")
            else:
                console.print("[green]✓[/green] every cited URL resolved cleanly")

    dashboard_path = render_dashboard(
        coverage=coverage,
        companies=companies,
        output_path=run_dir / "dashboard.html",
        analyses=analyses,
        broken_link_count=broken_link_count,
        allowed_dead_links=allow_dead_links,
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


async def _run_enrichment(
    companies: list[RawCompany],
    backend: Backend,
    *,
    model: str,
) -> list[CompanyAnalysis]:
    """Drive the enrichment pipeline with a Rich progress bar."""
    semaphore = asyncio.Semaphore(8)  # respect subscription rate limits
    results: list[CompanyAnalysis] = []

    async def one(company: RawCompany) -> CompanyAnalysis:
        async with semaphore:
            analysis, _cross = await analyze(company, backend, model=model)
            return analysis

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("enriching", total=len(companies))
        coros = [one(c) for c in companies]
        for coro in asyncio.as_completed(coros):
            analysis = await coro
            results.append(analysis)
            progress.advance(task)

    return results


def _print_enrichment_summary(analyses: list[CompanyAnalysis]) -> None:
    from collections import Counter

    by_conf: Counter[str] = Counter(a.confidence for a in analyses)
    by_industry: Counter[str] = Counter(a.industry_primary.value for a in analyses)
    by_capability: Counter[str] = Counter(cap.value for a in analyses for cap in a.ai_capability)
    by_stack: Counter[str] = Counter(stack.value for a in analyses for stack in a.tech_stack)
    by_oss: Counter[str] = Counter(a.oss_posture.value for a in analyses)

    table = Table(title="Enrichment summary")
    table.add_column("Field", style="bold")
    table.add_column("Top 5", justify="left")
    table.add_row("confidence", _fmt_counter(by_conf))
    table.add_row("industry", _fmt_counter(by_industry, top=5))
    table.add_row("ai_capability", _fmt_counter(by_capability, top=5))
    table.add_row("tech_stack", _fmt_counter(by_stack, top=5))
    table.add_row("oss_posture", _fmt_counter(by_oss))
    console.print(table)


def _fmt_counter(counter: object, top: int = 6) -> str:
    from collections import Counter as _Counter

    assert isinstance(counter, _Counter)
    return ", ".join(f"{name} ({count})" for name, count in counter.most_common(top))


def _entrypoint() -> None:
    """Console-script entrypoint registered in pyproject.toml."""
    app()


if __name__ == "__main__":
    _entrypoint()
