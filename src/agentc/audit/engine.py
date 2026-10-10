from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

import yaml

# --- 规则分级 -----------------------------------------------------------------
#
# 记录类（recording）：命中只写进 CopyReport.findings 并打印摘要,文件照常进候选仓。
# 阻断类（blocking）：命中即 unlink + 记入 CopyReport.blocked。
# 凭据类（credential）：阻断类的子集,额外让 materialize_candidate 抛错退出,
#   不允许走到 _git_init_commit——凭据泄漏一旦进了 git 历史就很难擦干净。
#
# 分级默认值是"阻断"：只有显式列在 RECORDING_RULES 里的规则才放行。
# 所以以后新加规则忘了登记,结果是误杀（可查、可 exclude）,不是漏放。
# 新规则若属于凭据类,还要自己加进 CREDENTIAL_RULES,否则只阻断、不拦 git init。

RECORDING_RULES = frozenset(
    {
        # 编译器自身的检测常量就长这样（mcp.py 的 _HOME_PREFIXES 等）,删掉等于删功能。
        "absolute-home-path",
        # 外部邮箱（维护者、上游 issue 链接）不是内部资产,留给人判断。
        "email",
        # 第三方 pip 源：既不是内网源也不是官方 PyPI,报出来让人看一眼。
        "package-index",
    }
)

CREDENTIAL_RULES = frozenset(
    {
        "github-token",
        "internal-domain",
        "internal-email",
        "internal-git-remote",
        "internal-package-index",
    }
)


def is_blocking_rule(rule: str) -> bool:
    """新增规则默认阻断；只有登记为记录类的规则放行。"""
    return rule not in RECORDING_RULES


def is_credential_rule(rule: str) -> bool:
    """凭据类命中要在 git init 之前中止物化。"""
    return rule in CREDENTIAL_RULES


# --- 数据面 -------------------------------------------------------------------


@dataclass(frozen=True)
class ScanRules:
    internal_domain_suffixes: tuple[str, ...]
    internal_hosts: tuple[str, ...]
    public_package_index_hosts: tuple[str, ...]
    example_domains: tuple[str, ...]

    def is_internal_host(self, host: str) -> bool:
        lowered = host.lower()
        if any(lowered == known or lowered.endswith("." + known)
               for known in self.internal_hosts):
            return True
        return any(
            lowered == suffix or lowered.endswith("." + suffix)
            for suffix in self.internal_domain_suffixes
        )

    def mentions_internal_host(self, text: str) -> bool:
        lowered = text.lower()
        needles = self.internal_hosts + self.internal_domain_suffixes
        return any(needle in lowered for needle in needles)


def _tuple_of(payload: dict, key: str) -> tuple[str, ...]:
    return tuple(str(item) for item in (payload.get(key) or []))


def load_scan_rules(path: Path) -> ScanRules:
    if not path.is_file():
        raise FileNotFoundError(
            f"scan rules not found: {path}. Phase 0 handoff files live under the "
            "gitignored /docs/ tree; check the .gitignore whitelist for "
            "docs/superpowers/specs/2026-09-09-open-source-cross-platform-agent-compiler/ "
            "or stage the file with `git add -f`."
        )
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ScanRules(
        internal_domain_suffixes=_tuple_of(payload, "internal_domain_suffixes"),
        internal_hosts=_tuple_of(payload, "internal_hosts"),
        public_package_index_hosts=_tuple_of(payload, "public_package_index_hosts"),
        example_domains=_tuple_of(payload, "example_domains"),
    )


def public_rules_path() -> Path:
    return Path(str(files("agentc.audit").joinpath("public-scan-rules.yaml")))


@lru_cache(maxsize=1)
def default_scan_rules() -> ScanRules:
    return load_scan_rules(public_rules_path())


# --- 正则 ---------------------------------------------------------------------

_INTERNAL_PLUGIN_PATTERNS = (
    "jxp_" + "private_plugin",
    "kiwi-" + "tv-plugin",
)

_HOME_PATH_PATTERNS = (
    "/Users/",
    "/home/",
)

# Real Windows home paths use a single backslash (C:\Users\alice); JSON/py string
# literals double it (C:\\Users\\alice). Drive letter and "Users" are case-insensitive.
_WINDOWS_HOME_RE = re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}", re.IGNORECASE)

_GITHUB_TOKEN_RE = re.compile(r"ghp_[A-Za-z0-9]{20,}")

_PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# git@host:path 与 ssh://[user@]host/path 两种远端写法。
_GIT_REMOTE_RE = re.compile(
    r"(?:ssh://[^\s'\"<>]+|git@[A-Za-z0-9._-]+[:/][^\s'\"<>]*)"
)

# pip 的 --index-url / --extra-index-url，含 pip.conf 里的 `index-url = ...` 写法。
_PIP_INDEX_RE = re.compile(
    r"(?:--)?(?:extra-)?index-url\s*=?\s*(\S+)", re.IGNORECASE
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_URL_HOST_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://([^/\s]+)")


@dataclass(frozen=True)
class ScanFinding:
    path: str
    rule: str
    snippet: str


def _redact_snippet(text: str) -> str:
    if len(text) <= 16:
        return text
    return text[:8] + "***" + text[-8:]


def _is_placeholder_span(start: int, end: int, text: str) -> bool:
    for match in _PLACEHOLDER_RE.finditer(text):
        if match.start() <= start and end <= match.end():
            return True
    return False


def _is_example_domain_context(
    start: int, end: int, text: str, rules: ScanRules
) -> bool:
    lowered = text.lower()
    for domain in rules.example_domains:
        pos = 0
        while True:
            idx = lowered.find(domain, pos)
            if idx == -1:
                break
            domain_end = idx + len(domain)
            # 令牌整体落在示例域名里,或邮箱域名就是示例域名（span 覆盖到 @ 之前）。
            if idx <= start and end <= domain_end:
                return True
            if start <= idx and domain_end <= end:
                return True
            pos = idx + 1
    return False


def _in_any_span(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(lo <= start and end <= hi for lo, hi in spans)


def _url_host(url: str) -> str:
    match = _URL_HOST_RE.match(url.strip("'\"<>,;"))
    if not match:
        return ""
    host = match.group(1)
    if "@" in host:  # 去掉 user:pass@
        host = host.rsplit("@", 1)[1]
    if host.startswith("["):  # IPv6 字面量
        return host
    return host.split(":", 1)[0]


# --- 各规则 -------------------------------------------------------------------


def _add_literal_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
    rule: str,
    patterns: tuple[str, ...],
) -> None:
    for pattern in patterns:
        start = 0
        while True:
            idx = text.find(pattern, start)
            if idx == -1:
                break
            match_text = text[idx : idx + len(pattern)]
            findings.append(
                ScanFinding(
                    path=relposix,
                    rule=rule,
                    snippet=_redact_snippet(match_text),
                )
            )
            start = idx + len(pattern)


def _add_windows_home_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
) -> None:
    for match in _WINDOWS_HOME_RE.finditer(text):
        findings.append(
            ScanFinding(
                path=relposix,
                rule="absolute-home-path",
                snippet=_redact_snippet(match.group(0)),
            )
        )


def _add_token_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
    rules: ScanRules,
) -> None:
    for match in _GITHUB_TOKEN_RE.finditer(text):
        start, end = match.span()
        if _is_placeholder_span(start, end, text):
            continue
        if _is_example_domain_context(start, end, text, rules):
            continue
        findings.append(
            ScanFinding(
                path=relposix,
                rule="github-token",
                snippet=_redact_snippet(match.group(0)),
            )
        )


def _add_internal_domain_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
    rules: ScanRules,
) -> None:
    lowered = text.lower()
    for suffix in rules.internal_domain_suffixes:
        start = 0
        while True:
            idx = lowered.find(suffix, start)
            if idx == -1:
                break
            findings.append(
                ScanFinding(
                    path=relposix,
                    rule="internal-domain",
                    snippet=_redact_snippet(text[idx : idx + len(suffix)]),
                )
            )
            start = idx + len(suffix)


def _add_git_remote_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
    rules: ScanRules,
) -> list[tuple[int, int]]:
    """报内部 git 远端；返回所有远端 span,供邮箱规则去重（`git@host` 长得像邮箱）。"""
    spans: list[tuple[int, int]] = []
    for match in _GIT_REMOTE_RE.finditer(text):
        spans.append(match.span())
        if not rules.mentions_internal_host(match.group(0)):
            continue
        findings.append(
            ScanFinding(
                path=relposix,
                rule="internal-git-remote",
                snippet=_redact_snippet(match.group(0)),
            )
        )
    return spans


def _add_package_index_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
    rules: ScanRules,
) -> None:
    for match in _PIP_INDEX_RE.finditer(text):
        url = match.group(1)
        host = _url_host(url)
        if not host:
            continue
        if rules.is_internal_host(host):
            rule = "internal-package-index"
        elif host.lower() in {h.lower() for h in rules.public_package_index_hosts}:
            continue
        else:
            rule = "package-index"
        findings.append(
            ScanFinding(
                path=relposix,
                rule=rule,
                snippet=_redact_snippet(host),
            )
        )


def _add_email_findings(
    findings: list[ScanFinding],
    relposix: str,
    text: str,
    rules: ScanRules,
    git_remote_spans: list[tuple[int, int]],
) -> None:
    for match in _EMAIL_RE.finditer(text):
        start, end = match.span()
        if _in_any_span(start, end, git_remote_spans):
            continue
        if _is_placeholder_span(start, end, text):
            continue
        if _is_example_domain_context(start, end, text, rules):
            continue
        domain = match.group(0).rsplit("@", 1)[1]
        rule = "internal-email" if rules.is_internal_host(domain) else "email"
        findings.append(
            ScanFinding(
                path=relposix,
                rule=rule,
                snippet=_redact_snippet(match.group(0)),
            )
        )


def scan_text(
    relposix: str, text: str, rules: ScanRules | None = None
) -> list[ScanFinding]:
    active = rules if rules is not None else default_scan_rules()
    rel = Path(relposix).as_posix()
    findings: list[ScanFinding] = []
    _add_literal_findings(findings, rel, text, "internal-plugin", _INTERNAL_PLUGIN_PATTERNS)
    _add_literal_findings(findings, rel, text, "absolute-home-path", _HOME_PATH_PATTERNS)
    _add_windows_home_findings(findings, rel, text)
    _add_token_findings(findings, rel, text, active)
    _add_internal_domain_findings(findings, rel, text, active)
    git_remote_spans = _add_git_remote_findings(findings, rel, text, active)
    _add_package_index_findings(findings, rel, text, active)
    _add_email_findings(findings, rel, text, active, git_remote_spans)
    return findings


def scan_tree(
    root: Path, rel_files: list[str], rules: ScanRules | None = None
) -> list[ScanFinding]:
    active = rules if rules is not None else default_scan_rules()
    findings: list[ScanFinding] = []
    for relposix in rel_files:
        rel = Path(relposix).as_posix()
        path = root / rel
        text = path.read_text(encoding="utf-8")
        findings.extend(scan_text(rel, text, active))
    return findings
