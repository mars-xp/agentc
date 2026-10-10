from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from agentc.importers.detect import IMPORT_KINDS
from agentc.installer.ledger import INSTALL_HOSTS
from agentc.model.diagnostics import Diagnostic
from agentc.project.create_extension import create_agent, create_command, create_rule
from agentc.project.create_skill import create_skill
from agentc.project.init_project import init_project
from agentc.protocol.envelope import emit_protocol, wrap
from agentc.validate.engine import validate_project


def _diagnostic_to_dict(d: Diagnostic) -> dict[str, str | None]:
    return {
        "code": d.code,
        "level": d.level,
        "message": d.message,
        "path": d.path,
        "hint": d.hint,
    }


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


def _host_from_path(path: str | None) -> str | None:
    if not path:
        return None
    prefix = "/targets/"
    if path.startswith(prefix):
        rest = path[len(prefix) :]
        if rest and "/" not in rest:
            return rest
    return None


def _hosts_with_error_or_warning(diagnostics: list[Diagnostic]) -> set[str]:
    blocked: set[str] = set()
    for diagnostic in diagnostics:
        if diagnostic.level not in ("error", "warning"):
            continue
        host = _host_from_path(diagnostic.path)
        if host is not None:
            blocked.add(host)
    return blocked


def _has_global_error(diagnostics: list[Diagnostic]) -> bool:
    return any(
        diagnostic.level == "error" and _host_from_path(diagnostic.path) is None
        for diagnostic in diagnostics
    )


def _print_diagnostic(d: Diagnostic) -> None:
    path_part = f" ({d.path})" if d.path else ""
    print(f"{d.level}: {d.code}: {d.message}{path_part}")
    print(f"  hint: {d.hint}")


def _emit(diagnostics: list[Diagnostic], fmt: str, *, group_by_host: bool = False) -> None:
    if fmt == "json":
        payload = [_diagnostic_to_dict(d) for d in diagnostics]
        print(json.dumps(payload, ensure_ascii=False))
        return
    if not group_by_host:
        for d in diagnostics:
            _print_diagnostic(d)
        return
    general: list[Diagnostic] = []
    by_host: dict[str, list[Diagnostic]] = {}
    host_order: list[str] = []
    for d in diagnostics:
        host = _host_from_path(d.path)
        if host is None:
            general.append(d)
            continue
        if host not in by_host:
            by_host[host] = []
            host_order.append(host)
        by_host[host].append(d)
    for d in general:
        _print_diagnostic(d)
    for host in host_order:
        print(f"host: {host}")
        for d in by_host[host]:
            _print_diagnostic(d)


def _collect_build_artifacts(project_root: Path) -> list[str]:
    outputs_root = project_root.resolve() / "build" / "outputs"
    if not outputs_root.is_dir():
        return []
    artifacts: list[str] = []
    for host_dir in sorted(outputs_root.iterdir()):
        if not host_dir.is_dir():
            continue
        for child in sorted(host_dir.iterdir()):
            if child.is_dir():
                artifacts.append(child.resolve().as_posix())
    return sorted(artifacts)


def _collect_package_artifacts(project_root: Path) -> list[str]:
    dist_root = project_root.resolve() / "build" / "dist"
    if not dist_root.is_dir():
        return []
    return sorted(
        path.resolve().as_posix()
        for path in dist_root.rglob("*.zip")
        if path.is_file()
    )


def _build_package_text_body(
    diagnostics: list[Diagnostic],
    *,
    source_failed: bool,
    project_root: Path,
    policy: str,
) -> str | None:
    from agentc.build.plan import BuildPlan
    from agentc.project.load import load_project_yaml, to_ir

    chunks: list[str] = []
    if diagnostics:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            _emit(diagnostics, "text", group_by_host=True)
        finally:
            sys.stdout = old_stdout
        chunks.append(buf.getvalue())
    if not source_failed:
        root = project_root.resolve()
        ir = to_ir(load_project_yaml(root / "agent-project.yaml"))
        plan = BuildPlan.from_ir(root, ir, policy)
        blocked = _hosts_with_error_or_warning(diagnostics)
        skip_ok = _has_global_error(diagnostics)
        for host in plan.enabled_hosts:
            if host not in blocked:
                chunks.append(f"host: {host}\n")
                if not skip_ok:
                    chunks.append("ok\n")
    return "".join(chunks) if chunks else None


def _finish(
    *,
    fmt: str,
    diagnostics: list[Diagnostic],
    exit_code: int,
    artifacts: list[str],
    confirmation_required: bool,
    summary: str,
    text_body: str | None = None,
    group_by_host: bool = False,
) -> int:
    if fmt == "protocol":
        emit_protocol(
            wrap(
                diagnostics=diagnostics,
                exit_code=exit_code,
                artifacts=artifacts,
                confirmation_required=confirmation_required,
                summary=summary,
            )
        )
        return exit_code
    if fmt == "json":
        _emit(diagnostics, "json")
        return exit_code
    if text_body is not None:
        print(text_body, end="")
    elif diagnostics:
        _emit(diagnostics, fmt, group_by_host=group_by_host)
    return exit_code


def _parse_targets(value: str) -> tuple[str, ...]:
    if value == "all":
        return ("all",)
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--format",
        choices=("text", "json", "protocol"),
        default="text",
        help="Output format (default: text).",
    )

    # Nested parsers must not re-default --format, or a value given before the
    # sub-subcommand would be silently downgraded back to text.
    nested = argparse.ArgumentParser(add_help=False)
    nested.add_argument(
        "--format",
        choices=("text", "json", "protocol"),
        default=argparse.SUPPRESS,
        help="Output format (default: text).",
    )

    parser = argparse.ArgumentParser(prog="agentc")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", parents=[common])
    init_parser.add_argument("dir", type=Path)
    init_parser.add_argument("--id", required=True)
    init_parser.add_argument("--name", required=True)
    init_parser.add_argument("--targets", required=True)
    init_parser.add_argument("--license", default="Apache-2.0")

    create_parser = subparsers.add_parser("create", parents=[common])
    create_sub = create_parser.add_subparsers(dest="create_kind", required=True)
    skill_parser = create_sub.add_parser("skill", parents=[nested])
    skill_parser.add_argument("name")
    skill_parser.add_argument("--project", type=Path, required=True)

    rule_parser = create_sub.add_parser("rule", parents=[nested])
    rule_parser.add_argument("name")
    rule_parser.add_argument("--project", type=Path, required=True)

    command_parser = create_sub.add_parser("command", parents=[nested])
    command_parser.add_argument("name")
    command_parser.add_argument("--project", type=Path, required=True)

    agent_parser = create_sub.add_parser("agent", parents=[nested])
    agent_parser.add_argument("name")
    agent_parser.add_argument("--project", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", parents=[common])
    validate_parser.add_argument("dir", type=Path)
    validate_parser.add_argument(
        "--policy",
        choices=("strict", "compatible", "permissive"),
        default=None,
    )

    build_parser = subparsers.add_parser("build", parents=[common])
    build_parser.add_argument("dir", type=Path)
    build_parser.add_argument(
        "--policy",
        choices=("strict", "compatible", "permissive"),
        default=None,
    )
    build_parser.add_argument("--profile", action="append", default=[])

    package_parser = subparsers.add_parser("package", parents=[common])
    package_parser.add_argument("dir", type=Path)
    package_parser.add_argument(
        "--policy",
        choices=("strict", "compatible", "permissive"),
        default=None,
    )

    inspect_parser = subparsers.add_parser("inspect", parents=[common])
    inspect_parser.add_argument("dir", type=Path)
    inspect_parser.add_argument("--kind", choices=IMPORT_KINDS, default=None)

    import_parser = subparsers.add_parser("import", parents=[common])
    import_sub = import_parser.add_subparsers(dest="import_kind", required=True)
    for import_kind in IMPORT_KINDS:
        kind_import = import_sub.add_parser(import_kind, parents=[nested])
        kind_import.add_argument("--from", dest="from_dir", type=Path, required=True)
        kind_import.add_argument("--output", type=Path, required=True)
        kind_import.add_argument("--yes", action="store_true")
    merge_import = import_sub.add_parser("merge", parents=[nested])
    merge_import.add_argument("--cursor", dest="cursor", type=Path, default=None)
    merge_import.add_argument(
        "--cursor-workspace", dest="cursor_workspace", type=Path, default=None
    )
    merge_import.add_argument(
        "--cursor-user", dest="cursor_user", type=Path, default=None
    )
    merge_import.add_argument("--codex", dest="codex", type=Path, default=None)
    merge_import.add_argument(
        "--claude-code", dest="claude_code", type=Path, default=None
    )
    merge_import.add_argument("--output", type=Path, required=True)
    merge_import.add_argument("--yes", action="store_true")

    audit_parser = subparsers.add_parser("audit", parents=[common])
    audit_parser.add_argument("--for-public", action="store_true")
    audit_parser.add_argument("dir", type=Path, nargs="?", default=None)

    doctor_parser = subparsers.add_parser("doctor", parents=[common])
    doctor_parser.add_argument("--dir", type=Path, default=None)

    matrix_parser = subparsers.add_parser("matrix", parents=[common])
    matrix_parser.add_argument(
        "--host",
        choices=("cursor", "codex", "claude-code"),
        default=None,
    )

    install_parser = subparsers.add_parser("install", parents=[common])
    install_sub = install_parser.add_subparsers(dest="install_kind", required=True)
    for install_kind in INSTALL_HOSTS:
        kind_install = install_sub.add_parser(install_kind, parents=[nested])
        kind_install.add_argument("--from", dest="from_zip", type=Path, required=True)
        kind_install.add_argument("--root", type=Path, default=None)
        kind_install.add_argument("--yes", action="store_true")

    all_install = install_sub.add_parser("all", parents=[nested])
    all_install.add_argument("--cursor", dest="cursor_zip", type=Path, required=True)
    all_install.add_argument("--codex", dest="codex_zip", type=Path, required=True)
    all_install.add_argument("--claude-code", dest="claude_zip", type=Path, required=True)
    all_install.add_argument("--cursor-root", type=Path, default=None)
    all_install.add_argument("--codex-root", type=Path, default=None)
    all_install.add_argument("--claude-root", type=Path, default=None)
    all_install.add_argument("--yes", action="store_true")
    all_install.add_argument("--partial-ok", dest="partial_ok", action="store_true")

    uninstall_parser = subparsers.add_parser("uninstall", parents=[common])
    uninstall_sub = uninstall_parser.add_subparsers(dest="uninstall_kind", required=True)
    for uninstall_kind in INSTALL_HOSTS:
        kind_uninstall = uninstall_sub.add_parser(uninstall_kind, parents=[nested])
        kind_uninstall.add_argument("--name", required=True)
        kind_uninstall.add_argument("--root", type=Path, default=None)
        kind_uninstall.add_argument("--yes", action="store_true")

    status_parser = subparsers.add_parser("status", parents=[common])
    status_parser.add_argument("--kind", choices=INSTALL_HOSTS, default=None)
    status_parser.add_argument("--name", default=None)
    status_parser.add_argument("--root", type=Path, default=None)

    sync_parser = subparsers.add_parser("sync", parents=[common])
    sync_sub = sync_parser.add_subparsers(dest="sync_kind", required=True)
    for sync_kind in INSTALL_HOSTS:
        kind_sync = sync_sub.add_parser(sync_kind, parents=[nested])
        kind_sync.add_argument("--from", dest="from_zip", type=Path, required=True)
        kind_sync.add_argument("--root", type=Path, default=None)
        kind_sync.add_argument("--yes", action="store_true")

    restore_parser = subparsers.add_parser("restore", parents=[common])
    restore_sub = restore_parser.add_subparsers(dest="restore_kind", required=True)
    for restore_kind in INSTALL_HOSTS:
        kind_restore = restore_sub.add_parser(restore_kind, parents=[nested])
        kind_restore.add_argument("--name", required=True)
        kind_restore.add_argument("--root", type=Path, default=None)
        kind_restore.add_argument("--yes", action="store_true")

    from agentc.workspace.source import SOURCE_KINDS

    workspace_parser = subparsers.add_parser("workspace", parents=[common])
    workspace_sub = workspace_parser.add_subparsers(dest="workspace_kind", required=True)
    workspace_build = workspace_sub.add_parser("build", parents=[nested])
    workspace_build.add_argument("source", type=Path)
    workspace_build.add_argument("--targets", required=True)
    workspace_build.add_argument("--source-kind", choices=SOURCE_KINDS, default=None)
    workspace_build.add_argument("--output", type=Path, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    fmt = args.format

    if args.command == "init":
        diagnostics = init_project(
            args.dir,
            project_id=args.id,
            name=args.name,
            targets=_parse_targets(args.targets),
            license_id=args.license,
        )
        exit_code = 1 if _has_errors(diagnostics) else 0
        artifacts = [args.dir.resolve().as_posix()] if exit_code == 0 else []
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=False,
            summary="init ok" if exit_code == 0 else "init failed",
        )
    elif args.command == "create":
        if args.create_kind == "skill":
            diagnostics = create_skill(args.project, args.name)
            artifact_rel = f"skills/{args.name}"
        elif args.create_kind == "rule":
            diagnostics = create_rule(args.project, args.name)
            artifact_rel = f"extensions/rules/{args.name}.md"
        elif args.create_kind == "command":
            diagnostics = create_command(args.project, args.name)
            artifact_rel = f"extensions/commands/{args.name}.md"
        elif args.create_kind == "agent":
            diagnostics = create_agent(args.project, args.name)
            artifact_rel = f"extensions/agents/{args.name}.md"
        else:
            parser.error(f"Unknown create kind: {args.create_kind}")
        exit_code = 1 if _has_errors(diagnostics) else 0
        if exit_code == 0:
            artifacts = [(args.project.resolve() / artifact_rel).as_posix()]
        else:
            artifacts = []
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=False,
            summary=(
                f"create {args.create_kind} ok"
                if exit_code == 0
                else f"create {args.create_kind} failed"
            ),
        )
    elif args.command == "validate":
        diagnostics = validate_project(args.dir, policy=args.policy)
        exit_code = 1 if _has_errors(diagnostics) else 0
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=[],
            confirmation_required=False,
            summary="validate ok" if exit_code == 0 else "validate failed",
        )
    elif args.command == "audit":
        from agentc.audit.run import audit_directory

        diagnostics = audit_directory(args.dir, for_public=args.for_public)
        exit_code = 1 if _has_errors(diagnostics) else 0
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=[],
            confirmation_required=False,
            summary="audit ok" if exit_code == 0 else "audit failed",
        )
    elif args.command == "doctor":
        from agentc.doctor.run import run_doctor

        diagnostics = run_doctor(args.dir)
        exit_code = 1 if _has_errors(diagnostics) else 0
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=[],
            confirmation_required=False,
            summary="doctor ok" if exit_code == 0 else "doctor failed",
        )
    elif args.command == "matrix":
        from agentc.matrix.load import load_profile_document

        document = load_profile_document(host=args.host)
        if fmt == "json":
            print(json.dumps(document, ensure_ascii=False))
            return 0
        if fmt == "protocol":
            return _finish(
                fmt=fmt,
                diagnostics=[],
                exit_code=0,
                artifacts=[],
                confirmation_required=False,
                summary="matrix ok",
            )
        for profile in document["profiles"]:
            print("id: {0}".format(profile["id"]))
            print("host: {0}".format(profile["host"]))
            capabilities = profile.get("capabilities") or {}
            for capability, level in capabilities.items():
                print("  {0}: {1}".format(capability, level))
        return 0
    elif args.command == "build":
        from agentc.build.run import build_project, exit_code_for_build

        diagnostics, source_failed, policy = build_project(
            args.dir,
            policy=args.policy,
            profile_overrides=tuple(args.profile or ()),
        )
        exit_code = exit_code_for_build(
            diagnostics, policy, source_failed=source_failed
        )
        text_body = None
        if fmt == "text":
            text_body = _build_package_text_body(
                diagnostics,
                source_failed=source_failed,
                project_root=args.dir,
                policy=policy,
            )
        artifacts = _collect_build_artifacts(args.dir) if exit_code == 0 else []
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=False,
            summary="build ok" if exit_code == 0 else "build failed",
            text_body=text_body,
        )
    elif args.command == "package":
        from agentc.build.package import exit_code_for_package, package_project

        diagnostics, source_failed, policy = package_project(
            args.dir, policy=args.policy
        )
        exit_code = exit_code_for_package(
            diagnostics, policy, source_failed=source_failed
        )
        text_body = None
        if fmt == "text":
            text_body = _build_package_text_body(
                diagnostics,
                source_failed=source_failed,
                project_root=args.dir,
                policy=policy,
            )
        artifacts = _collect_package_artifacts(args.dir) if exit_code == 0 else []
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=False,
            summary="package ok" if exit_code == 0 else "package failed",
            text_body=text_body,
        )
    elif args.command == "inspect":
        from agentc.importers.run import inspect_source

        diagnostics, _plan, text_report = inspect_source(args.dir, kind=args.kind)
        exit_code = 1 if _has_errors(diagnostics) else 0
        text_body = None
        if fmt == "text":
            if exit_code != 0:
                error_lines = []
                for d in diagnostics:
                    if d.level == "error":
                        path_part = f" ({d.path})" if d.path else ""
                        error_lines.append(
                            f"{d.level}: {d.code}: {d.message}{path_part}\n"
                        )
                        error_lines.append(f"  hint: {d.hint}\n")
                text_body = "".join(error_lines) if error_lines else None
            else:
                text_body = text_report
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=[],
            confirmation_required=False,
            summary="inspect ok" if exit_code == 0 else "inspect failed",
            text_body=text_body,
        )
    elif args.command == "import":
        if args.import_kind == "merge":
            from agentc.importers.run import import_merge

            diagnostics, text_report = import_merge(
                cursor=args.cursor,
                cursor_workspace=args.cursor_workspace,
                cursor_user=args.cursor_user,
                codex=args.codex,
                claude_code=args.claude_code,
                output_dir=args.output,
                yes=args.yes,
            )
        else:
            from agentc.importers.run import import_source

            diagnostics, text_report = import_source(
                args.from_dir,
                args.output,
                kind=args.import_kind,
                yes=args.yes,
            )
        exit_code = 1 if _has_errors(diagnostics) else 0
        confirmation_required = not args.yes
        if exit_code == 0 and args.yes:
            artifacts = [args.output.resolve().as_posix()]
        else:
            artifacts = []
        text_body = None
        if fmt == "text":
            text_chunks: list[str] = []
            if text_report:
                text_chunks.append(text_report)
            if exit_code != 0:
                for d in diagnostics:
                    path_part = f" ({d.path})" if d.path else ""
                    text_chunks.append(
                        f"{d.level}: {d.code}: {d.message}{path_part}\n"
                    )
                    text_chunks.append(f"  hint: {d.hint}\n")
            text_body = "".join(text_chunks) if text_chunks else None
        summary = "import ok" if exit_code == 0 else "import failed"
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=confirmation_required,
            summary=summary,
            text_body=text_body,
        )
    elif args.command == "install":
        if args.install_kind == "all":
            from agentc.installer.run import install_all

            diagnostics, text_report, dests = install_all(
                cursor_zip=args.cursor_zip,
                codex_zip=args.codex_zip,
                claude_zip=args.claude_zip,
                cursor_root=args.cursor_root,
                codex_root=args.codex_root,
                claude_root=args.claude_root,
                yes=args.yes,
                partial_ok=args.partial_ok,
            )
            exit_code = 1 if _has_errors(diagnostics) else 0
            confirmation_required = not args.yes
            artifacts = (
                [dest.resolve().as_posix() for dest in dests]
                if args.yes and exit_code == 0
                else []
            )
        else:
            from agentc.installer.run import install_zip

            diagnostics, text_report, dest, wrote = install_zip(
                args.from_zip, args.root, host=args.install_kind, yes=args.yes
            )
            exit_code = 1 if _has_errors(diagnostics) else 0
            confirmation_required = not args.yes
            artifacts = (
                [dest.resolve().as_posix()]
                if args.yes and exit_code == 0 and dest is not None and wrote
                else []
            )
        text_body = None
        if fmt == "text":
            text_chunks: list[str] = []
            if text_report:
                text_chunks.append(text_report)
            if exit_code != 0:
                for d in diagnostics:
                    path_part = f" ({d.path})" if d.path else ""
                    text_chunks.append(
                        f"{d.level}: {d.code}: {d.message}{path_part}\n"
                    )
                    text_chunks.append(f"  hint: {d.hint}\n")
            text_body = "".join(text_chunks) if text_chunks else None
        summary = "install ok" if exit_code == 0 else "install failed"
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=confirmation_required,
            summary=summary,
            text_body=text_body,
        )
    elif args.command == "uninstall":
        from agentc.installer.run import uninstall_plugin

        diagnostics, text_report, dest = uninstall_plugin(
            host=args.uninstall_kind,
            name=args.name,
            install_root=args.root,
            yes=args.yes,
        )
        exit_code = 1 if _has_errors(diagnostics) else 0
        confirmation_required = not args.yes
        artifacts = (
            [dest.resolve().as_posix()]
            if args.yes and exit_code == 0 and dest is not None and dest.exists()
            else []
        )
        text_body = None
        if fmt == "text":
            text_chunks: list[str] = []
            if text_report:
                text_chunks.append(text_report)
            if exit_code != 0:
                for d in diagnostics:
                    path_part = f" ({d.path})" if d.path else ""
                    text_chunks.append(
                        f"{d.level}: {d.code}: {d.message}{path_part}\n"
                    )
                    text_chunks.append(f"  hint: {d.hint}\n")
            text_body = "".join(text_chunks) if text_chunks else None
        summary = "uninstall ok" if exit_code == 0 else "uninstall failed"
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=confirmation_required,
            summary=summary,
            text_body=text_body,
        )
    elif args.command == "status":
        from agentc.installer.run import status_installs

        diagnostics, text_report = status_installs(
            host=args.kind, name=args.name, install_root=args.root
        )
        exit_code = 1 if _has_errors(diagnostics) else 0
        text_body = None
        if fmt == "text":
            text_chunks: list[str] = []
            if text_report:
                text_chunks.append(text_report)
            for d in diagnostics:
                path_part = f" ({d.path})" if d.path else ""
                text_chunks.append(f"{d.level}: {d.code}: {d.message}{path_part}\n")
                text_chunks.append(f"  hint: {d.hint}\n")
            text_body = "".join(text_chunks) if text_chunks else None
        summary = "status ok" if exit_code == 0 else "status failed"
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=[],
            confirmation_required=False,
            summary=summary,
            text_body=text_body,
        )
    elif args.command == "sync":
        from agentc.installer.run import sync_zip

        diagnostics, text_report, dest = sync_zip(
            args.from_zip, args.root, host=args.sync_kind, yes=args.yes
        )
        exit_code = 1 if _has_errors(diagnostics) else 0
        confirmation_required = not args.yes
        artifacts = (
            [dest.resolve().as_posix()]
            if args.yes and exit_code == 0 and dest is not None
            else []
        )
        text_body = None
        if fmt == "text":
            text_chunks: list[str] = []
            if text_report:
                text_chunks.append(text_report)
            if exit_code != 0:
                for d in diagnostics:
                    path_part = f" ({d.path})" if d.path else ""
                    text_chunks.append(
                        f"{d.level}: {d.code}: {d.message}{path_part}\n"
                    )
                    text_chunks.append(f"  hint: {d.hint}\n")
            text_body = "".join(text_chunks) if text_chunks else None
        summary = "sync ok" if exit_code == 0 else "sync failed"
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=confirmation_required,
            summary=summary,
            text_body=text_body,
        )
    elif args.command == "restore":
        from agentc.installer.run import restore_plugin

        diagnostics, text_report, dest = restore_plugin(
            host=args.restore_kind,
            name=args.name,
            install_root=args.root,
            yes=args.yes,
        )
        exit_code = 1 if _has_errors(diagnostics) else 0
        confirmation_required = not args.yes
        artifacts = (
            [dest.resolve().as_posix()]
            if args.yes and exit_code == 0 and dest is not None and dest.exists()
            else []
        )
        text_body = None
        if fmt == "text":
            text_chunks: list[str] = []
            if text_report:
                text_chunks.append(text_report)
            if exit_code != 0:
                for d in diagnostics:
                    path_part = f" ({d.path})" if d.path else ""
                    text_chunks.append(
                        f"{d.level}: {d.code}: {d.message}{path_part}\n"
                    )
                    text_chunks.append(f"  hint: {d.hint}\n")
            text_body = "".join(text_chunks) if text_chunks else None
        summary = "restore ok" if exit_code == 0 else "restore failed"
        return _finish(
            fmt=fmt,
            diagnostics=diagnostics,
            exit_code=exit_code,
            artifacts=artifacts,
            confirmation_required=confirmation_required,
            summary=summary,
            text_body=text_body,
        )
    elif args.command == "workspace":
        from agentc.workspace.run import run_workspace_build

        result = run_workspace_build(
            source=args.source,
            targets=tuple(part.strip() for part in args.targets.split(",")),
            source_kind=args.source_kind,
            output_dir=args.output,
        )
        return _finish(
            fmt=fmt,
            diagnostics=result.diagnostics,
            exit_code=result.exit_code,
            artifacts=result.artifacts,
            confirmation_required=False,
            summary=result.summary,
            text_body=result.text_body,
        )
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
