"""Apply install plans with atomic writes."""

from __future__ import annotations

import os
import shutil

from agentc.installer.ledger import hash_tree, write_ledger
from agentc.installer.pipeline import InstallPlan
from agentc.model.diagnostics import CODE_INSTALL_CONFLICT, CODE_INSTALL_IO, Diagnostic


def _conflict_diagnostic(plan: InstallPlan, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_INSTALL_CONFLICT,
        level="error",
        message=message,
        path=plan.dest.as_posix(),
        hint="",
    )


def _io_diagnostic(plan: InstallPlan, message: str) -> Diagnostic:
    return Diagnostic(
        code=CODE_INSTALL_IO,
        level="error",
        message=message,
        path=plan.dest.as_posix(),
        hint="",
    )


def apply_install(
    plan: InstallPlan, diagnostics: list[Diagnostic], text: str
) -> tuple[list[Diagnostic], str, bool]:
    diagnostics = list(diagnostics)

    if plan.conflicts:
        diagnostics.append(
            _conflict_diagnostic(
                plan,
                "install destination conflicts with zip contents",
            )
        )
        return diagnostics, text, False

    if plan.dest.exists():
        if plan.create:
            diagnostics.append(
                _conflict_diagnostic(
                    plan,
                    "install destination is missing files required for an identical match",
                )
            )
            return diagnostics, text, False
        return diagnostics, text, False

    staging_dest = plan.install_root / f".agentc-install-tmp-{plan.plugin_name}"

    try:
        plan.install_root.mkdir(parents=True, exist_ok=True)
        if staging_dest.exists():
            shutil.rmtree(staging_dest)
        shutil.copytree(plan.extracted_plugin, staging_dest)
        if plan.dest.exists():
            shutil.rmtree(staging_dest)
            diagnostics.append(
                _conflict_diagnostic(
                    plan,
                    "install destination appeared before atomic replace",
                )
            )
            return diagnostics, text, False
        os.replace(staging_dest, plan.dest)
        try:
            write_ledger(
                install_root=plan.install_root,
                host=plan.host,
                name=plan.plugin_name,
                dest=plan.dest,
                files=hash_tree(plan.dest),
            )
        except OSError as exc:
            if plan.dest.exists():
                shutil.rmtree(plan.dest, ignore_errors=True)
            diagnostics.append(_io_diagnostic(plan, str(exc)))
            return diagnostics, text, False
    except OSError as exc:
        if staging_dest.exists():
            shutil.rmtree(staging_dest, ignore_errors=True)
        diagnostics.append(_io_diagnostic(plan, str(exc)))
        return diagnostics, text, False

    return diagnostics, text, True
