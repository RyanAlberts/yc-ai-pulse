"""Typer CLI. Phase 1 PR #1 ships ``run-coverage`` — fetch + classify + dashboard.
PR #2 adds ``--enrich`` for LLM-based AI/stack/OSS classification.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from ycai import __version__
from ycai.coverage import compute_coverage, coverage_summary_lines
from ycai.crawler import CrawlResult, crawl_companies
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
    BatchCoverage,
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


daemon_app = typer.Typer(
    name="daemon",
    help="Start / stop / inspect the local FastAPI daemon (Phase 3 backend).",
    no_args_is_help=True,
)
app.add_typer(daemon_app)


@daemon_app.command("start")
def daemon_start(
    host: str = typer.Option("127.0.0.1", help="Bind address (always 127.0.0.1 unless overridden)."),
    port: int = typer.Option(8787, help="Bind port."),
) -> None:
    """Start the local FastAPI daemon (detached). Prints the bearer token at the end."""
    from ycai import daemon as daemon_mod

    console.print(f"[cyan]→[/cyan] starting daemon on http://{host}:{port}…")
    state = daemon_mod.start(host=host, port=port)
    if not state.running:
        console.print("[red]✗ daemon failed to come up; check ~/.ycai/daemon.log[/red]")
        raise typer.Exit(2)
    console.print(f"[green]✓[/green] daemon running (pid={state.pid})")
    console.print()
    console.print(f"[bold]token:[/bold] [yellow]{daemon_mod.token()}[/yellow]")
    console.print(
        "[dim]Paste this token into the Chrome extension's setup screen "
        "(it's stored at ~/.ycai/token; `chmod 600`).[/dim]"
    )


@daemon_app.command("stop")
def daemon_stop(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8787, help="Bind port."),
) -> None:
    """Stop the running daemon. Best-effort SIGTERM then SIGKILL."""
    from ycai import daemon as daemon_mod

    console.print("[cyan]→[/cyan] stopping daemon…")
    state = daemon_mod.stop(host=host, port=port)
    if state.running:
        console.print("[red]✗ daemon still running after stop attempt[/red]")
        raise typer.Exit(2)
    console.print("[green]✓[/green] daemon stopped")


@daemon_app.command("status")
def daemon_status(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8787, help="Bind port."),
) -> None:
    """Print daemon health: process state, /healthz response, token presence."""
    from ycai import daemon as daemon_mod

    state = daemon_mod.status(host=host, port=port)
    table = Table(title="ycai daemon status", show_header=False)
    table.add_column("field")
    table.add_column("value")
    table.add_row("running", "yes" if state.running else "no")
    table.add_row("pid", str(state.pid or "—"))
    table.add_row("token present", "yes" if state.token_present else "no")
    if state.health is not None:
        table.add_row("healthz status", str(state.health.get("status", "—")))
        table.add_row("daemon version", str(state.health.get("version", "—")))
    else:
        table.add_row("healthz", "[red]unreachable[/red]")
    console.print(table)
    if not state.running:
        raise typer.Exit(1)


@daemon_app.command("token")
def daemon_token() -> None:
    """Print the bearer token to stdout (suitable for piping into pbcopy)."""
    from ycai import daemon as daemon_mod

    print(daemon_mod.token())


@app.command("dashboard")
def dashboard_cmd(
    run_dir: Path = typer.Argument(..., help="Path to a previously-generated run directory."),
    allow_dead_links: bool = typer.Option(False, "--allow-dead-links", help="Skip the cited-URL re-verification step."),
) -> None:
    """Re-render the dashboard from existing artifacts. No LLM cost.

    Useful when:
      - You changed dashboard.py and want to regenerate the HTML without re-paying for the LLM.
      - You want to inspect an older run with the latest dashboard chart layout.
      - You ran out of subscription quota mid-run and need to render what you have.
    """
    coverage_path = run_dir / "coverage.json"
    if not coverage_path.exists():
        console.print(f"[red]✗ {coverage_path} not found[/red]")
        raise typer.Exit(2)
    coverage = BatchCoverage.model_validate_json(coverage_path.read_text())

    raw_path = run_dir / "raw" / "yc_companies.json"
    if not raw_path.exists():
        console.print(f"[red]✗ {raw_path} not found[/red]")
        raise typer.Exit(2)
    raw_companies = json.loads(raw_path.read_text())
    companies = [RawCompany.model_validate(c) for c in raw_companies]

    analyses: list[CompanyAnalysis] | None = None
    analyses_jsonl = run_dir / "analyses.jsonl"
    analyses_json = run_dir / "analyses.json"
    if analyses_jsonl.exists():
        analyses = [
            CompanyAnalysis.model_validate_json(line)
            for line in analyses_jsonl.read_text().splitlines()
            if line.strip()
        ]
    elif analyses_json.exists():
        analyses = [CompanyAnalysis.model_validate(a) for a in json.loads(analyses_json.read_text())]

    broken_link_count = 0
    if analyses and not allow_dead_links:
        cited = collect_cited_urls(analyses)
        if cited:
            console.print(f"[cyan]→[/cyan] re-verifying {len(cited)} cited URL(s)…")
            statuses = check_urls(cited)
            broken: dict[str, tuple[str, str]] = {
                url: (status, reason) for url, (status, reason) in statuses.items() if status == "dead"
            }
            broken_link_count = len(broken)
            if broken:
                report = write_broken_links_report(run_dir, broken, analyses)
                console.print(f"[red]✗ {broken_link_count} dead link(s); details: {report}[/red]")
                console.print("[red]  pass --allow-dead-links to render anyway with a warning banner[/red]")
                raise typer.Exit(4)

    out = render_dashboard(
        coverage=coverage,
        companies=companies,
        output_path=run_dir / "dashboard.html",
        analyses=analyses,
        broken_link_count=broken_link_count,
        allowed_dead_links=allow_dead_links and broken_link_count > 0,
    )
    console.print(f"[green]✓[/green] wrote dashboard.html → {out}")


@app.command("report")
def report_cmd(
    run_dir: Path = typer.Argument(..., help="Run directory with coverage.json + analyses.json(l)."),
    deck_only: bool = typer.Option(False, "--deck-only", help="Skip the .docx memo."),
    memo_only: bool = typer.Option(False, "--memo-only", help="Skip the .pptx deck."),
) -> None:
    """Generate the .pptx deck (and .docx memo when shipped) from existing artifacts.

    Anti-hallucination Layer 2 runs before any file is written:
    forbidden-phrase scan + numerical-drift check. Any violation aborts the
    build with the offending span so you can fix the prose.
    """
    coverage_path = run_dir / "coverage.json"
    raw_path = run_dir / "raw" / "yc_companies.json"
    if not coverage_path.exists() or not raw_path.exists():
        console.print(f"[red]✗ {run_dir} doesn't look like a valid run directory[/red]")
        raise typer.Exit(2)

    coverage = BatchCoverage.model_validate_json(coverage_path.read_text())
    companies = [RawCompany.model_validate(c) for c in json.loads(raw_path.read_text())]

    analyses_jsonl = run_dir / "analyses.jsonl"
    analyses_json = run_dir / "analyses.json"
    if analyses_jsonl.exists():
        analyses = [
            CompanyAnalysis.model_validate_json(line)
            for line in analyses_jsonl.read_text().splitlines()
            if line.strip()
        ]
    elif analyses_json.exists():
        analyses = [CompanyAnalysis.model_validate(a) for a in json.loads(analyses_json.read_text())]
    else:
        console.print(f"[red]✗ no analyses found in {run_dir}. Run with --enrich first.[/red]")
        raise typer.Exit(2)

    from ycai.reports.docx import build_memo
    from ycai.reports.ppt import Layer2Failure, build_deck

    if not memo_only:
        deck_path = run_dir / "deck.pptx"
        console.print("[cyan]→[/cyan] building deck.pptx (Layer 2 audit before write)…")
        try:
            build_deck(coverage, companies, analyses, output_path=deck_path)
        except Layer2Failure as exc:
            console.print(f"[red]✗ deck Layer 2 audit failed:[/red] {exc}")
            for hit in exc.forbidden[:5]:
                console.print(f"  [red]forbidden phrase '{hit.phrase}':[/red] {hit.excerpt}")
            for drift in exc.drifts[:5]:
                console.print(f"  [red]numerical drift '{drift.number}':[/red] {drift.excerpt}")
            raise typer.Exit(5) from exc
        console.print(f"[green]✓[/green] wrote {deck_path}")

    if not deck_only:
        memo_path = run_dir / "report.docx"
        console.print("[cyan]→[/cyan] building report.docx (Layer 2 audit before write)…")
        try:
            build_memo(coverage, companies, analyses, output_path=memo_path)
        except Layer2Failure as exc:
            console.print(f"[red]✗ memo Layer 2 audit failed:[/red] {exc}")
            for hit in exc.forbidden[:5]:
                console.print(f"  [red]forbidden phrase '{hit.phrase}':[/red] {hit.excerpt}")
            for drift in exc.drifts[:5]:
                console.print(f"  [red]numerical drift '{drift.number}':[/red] {drift.excerpt}")
            raise typer.Exit(5) from exc
        console.print(f"[green]✓[/green] wrote {memo_path}")


@app.command("resume")
def resume_cmd(
    run_dir: Path = typer.Argument(..., help="Run directory from a previous (partial) enrichment."),
    api_key: str | None = typer.Option(None, "--api-key"),
    model: str = typer.Option(DEFAULT_MODEL, "--model"),
) -> None:
    """Resume an interrupted enrichment run.

    Reads ``analyses.jsonl`` from the run directory, identifies which slugs
    are still missing relative to ``coverage.json``, and enriches only those.
    Re-renders the dashboard at the end.
    """
    coverage_path = run_dir / "coverage.json"
    raw_path = run_dir / "raw" / "yc_companies.json"
    jsonl_path = run_dir / "analyses.jsonl"
    if not coverage_path.exists() or not raw_path.exists():
        console.print(f"[red]✗ {run_dir} doesn't look like a valid run directory[/red]")
        raise typer.Exit(2)

    coverage = BatchCoverage.model_validate_json(coverage_path.read_text())
    raw_companies = json.loads(raw_path.read_text())
    companies = [RawCompany.model_validate(c) for c in raw_companies]
    analyzable_slugs = {r.slug for r in coverage.records if r.tier in (CoverageTier.A, CoverageTier.B)}

    completed_slugs: set[str] = set()
    existing: list[CompanyAnalysis] = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            if not line.strip():
                continue
            analysis = CompanyAnalysis.model_validate_json(line)
            existing.append(analysis)
            completed_slugs.add(analysis.slug)

    todo = [c for c in companies if c.slug in analyzable_slugs and c.slug not in completed_slugs]
    if not todo:
        console.print(f"[green]✓[/green] all {len(analyzable_slugs)} slugs already enriched")
    else:
        console.print(f"[cyan]→[/cyan] resuming: {len(completed_slugs)} done, {len(todo)} remaining")
        try:
            backend = make_default_backend(api_key=api_key)
        except RuntimeError as exc:
            console.print(f"[red]✗ no LLM backend available:[/red] {exc}")
            raise typer.Exit(3) from exc
        new_results = asyncio.run(
            _run_enrichment(
                todo,
                backend,
                model=model,
                jsonl_path=jsonl_path,
                raw_failure_path=run_dir / "raw_failures.jsonl",
            )
        )
        existing.extend(new_results)

    # Always rewrite analyses.json from the canonical jsonl.
    analyses_json = run_dir / "analyses.json"
    analyses_json.write_text(json.dumps([a.model_dump(mode="json") for a in existing], indent=2))
    console.print(f"[green]✓[/green] wrote {analyses_json}")
    _print_enrichment_summary(existing)
    console.print()
    console.print(f"[bold]next:[/bold] ycai dashboard {run_dir}")


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
    no_crawl: bool = typer.Option(
        False,
        "--no-crawl",
        help="Skip the depth=1 website crawl that recovers tech_stack and oss_posture "
        "signal. Faster but classifications fall back to YC-only context.",
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

        crawl_results: dict[str, CrawlResult] = {}
        if not no_crawl:
            crawl_targets = [c.website for c in keepers if c.website]
            console.print(f"[cyan]→[/cyan] crawling {len(crawl_targets)} websites at depth=1 (polite, robots-aware)…")
            crawl_results = asyncio.run(crawl_companies(crawl_targets))
            crawled_pages = sum(len(r.pages) for r in crawl_results.values())
            blocked = sum(1 for r in crawl_results.values() if r.robots_blocked)
            errored = sum(1 for r in crawl_results.values() if r.error)
            console.print(
                f"[green]✓[/green] crawl summary: {crawled_pages} page(s) across {len(crawl_targets)} sites "
                f"({blocked} blocked by robots, {errored} unreachable)"
            )
            crawl_jsonl = run_dir / "crawl_results.jsonl"
            with crawl_jsonl.open("w") as f:
                for url, result in crawl_results.items():
                    record = {
                        "homepage": url,
                        "robots_blocked": result.robots_blocked,
                        "error": result.error,
                        "pages": [
                            {"url": p.url, "status": p.status, "bytes": p.bytes_fetched, "chars": len(p.text)}
                            for p in result.pages
                        ],
                    }
                    f.write(json.dumps(record) + "\n")
            console.print(f"[green]✓[/green] wrote crawl_results.jsonl → {crawl_jsonl}")
        else:
            console.print("[yellow]⚠ skipping website crawl (--no-crawl)[/yellow]")

        console.print(f"[cyan]→[/cyan] enriching {len(keepers)} companies with {backend_name} (model={model})…")
        jsonl_path = run_dir / "analyses.jsonl"
        raw_failure_path = run_dir / "raw_failures.jsonl"
        analyses = asyncio.run(
            _run_enrichment(
                keepers,
                backend,
                model=model,
                jsonl_path=jsonl_path,
                raw_failure_path=raw_failure_path,
                crawl_results=crawl_results,
            )
        )
        analyses_path = run_dir / "analyses.json"
        analyses_path.write_text(json.dumps([a.model_dump(mode="json") for a in analyses], indent=2))
        console.print(f"[green]✓[/green] wrote analyses.json → {analyses_path}")
        if raw_failure_path.exists():
            failure_count = sum(1 for _ in raw_failure_path.open())
            console.print(f"[yellow]captured {failure_count} raw failure(s) for audit → {raw_failure_path}[/yellow]")
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
    jsonl_path: Path,
    raw_failure_path: Path | None = None,
    crawl_results: dict[str, CrawlResult] | None = None,
) -> list[CompanyAnalysis]:
    """Drive the enrichment pipeline with a Rich progress bar.

    Writes each completed analysis to ``jsonl_path`` immediately so a crash
    or quota wall doesn't lose progress. Resume reads this file and skips
    already-enriched slugs.
    """
    semaphore = asyncio.Semaphore(8)  # respect subscription rate limits
    results: list[CompanyAnalysis] = []
    counters: Counter[str] = Counter()
    write_lock = asyncio.Lock()
    crawl_results = crawl_results or {}

    async def one(company: RawCompany) -> CompanyAnalysis:
        async with semaphore:
            crawl = crawl_results.get(company.website) if company.website else None
            crawled_pages: list[tuple[str, str]] | None = None
            if crawl and crawl.pages:
                crawled_pages = [(p.url, p.text) for p in crawl.pages]
            analysis, _cross = await analyze(
                company,
                backend,
                model=model,
                raw_failure_log=raw_failure_path,
                crawled_pages=crawled_pages,
            )
            async with write_lock:
                with jsonl_path.open("a") as f:
                    f.write(json.dumps(analysis.model_dump(mode="json")) + "\n")
            return analysis

    description = "enriching · {high}h {med}m {low}l"
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(description.format(high=0, med=0, low=0), total=len(companies))
        coros = [one(c) for c in companies]
        for coro in asyncio.as_completed(coros):
            analysis = await coro
            results.append(analysis)
            counters[analysis.confidence] += 1
            progress.update(
                task,
                description=description.format(high=counters["high"], med=counters["medium"], low=counters["low"]),
            )
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
