"""Backend entry point.

    python -m papercreator                 # serve on the configured host/port
    python -m papercreator --port 9000
    python -m papercreator --dev           # auto-reload for development
    python -m papercreator --check         # diagnose the environment, then exit

``--check`` is the first thing to run when something is wrong: it reports which
retrieval providers work, whether an LLM is configured, which optional analysis
packages are importable, whether the model host is reachable, and the database
state - without starting a server.
"""

from __future__ import annotations

import argparse
import os
import sys


def run_check() -> int:
    """Print a diagnostic report. Returns a non-zero exit code on a hard problem."""
    from .core import db
    from .core.config import dotenv_source, get_settings, load_dotenv_file
    from .core.logging_setup import setup_logging
    from .core.paths import get_paths

    load_dotenv_file()
    paths = get_paths().ensure()
    setup_logging("WARNING", paths.logs_dir)
    settings = get_settings()
    problems: list[str] = []

    print("PaperCreator environment check")
    print("=" * 68)
    print(f"python           {sys.version.split()[0]} ({sys.platform})")
    print(f".env             {dotenv_source() or '(none found)'}")
    print("\npaths")
    for key, value in paths.describe().items():
        print(f"  {key:<10} {value}")

    print("\ndatabase")
    try:
        db.init_db()
        stats = db.stats()
        print(f"  schema v{stats['schema_version']}  "
              f"{stats['size_bytes'] / 1024:.0f} KB")
        counts = stats["counts"]
        print(f"  papers={counts.get('papers')} projects={counts.get('projects')} "
              f"analyses={counts.get('analyses')} runs={counts.get('agent_runs')} "
              f"skills={counts.get('skills')}")
    except Exception as exc:  # noqa: BLE001 - the report is the point
        problems.append(f"database: {exc}")
        print(f"  FAILED: {exc}")

    print("\nretrieval providers")
    try:
        from .retrieval import registry as retrieval_registry

        for entry in retrieval_registry.describe_all():
            mark = "ok  " if entry["available"] else "--  "
            note = entry["unavailable_reason"] or entry.get("coverage", "")
            print(f"  {mark}{entry['id']:<16} {entry['tier']:<9} {note[:44]}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"retrieval: {exc}")
        print(f"  FAILED: {exc}")

    print("\nLLM providers")
    try:
        from .llm import registry as llm_registry

        status = llm_registry.status()
        if status["usable"]:
            print(f"  usable: {', '.join(status['usable'])}")
            for role, model in status["roles"].items():
                print(f"  {role:<10} {model or '(not set)'}")
        else:
            print("  none configured - agent features will refuse to run")
            print("  set PC_OPENAI_API_KEY (or another key) in .env, or run Ollama")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"llm: {exc}")
        print(f"  FAILED: {exc}")

    print("\nanalysis stack")
    try:
        from .analysis import cluster, embeddings, reduce

        environment = embeddings.model_environment()
        print(f"  umap             {'yes' if reduce.umap_available() else 'no'}")
        print(f"  hdbscan          {'yes' if cluster.hdbscan_available() else 'no'}")
        print("  sentence-transformers "
              f"{'yes' if embeddings.sentence_transformers_package_available() else 'no'}")
        blocker = embeddings.sentence_transformers_blocker()
        chosen = embeddings.resolve_backend("auto")
        print(f"  model host       {environment['endpoint']}")
        if environment.get("patched"):
            print(f"  endpoint patched {', '.join(environment['patched'][:3])}")
        print(f"  auto backend     {chosen[0]} ({chosen[1]})")
        if blocker:
            print(f"  note             {blocker[:120]}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"analysis: {exc}")
        print(f"  FAILED: {exc}")

    print("\nexport")
    try:
        from .convert import exporters

        capabilities = exporters.describe_capabilities()
        print(f"  pandoc           {'yes' if capabilities['pandoc'] else 'no'}")
        engines = [k for k, v in capabilities["latex_engines"].items() if v]
        print(f"  latex engines    {', '.join(engines) or 'none'}")
        print(f"  can build pdf    {'yes' if capabilities['can_build_pdf'] else 'no'}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"export: {exc}")
        print(f"  FAILED: {exc}")

    print("\nskills")
    try:
        from .skills import loader as skills_loader

        summary = skills_loader.sync_registry()
        print(f"  {summary['count']} available {summary['by_scope']}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"skills: {exc}")
        print(f"  FAILED: {exc}")

    print("\ngit")
    try:
        from .vcs import git as git_module

        print(f"  available        {'yes' if git_module.git_available() else 'no'}")
        print(f"  auto-commit      {settings.writing.auto_git_commit}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")

    print("\n" + "=" * 68)
    if problems:
        print(f"{len(problems)} problem(s) found:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("no problems found")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="papercreator", description="PaperCreator backend server"
    )
    parser.add_argument("--host", default="", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="port (default 8765)")
    parser.add_argument(
        "--dev", action="store_true", help="auto-reload on code changes"
    )
    parser.add_argument(
        "--check", action="store_true", help="run diagnostics and exit"
    )
    parser.add_argument("--log-level", default="", help="DEBUG/INFO/WARNING/ERROR")
    args = parser.parse_args(argv)

    if args.check:
        return run_check()

    from .core.config import get_settings, load_dotenv_file

    load_dotenv_file()
    settings = get_settings()
    host = args.host or settings.server.host
    port = args.port or settings.server.port
    log_level = (args.log_level or settings.server.log_level).lower()

    import uvicorn

    if args.dev:
        # Reload needs an import string rather than an app object, and must watch
        # the package directory only - watching the workspace would reload on
        # every manuscript save.
        from pathlib import Path

        # The reload worker imports the module-level app in a child process.
        # Propagate the already-resolved CLI/settings endpoint so that its CORS
        # policy and lifecycle log describe the same socket Uvicorn binds.
        os.environ["PC_HOST"] = host
        os.environ["PC_PORT"] = str(port)

        uvicorn.run(
            "papercreator.api.app:app",
            host=host, port=port, log_level=log_level, reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
        )
    else:
        from .api.app import create_app

        app = create_app(bind_host=host, bind_port=port)

        # log_config=None keeps our own logging setup from being overwritten by
        # uvicorn's default dictConfig.  Owning the Server instance gives the
        # desktop's authenticated loopback shutdown capability a graceful flag.
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level,
            log_config=None,
            # The renderer keeps an SSE stream open.  A backend-only restart
            # cancels that connection after a short grace period, then still
            # executes FastAPI lifespan cleanup and the SQLite WAL checkpoint.
            timeout_graceful_shutdown=2,
        )
        server = uvicorn.Server(config)
        app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
        server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
