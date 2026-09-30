"""Deterministic source identity and release evidence helpers."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import subprocess
import tomllib
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

PRODUCT_ID = "ThemeScheduler"
RELEASE_RECORD_KIND = "themescheduler.release-record"
SOURCE_MANIFEST_KIND = "themescheduler.source-manifest"
BUILD_ENVIRONMENT_KIND = "themescheduler.build-environment"
QUALITY_REPORT_KIND = "themescheduler.quality-report"
SCHEMA_VERSION = 1
QUALITY_REPORT_SCHEMA_VERSION = 2
TEST_REPORT_SCHEMA_VERSION = 2
WINDOWS_X64_MACHINE = 0x8664
WINDOWS_GUI_SUBSYSTEM = 2

SOURCE_ROOTS = (
    "src",
    "entrypoints",
    "ui",
    "packaging",
    "assets",
    "tests",
    "tools",
    "docs",
)
SOURCE_FILES = (
    "pyproject.toml",
    "uv.lock",
    "MANIFEST.in",
    "README.md",
    ".gitignore",
)
IGNORED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".pyinstaller-build",
    ".pyinstaller-setup-build",
}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
IGNORED_RELATIVE_PATHS = {
    "tests/coverage_baseline.json",
}

VERSION_RESOURCES = {
    "ThemeScheduler.exe": "packaging/version_info.txt",
    "ThemeScheduler-Setup.exe": "packaging/version_info_setup.txt",
    "Uninstall.exe": "packaging/version_info_uninstall.txt",
}
QUALITY_PATHS = ("src", "entrypoints", "packaging", "tools", "tests")
REQUIRED_RELEASE_CHECK_IDS = frozenset(
    {
        "compileall",
        "uv-lock",
        "pyright",
        "ruff-lint",
        "ruff-format",
    }
)
COMPLEXITY_RULES = (
    "C901",
    "PLR0911",
    "PLR0912",
    "PLR0913",
    "PLR0915",
    "PLR1702",
)
type CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def release_quality_commands(
    python: Path,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the single authoritative Pyright/Ruff release gate commands."""

    executable = str(Path(python))
    return (
        (
            "pyright",
            (executable, "-m", "pyright", "--outputjson"),
        ),
        (
            "ruff-lint",
            (
                executable,
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--output-format=json",
                *QUALITY_PATHS,
            ),
        ),
        (
            "ruff-format",
            (
                executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "--no-cache",
                *QUALITY_PATHS,
            ),
        ),
    )


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_candidates(project_root: Path) -> Iterable[Path]:
    for relative in SOURCE_FILES:
        yield project_root / relative
    for relative in SOURCE_ROOTS:
        root = project_root / relative
        if not root.is_dir():
            raise FileNotFoundError(f"Release source root is missing: {root}")
        for candidate in root.rglob("*"):
            if candidate.is_dir():
                continue
            relative_path = candidate.relative_to(project_root)
            if relative_path.as_posix() in IGNORED_RELATIVE_PATHS:
                continue
            if any(
                part in IGNORED_PARTS or part.endswith(".egg-info")
                for part in relative_path.parts
            ):
                continue
            if candidate.suffix.lower() in IGNORED_SUFFIXES:
                continue
            yield candidate


def capture_source_manifest(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve(strict=True)
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in _source_candidates(project_root):
        candidate = candidate.resolve(strict=True)
        if not candidate.is_file():
            raise ValueError(f"Release input is not a regular file: {candidate}")
        relative = candidate.relative_to(project_root).as_posix()
        folded = relative.casefold()
        if folded in seen:
            raise ValueError(f"Duplicate release input path: {relative}")
        seen.add(folded)
        files.append(
            {
                "path": relative,
                "size": candidate.stat().st_size,
                "sha256": file_sha256(candidate),
            }
        )
    files.sort(key=lambda item: str(item["path"]).casefold())
    identity = hashlib.sha256(canonical_json_bytes({"files": files})).hexdigest()
    return {
        "kind": SOURCE_MANIFEST_KIND,
        "schemaVersion": SCHEMA_VERSION,
        "productId": PRODUCT_ID,
        "sourceTreeSha256": identity,
        "files": files,
    }


def project_version(project_root: Path) -> str:
    pyproject = Path(project_root) / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    try:
        value = payload["project"]["version"]
    except (KeyError, TypeError) as exc:
        raise ValueError("pyproject.toml has no project.version.") from exc
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+",
        value,
    ):
        raise ValueError("Release project.version must be stable X.Y.Z.")
    return value


def _resource_value(text: str, name: str) -> str:
    match = re.search(
        rf"StringStruct\(u'{re.escape(name)}', u'([^']+)'\)",
        text,
    )
    if match is None:
        raise ValueError(f"Version resource has no {name}.")
    return match.group(1)


def _fixed_file_info_version(text: str, name: str) -> tuple[int, int, int, int]:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,\s*"
        rf"([0-9]+)\s*,\s*([0-9]+)\s*\)",
        text,
    )
    if match is None:
        raise ValueError(f"Version resource has no valid {name} tuple.")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4)),
    )


def validate_manifest_configuration(project_root: Path) -> None:
    """Reject missing or external deterministic MANIFEST inputs."""

    root = Path(project_root).resolve(strict=True)
    manifest = root / "MANIFEST.in"
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        directive, _, relative = line.partition(" ")
        if directive not in {"include", "graft"}:
            continue
        relative = relative.strip()
        if not relative or any(symbol in relative for symbol in "*?[]"):
            raise ValueError(
                f"MANIFEST.in line {line_number} is not a deterministic path."
            )
        candidate = (root / relative).resolve(strict=False)
        if not candidate.is_relative_to(root):
            raise ValueError(f"MANIFEST.in line {line_number} escapes the project.")
        expected = "file" if directive == "include" else "directory"
        exists = candidate.is_file() if directive == "include" else candidate.is_dir()
        if not exists:
            raise FileNotFoundError(
                f"MANIFEST.in line {line_number} references a missing {expected}: "
                f"{relative}"
            )


def validate_release_configuration(project_root: Path, version: str) -> None:
    project_root = Path(project_root).resolve(strict=True)
    validate_manifest_configuration(project_root)
    if project_version(project_root) != version:
        raise ValueError("Build version does not match pyproject.toml project.version.")
    major, minor, patch = (int(part) for part in version.split("."))
    expected_fixed_version = (major, minor, patch, 0)
    for executable, relative in VERSION_RESOURCES.items():
        text = (project_root / relative).read_text(encoding="utf-8")
        if _fixed_file_info_version(text, "filevers") != expected_fixed_version:
            raise ValueError(f"{relative} numeric filevers does not match.")
        if _fixed_file_info_version(text, "prodvers") != expected_fixed_version:
            raise ValueError(f"{relative} numeric prodvers does not match.")
        if _resource_value(text, "FileVersion") != version:
            raise ValueError(f"{relative} FileVersion does not match.")
        if _resource_value(text, "ProductVersion") != version:
            raise ValueError(f"{relative} ProductVersion does not match.")
        if _resource_value(text, "OriginalFilename") != executable:
            raise ValueError(f"{relative} OriginalFilename does not match.")
        if _resource_value(text, "CompanyName") != PRODUCT_ID:
            raise ValueError(f"{relative} CompanyName does not match.")
    icon = project_root / "assets" / "ThemeScheduler.ico"
    header = icon.read_bytes()[:6]
    if header != struct.pack("<HHH", 0, 1, 7):
        raise ValueError("ThemeScheduler.ico is not the frozen seven-size icon.")


def validate_release_test_report(
    path: Path,
    *,
    project_root: Path,
    source_identity: str,
) -> dict[str, Any]:
    project_root = Path(project_root).resolve(strict=True)
    report_root = (project_root / "artifacts" / "test-reports").resolve(strict=True)
    path = Path(path).resolve(strict=True)
    try:
        path.relative_to(report_root)
    except ValueError as exc:
        raise ValueError(
            "Release test report must be under artifacts/test-reports."
        ) from exc
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Release test report must contain a JSON object.")
    required = {
        "kind",
        "schemaVersion",
        "capturedAt",
        "mode",
        "group",
        "patterns",
        "testsRun",
        "failures",
        "errors",
        "skipped",
        "durationSeconds",
        "checks",
        "success",
        "sourceIdentity",
    }
    if set(payload) != required:
        raise ValueError("Release test report fields do not match schema v2.")
    if (
        payload.get("kind") != "themescheduler.test-report"
        or payload.get("schemaVersion") != TEST_REPORT_SCHEMA_VERSION
        or payload.get("mode") != "release"
        or payload.get("group") is not None
        or payload.get("success") is not True
        or payload.get("failures") != 0
        or payload.get("errors") != 0
    ):
        raise ValueError("Release test report is not a successful full gate.")
    if payload.get("sourceIdentity") != source_identity:
        raise ValueError("Release test report belongs to different source.")
    if (
        isinstance(payload.get("testsRun"), bool)
        or not isinstance(payload.get("testsRun"), int)
        or payload["testsRun"] <= 0
    ):
        raise ValueError("Release test report has an invalid test count.")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ValueError("Release test report auxiliary checks did not pass.")
    check_ids = [
        check.get("checkId") if isinstance(check, dict) else None for check in checks
    ]
    if (
        len(check_ids) != len(REQUIRED_RELEASE_CHECK_IDS)
        or set(check_ids) != REQUIRED_RELEASE_CHECK_IDS
        or any(
            not isinstance(check, dict) or check.get("returnCode") != 0
            for check in checks
        )
    ):
        raise ValueError("Release test report auxiliary checks did not pass.")
    return payload


def capture_build_environment(python: Path) -> dict[str, Any]:
    python = Path(python).resolve(strict=True)
    query = (
        "import importlib.metadata as m,json,platform,struct,sys;"
        "print(json.dumps({"
        "'python':platform.python_version(),"
        "'implementation':platform.python_implementation(),"
        "'pointerBits':struct.calcsize('P')*8,"
        "'platform':platform.platform(),"
        "'windowsVersion':platform.version(),"
        "'pyinstaller':m.version('pyinstaller'),"
        "'pywebview':m.version('pywebview')"
        "},sort_keys=True))"
    )
    completed = subprocess.run(
        [str(python), "-c", query],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Cannot inspect build Python: "
            + (completed.stderr or completed.stdout).strip()
        )
    details = json.loads(completed.stdout)
    if (
        details.get("implementation") != "CPython"
        or not str(details.get("python", "")).startswith("3.12.")
        or details.get("pointerBits") != 64
        or not str(details.get("platform", "")).startswith("Windows-")
    ):
        raise ValueError("Release build requires CPython 3.12 x64 on Windows.")
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv executable is required for release builds.")
    uv_result = subprocess.run(
        [uv, "--version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if uv_result.returncode != 0:
        raise RuntimeError("Cannot read the uv version.")
    return {
        "kind": BUILD_ENVIRONMENT_KIND,
        "schemaVersion": SCHEMA_VERSION,
        "productId": PRODUCT_ID,
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "os": {
            "platform": details["platform"],
            "windowsVersion": details["windowsVersion"],
            "architecture": "x64",
        },
        "toolchain": {
            "python": details["python"],
            "implementation": details["implementation"],
            "uv": uv_result.stdout.strip().removeprefix("uv "),
            "pyinstaller": details["pyinstaller"],
            "pywebview": details["pywebview"],
        },
    }


def _run_ruff(
    python: Path,
    project_root: Path,
    arguments: list[str],
    *,
    runner: CommandRunner,
    paths: tuple[str, ...] = QUALITY_PATHS,
) -> subprocess.CompletedProcess[str]:
    return runner(
        [
            str(python),
            "-m",
            "ruff",
            *arguments,
            *paths,
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ruff_findings(
    output: str,
    *,
    project_root: Path,
    label: str,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ruff {label} returned invalid JSON.") from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise RuntimeError(f"Ruff {label} returned an invalid finding list.")
    findings: list[dict[str, Any]] = []
    for item in payload:
        reported = Path(str(item.get("filename", "")))
        filename = (
            reported if reported.is_absolute() else project_root / reported
        ).resolve(strict=False)
        if not filename.is_relative_to(project_root):
            raise RuntimeError(f"Ruff {label} reported an external path.")
        location = item.get("location")
        if not isinstance(location, dict):
            raise RuntimeError(f"Ruff {label} finding has no location.")
        findings.append(
            {
                "code": str(item.get("code", "")),
                "path": filename.relative_to(project_root).as_posix(),
                "line": int(location.get("row", 0)),
                "column": int(location.get("column", 0)),
                "message": str(item.get("message", "")),
            }
        )
    return findings


def capture_quality_report(
    project_root: Path,
    python: Path,
    *,
    source_identity: str,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Run locked Pyright/Ruff gates and retain complexity as evidence."""

    root = Path(project_root).resolve(strict=True)
    executable = Path(python).resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{64}", source_identity):
        raise ValueError("Quality report source identity is invalid.")

    ruff_version = runner(
        [str(executable), "-m", "ruff", "--version"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if ruff_version.returncode != 0 or not ruff_version.stdout.startswith("ruff "):
        raise RuntimeError("Cannot read the locked Ruff version.")

    pyright_version = runner(
        [str(executable), "-m", "pyright", "--version"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if pyright_version.returncode != 0 or not pyright_version.stdout.startswith(
        "pyright "
    ):
        raise RuntimeError("Cannot read the locked Pyright version.")

    quality_commands = dict(release_quality_commands(executable))
    pyright = runner(
        list(quality_commands["pyright"]),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        pyright_payload = json.loads(pyright.stdout)
        pyright_summary = pyright_payload["summary"]
        diagnostic_counts = {
            name: int(pyright_summary[name])
            for name in ("errorCount", "warningCount", "informationCount")
        }
        files_analyzed = int(pyright_summary["filesAnalyzed"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Pyright returned invalid JSON.") from exc
    if pyright.returncode != 0 or any(diagnostic_counts.values()):
        raise RuntimeError(
            "Pyright gate failed: "
            + ", ".join(f"{key}={value}" for key, value in diagnostic_counts.items())
        )

    lint = runner(
        list(quality_commands["ruff-lint"]),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lint_findings = _ruff_findings(
        lint.stdout,
        project_root=root,
        label="lint",
    )
    if lint.returncode != 0 or lint_findings:
        raise RuntimeError(
            f"Ruff lint gate failed with {len(lint_findings)} finding(s)."
        )

    formatting = runner(
        list(quality_commands["ruff-format"]),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if formatting.returncode != 0:
        raise RuntimeError(
            "Ruff format gate failed: "
            + (formatting.stderr or formatting.stdout).strip()
        )

    complexity = _run_ruff(
        executable,
        root,
        [
            "check",
            "--no-cache",
            "--output-format=json",
            "--select",
            ",".join(COMPLEXITY_RULES),
        ],
        runner=runner,
        paths=("src",),
    )
    if complexity.returncode not in {0, 1}:
        raise RuntimeError("Ruff complexity observation failed.")
    complexity_findings = _ruff_findings(
        complexity.stdout,
        project_root=root,
        label="complexity",
    )
    by_rule: dict[str, int] = {}
    for finding in complexity_findings:
        code = str(finding["code"])
        by_rule[code] = by_rule.get(code, 0) + 1

    return {
        "kind": QUALITY_REPORT_KIND,
        "schemaVersion": QUALITY_REPORT_SCHEMA_VERSION,
        "productId": PRODUCT_ID,
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceIdentity": source_identity,
        "tools": {
            "pyright": {
                "version": pyright_version.stdout.strip().removeprefix("pyright "),
            },
            "ruff": {
                "version": ruff_version.stdout.strip().removeprefix("ruff "),
            },
        },
        "configuration": {
            "path": "pyproject.toml",
            "sha256": file_sha256(root / "pyproject.toml"),
        },
        "paths": list(QUALITY_PATHS),
        "staticTyping": {
            "passed": True,
            "diagnostics": diagnostic_counts,
            "filesAnalyzed": files_analyzed,
        },
        "lint": {
            "passed": True,
            "findingCount": 0,
        },
        "format": {
            "passed": True,
        },
        "complexity": {
            "blocking": False,
            "paths": ["src"],
            "rules": list(COMPLEXITY_RULES),
            "findingCount": len(complexity_findings),
            "byRule": dict(sorted(by_rule.items())),
            "findings": complexity_findings,
        },
    }


def inspect_pe(path: Path) -> dict[str, Any]:
    data = Path(path).read_bytes()
    if len(data) < 256 or data[:2] != b"MZ":
        raise ValueError(f"Not a Windows PE executable: {path}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError(f"Invalid PE signature: {path}")
    machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
    optional_offset = pe_offset + 24
    magic = struct.unpack_from("<H", data, optional_offset)[0]
    subsystem = struct.unpack_from("<H", data, optional_offset + 68)[0]
    if machine != WINDOWS_X64_MACHINE or magic != 0x20B:
        raise ValueError(f"Release executable is not PE32+ x64: {path}")
    if subsystem != WINDOWS_GUI_SUBSYSTEM:
        raise ValueError(f"Release executable is not Windows GUI: {path}")
    return {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "machine": "x64",
        "format": "PE32+",
        "subsystem": "windows-gui",
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite release evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def distribution_readme(*, version: str, setup_sha256: str) -> str:
    return f"""# ThemeScheduler {version}

适用系统：Windows 11 x64。按当前用户安装，不要求预装 Python。

## 运行前验证

本版本面向少量熟人分发，ThemeScheduler 自有 EXE 未使用 Authenticode
签名，因此 Windows 可能显示 SmartScreen 提示。请只从可信发送者处获取，
并先在 PowerShell 中验证：

```powershell
(Get-FileHash .\\ThemeScheduler-Setup.exe -Algorithm SHA256).Hash
```

预期 SHA-256：

```text
{setup_sha256.upper()}
```

如文件来源或哈希不一致，请勿运行。确认发送者与哈希可信后，再按 Windows
界面决定是否继续；不要关闭 Defender、SmartScreen 或创建安全排除项。

## 首次安装

1. 双击 `ThemeScheduler-Setup.exe`，按向导完成当前用户安装。
2. 按需选择桌面快捷方式；程序默认不会立即改变当前外观。
3. 完成后打开 ThemeScheduler，配置昼夜时间、默认应用模式和两套强调色。
4. 首次点击“保存并启用”会启用计划，但不会立即套用外观；下一次计划边界才
   自动切换。

程序只管理默认应用模式和强调色，不修改默认 Windows 模式或色温“夜间模式”。
工作 GUI 无需常驻，关闭后任务计划仍会运行。

## 修复与维护

- 可在“控制面板 → 程序和功能”中选择“更改”进入维护区。
- 文件损坏或入口丢失时，直接重新运行同版本 Setup 完成事务式修复/重装；
  不要求先卸载。
- 修复、同版本重装和升级保留配置、昼夜颜色、首次安装恢复点和日志。
- 不提供后台自动更新；后续版本由用户运行新的 Setup 升级。

## 卸载

在“控制面板 → 程序和功能”中选择“卸载”。卸载向导集中提供三个选择：

- “恢复安装前外观”：恢复安装时捕获的应用模式和强调色；不改变默认 Windows
  模式。不选则保持卸载时的当前外观。
- “保留配置及昼夜颜色”：便于以后重装继续使用；不选则删除这些数据。
- “保留日志”：保留诊断日志；不选则删除日志。

程序、任务、登记、快捷方式和运行缓存始终删除。结果页点击“退出”后，临时
卸载进程和目录可能需要数秒完成自清理，不需要在任务管理器中手工结束。

## 默认位置

```text
程序：%LOCALAPPDATA%\\Programs\\ThemeScheduler
数据：%LOCALAPPDATA%\\ThemeScheduler
任务：Windows 任务计划程序 \\ThemeScheduler
```

## 首版已知限制

- 仅承诺 Windows 11 x64，不支持 ARM64。
- 只支持两个固定时间边界，不支持日出/日落计划。
- 应用需要自身支持并选择“跟随系统”才能响应深浅模式。
- 强调色应用依赖经目标 Windows 11 验证但未公开的主题管理接口；可见
  Shell 未同步时，维护区提供需用户确认的 Explorer 故障恢复。
- 不提供后台自动更新；升级或修复由用户重新运行 Setup。
"""  # noqa: RUF001 - preserve Chinese punctuation in the generated Chinese README.
