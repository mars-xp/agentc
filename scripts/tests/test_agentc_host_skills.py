#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "integrations" / "host-skills"
WORKSPACE = ROOT / "integrations" / "agentc-workspace"
SKILLS = ("create", "migrate", "build", "publish-prep")
WORKSPACE_SKILL = "agentc-workspace"
WORKSPACE_COMMANDS = (
    "agentc doctor --format protocol",
    "agentc workspace build <source> --targets <hosts> --format protocol",
    "agentc status --kind <host> --name <name> --root <dir> --format protocol",
    "agentc install <host> --from <zip> --root <dir> --yes --format protocol",
    "agentc sync <host> --from <zip> --root <dir> --yes --format protocol",
    "agentc status --kind <host> --name <name> --root <dir> --format protocol",
    "agentc restore <host> --name <name> --root <dir> --format protocol",
)
WORKSPACE_RULES = (
    "Do not assume the agentc command is installed with this skill.",
    "The source root is the plugin directory the user named, not the application repository root.",
    "The targets are whichever hosts the user named.",
    "Parse only the protocol envelope and the report. Do not parse natural-language output.",
    "On failure, name the original paths from the diagnostics and the report. Ask the user to fix those files and retry. Do not install.",
    "On success, show this round's ZIP paths and the SHA-256 in archives[].sha256 from workspace-build-report.json. That hash is not a field of the protocol envelope.",
    "Install only when the user asked to install and named an install root. Use this round's ZIP.",
    "Before install or sync, run status with the same --root and --kind.",
    "If the status diagnostics include message `unregistered`, run install.",
    "If status does not report `unregistered` and the diagnostics do not contain AGC-P5-0008, the install is registered and has not drifted. Run sync.",
    "If the diagnostics contain AGC-P5-0008, stop. The installed files have drifted. Do not install or sync.",
    "If install or sync fails, show the diagnostics and run status with the same --root and --kind. Do not run restore.",
    "A missing zip, a conflict, and any other failure before the destination is written leave the current files in place.",
    "Run restore only when the user explicitly asks to undo a change this operation already wrote.",
    "Do not create a side project. Do not call import. Do not install a partial result.",
    "The only fixed exclusion is the .git/ directory at the source root.",
)
HOST_BINARIES = (
    ("cursor", "cursor"),
    ("codex", "codex"),
    ("claude-code", "claude"),
)
HOST_MANIFESTS = {
    "cursor": ".cursor-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "claude-code": ".claude-plugin/plugin.json",
}
README = ROOT / "packaging" / "agentc" / "README.md"
INSTALL_COMMAND = "agentc install <host> --from <zip> --root <dir> --yes --format protocol"
STATUS_COMMAND = "agentc status --kind <host> --name <name> --root <dir> --format protocol"
SYNC_COMMAND = "agentc sync <host> --from <zip> --root <dir> --yes --format protocol"
RESTORE_COMMAND = "agentc restore <host> --name <name> --root <dir> --format protocol"
# Skill names returned by a host session this process actually started.
# This test does not start one, so the map stays empty unless a case records a listing.
_SESSION_LISTINGS: dict[str, tuple[str, ...]] = {}


def test_host_skills_layout():
    yaml_path = HOST / "agent-project.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert "schemaVersion: agent-project/v1" in text
    assert "enabled: true" in text
    for name in SKILLS:
        skill = HOST / "skills" / name / "SKILL.md"
        body = skill.read_text(encoding="utf-8")
        assert "name: {0}".format(name) in body
        assert "--format protocol" in body
        assert "agentc" in body


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
    return env


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name == "__pycache__"}
    resolved = Path(directory).resolve()
    if resolved in {HOST.resolve(), WORKSPACE.resolve()} and "build" in names:
        ignored.add("build")
    return ignored


def _run_agentc(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentc", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=cwd or ROOT,
    )


def test_host_skills_package_three_zips():
    from agentc.protocol.schema import validate_envelope

    hosts = ("cursor", "codex", "claude-code")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "host-skills"
        shutil.copytree(
            HOST,
            dest,
            ignore=_copy_ignore,
        )
        built = _run_agentc("build", str(dest), "--format", "protocol")
        assert built.returncode == 0, built.stdout + built.stderr
        built_obj = json.loads(built.stdout)
        assert validate_envelope(built_obj) == []
        pkg = _run_agentc("package", str(dest), "--format", "protocol")
        assert pkg.returncode == 0, pkg.stdout + pkg.stderr
        obj = json.loads(pkg.stdout)
        assert validate_envelope(obj) == []
        assert obj["status"] == "ok"
        zips = {
            dest / "build" / "dist" / host / "host-skills.zip"
            for host in hosts
        }
        for zip_path in zips:
            assert zip_path.is_file(), zip_path
            assert zip_path.resolve().as_posix() in obj["artifacts"]
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                for skill in SKILLS:
                    assert any(
                        "skills/{0}/SKILL.md".format(skill) in member.replace("\\", "/")
                        for member in names
                    ), (zip_path, skill)


def _agentc_commands(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("`") and line.endswith("`") and len(line) >= 2:
            line = line[1:-1].strip()
        if line.startswith("agentc "):
            found.append(line)
    return tuple(found)


def _bash_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip() != "```bash":
            index += 1
            continue
        index += 1
        chunk: list[str] = []
        while index < len(lines) and lines[index].strip() != "```":
            stripped = lines[index].strip()
            if stripped.startswith("agentc "):
                chunk.append(stripped)
            index += 1
        blocks.append(chunk)
        index += 1
    return blocks


def _command_positions(text: str, command: str) -> list[int]:
    spots: list[int] = []
    start = 0
    while True:
        found = text.find(command, start)
        if found < 0:
            return spots
        spots.append(found)
        start = found + len(command)


def _assert_restore_only_after_install_failure(text: str, label: str) -> None:
    blocks = _bash_blocks(text)
    restore_blocks = [
        block
        for block in blocks
        if any(line.startswith("agentc restore ") for line in block)
    ]
    assert restore_blocks == [[RESTORE_COMMAND]], (label, restore_blocks)
    assert "--yes" not in RESTORE_COMMAND
    for block in blocks:
        has_write_step = any(
            line.startswith("agentc install ")
            or line.startswith("agentc status ")
            or line.startswith("agentc sync ")
            for line in block
        )
        if has_write_step:
            assert not any(line.startswith("agentc restore ") for line in block), (
                label,
                block,
            )
    status_at = _command_positions(text, STATUS_COMMAND)
    install_at = text.index(INSTALL_COMMAND)
    sync_at = text.index(SYNC_COMMAND)
    restore_at = text.index(RESTORE_COMMAND)
    assert len(status_at) == 2, (label, status_at)
    assert status_at[0] < install_at < sync_at < status_at[1] < restore_at, label
    failure_note = text[status_at[1] : restore_at]
    assert "Do not run restore." in failure_note, label
    assert "If that install fails, run restore" not in text, label
    assert "AGC-P5-0008" in text, label


def _assert_workspace_skill_text(text: str, label: str) -> None:
    assert "name: agentc-workspace" in text, label
    commands = _agentc_commands(text)
    assert commands == WORKSPACE_COMMANDS, (label, commands)
    for rule in WORKSPACE_RULES:
        assert rule in text, (label, rule)
    assert "agentc import" not in text, label
    assert "agentc init" not in text, label
    assert "agentc create" not in text, label
    _assert_restore_only_after_install_failure(text, label)


def test_workspace_skill_is_one_source():
    skills = sorted(WORKSPACE.rglob("SKILL.md"))
    assert len(skills) == 1, [path.relative_to(ROOT).as_posix() for path in skills]
    skill = skills[0]
    assert skill.relative_to(WORKSPACE).as_posix() == "skills/agentc-workspace/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    _assert_workspace_skill_text(text, "source")
    project = (WORKSPACE / "agent-project.yaml").read_text(encoding="utf-8")
    assert "id: agentc-workspace" in project
    assert "skills/agentc-workspace" in project
    for name in SKILLS:
        assert "skills/{0}".format(name) not in project


def test_workspace_skill_packages_three_hosts_from_one_body():
    from agentc.protocol.schema import validate_envelope

    hosts = ("cursor", "codex", "claude-code")
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "agentc-workspace"
        shutil.copytree(WORKSPACE, dest, ignore=_copy_ignore)
        built = _run_agentc("build", str(dest), "--format", "protocol")
        assert built.returncode == 0, built.stdout + built.stderr
        packaged = _run_agentc("package", str(dest), "--format", "protocol")
        assert packaged.returncode == 0, packaged.stdout + packaged.stderr
        obj = json.loads(packaged.stdout)
        assert validate_envelope(obj) == []
        assert obj["status"] == "ok"
        bodies: list[str] = []
        for host in hosts:
            zip_path = dest / "build" / "dist" / host / "agentc-workspace.zip"
            assert zip_path.is_file(), zip_path
            assert zip_path.resolve().as_posix() in obj["artifacts"]
            with zipfile.ZipFile(zip_path) as archive:
                names = [member.replace("\\", "/") for member in archive.namelist()]
                skill_members = [member for member in names if member.endswith("SKILL.md")]
                assert skill_members == [
                    member
                    for member in skill_members
                    if member.endswith("skills/agentc-workspace/SKILL.md")
                ], (host, skill_members)
                assert len(skill_members) == 1, (host, skill_members)
                for name in SKILLS:
                    assert not any(
                        "skills/{0}/SKILL.md".format(name) in member for member in names
                    ), (host, name)
                manifest = HOST_MANIFESTS[host]
                assert any(member.endswith(manifest) for member in names), (host, names)
                body = archive.read(skill_members[0]).decode("utf-8")
            _assert_workspace_skill_text(body, host)
            bodies.append(body)
        assert _agentc_commands(bodies[0]) == _agentc_commands(bodies[1])
        assert _agentc_commands(bodies[1]) == _agentc_commands(bodies[2])


def probe_host_load(host: str) -> str:
    """Loaded only when a host session this process started listed the skill.

    A built zip and a host binary on PATH are not a session listing. This probe
    does not install the skill and does not start a host session.
    """
    listed = _SESSION_LISTINGS.get(host, ())
    if WORKSPACE_SKILL in listed:
        return "loaded"
    return "unverified"


def _affirmative_load_claim(sentence: str) -> bool:
    match = re.search(r"\bloaded\b", sentence, re.IGNORECASE)
    if match is None:
        return False
    prefix = sentence[: match.start()]
    if re.search(r"\b(not|never|no|without)\b", prefix, re.IGNORECASE):
        return False
    if "n't" in prefix.lower():
        return False
    return True


def readme_claims_loaded(text: str, host: str) -> bool:
    names = {
        "cursor": ("cursor",),
        "codex": ("codex",),
        "claude-code": ("claude-code", "claude code"),
    }[host]
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    for sentence in sentences:
        if not _affirmative_load_claim(sentence):
            continue
        lowered = sentence.lower()
        if any(name in lowered for name in names):
            return True
        names_other_host = any(
            token in lowered for token in ("cursor", "codex", "claude")
        )
        if "host" in lowered and not names_other_host:
            return True
    return False


def _assert_readme_matches_check(text: str, statuses: dict[str, str]) -> None:
    for host, status in statuses.items():
        if status == "loaded":
            continue
        assert not readme_claims_loaded(text, host), (
            "{0} check is {1}, but the README claims that host loaded the skill".format(
                host, status
            )
        )


def _flag_value(command: str, flag: str) -> str | None:
    parts = command.split()
    if flag not in parts:
        return None
    index = parts.index(flag)
    if index + 1 >= len(parts):
        return None
    return parts[index + 1]


def test_readme_status_repeats_install_root():
    readme = README.read_text(encoding="utf-8")
    assert (
        "Codex and Claude Code installs are not visible from the default Cursor install root."
        in readme
    )
    heading = "### Install the AgentC skill"
    section = readme.split(heading, 1)[1]
    section = section.split("\n### ", 1)[0]
    commands = [
        line.strip()
        for line in section.splitlines()
        if line.strip().startswith("agentc install ")
        or line.strip().startswith("agentc status ")
    ]
    installs = [line for line in commands if line.startswith("agentc install ")]
    assert installs, section
    for position, line in enumerate(commands):
        if not line.startswith("agentc install "):
            continue
        host = line.split()[2]
        assert position + 1 < len(commands), line
        status = commands[position + 1]
        assert status.startswith("agentc status "), (line, status)
        assert "--kind {0}".format(host) in status, (line, status)
        root = _flag_value(line, "--root")
        if root is not None:
            assert "--root {0}".format(root) in status, (line, status)
    assert any("--root" in line for line in installs)


def test_host_load_check_is_session_evidence():
    host = "cursor"
    assert host not in _SESSION_LISTINGS
    assert probe_host_load(host) == "unverified"
    claim = "Cursor loaded the skill."
    assert readme_claims_loaded(claim, host)
    try:
        _assert_readme_matches_check(claim, {host: probe_host_load(host)})
    except AssertionError as exc:
        assert host in str(exc)
    else:
        raise AssertionError("README load claim was accepted without a session")
    _SESSION_LISTINGS[host] = (WORKSPACE_SKILL,)
    try:
        assert probe_host_load(host) == "loaded"
        _assert_readme_matches_check(claim, {host: probe_host_load(host)})
    finally:
        _SESSION_LISTINGS.pop(host, None)
    assert probe_host_load(host) == "unverified"
    negation = "A built zip is not proof the host loaded the skill."
    assert not readme_claims_loaded(negation, host)


def test_zip_exists_is_not_host_loaded():
    assert _SESSION_LISTINGS == {}
    readme = README.read_text(encoding="utf-8")
    statuses = {host: probe_host_load(host) for host, _binary in HOST_BINARIES}
    _assert_readme_matches_check(readme, statuses)
    for host, binary in HOST_BINARIES:
        status = statuses[host]
        present = shutil.which(binary) is not None
        print(
            "host-load {0} {1} binary={2}".format(
                host, status, "present" if present else "missing"
            )
        )
        assert status == "unverified", (host, status)


def main() -> int:
    tests = [
        test_host_skills_layout,
        test_host_skills_package_three_zips,
        test_workspace_skill_is_one_source,
        test_workspace_skill_packages_three_hosts_from_one_body,
        test_readme_status_repeats_install_root,
        test_host_load_check_is_session_evidence,
        test_zip_exists_is_not_host_loaded,
    ]
    for test in tests:
        test()
        print("ok", test.__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
