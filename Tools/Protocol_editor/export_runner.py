"""Run the existing Fantasy exporter without a shell or a console window."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess


class ExportError(ValueError):
    pass


@dataclass(frozen=True)
class ExportCommand:
    args: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True)
class ExportResult:
    returncode: int
    output: str

    @property
    def success(self):
        return self.returncode == 0


def prepare_export(source: Path, project_root: Path, *, settings_path: Path | None = None) -> ExportCommand:
    directory = project_root / "examples/Tools/ProtocolExportTool"
    exporter = directory / "Fantasy.ProtocolExportTool.dll"
    settings = settings_path or directory / "ExporterSettings.json"
    if not exporter.is_file():
        raise ExportError(f"找不到协议导出工具：{exporter}")
    if not settings.is_file():
        raise ExportError(f"找不到导出配置：{settings}")
    try:
        values = json.loads(settings.read_text(encoding="utf-8-sig"))["Export"]
        entries = [values, *values.get("PackageExports", [])]
        roots = [(settings.parent / entry["NetworkProtocolDirectory"]["Value"]).resolve() for entry in entries]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ExportError(f"无法读取导出配置：{error}") from error
    source = source.resolve()
    if not any(source.is_relative_to(root) for root in roots):
        raise ExportError("文件已保存，但不在项目的协议导出目录中；请打开 examples/Config/NetworkProtocol 下的协议再导出。")
    executable = shutil.which("dotnet")
    if not executable:
        raise ExportError("找不到 dotnet。请安装 .NET 8 运行时或 SDK，并重新启动编辑器。")
    return ExportCommand((executable, str(exporter), "export", "--silent", "--config", str(settings.resolve())), directory)


def run_export(command: ExportCommand, *, timeout: float = 120) -> ExportResult:
    try:
        process = subprocess.run(command.args, cwd=command.cwd, capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=timeout,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        output = process.stdout
        if process.stderr:
            output += ("\n" if output else "") + process.stderr
        return ExportResult(process.returncode, output.rstrip())
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return ExportResult(-1, output + "\n导出超时，已停止导出进程。请检查日志后重试。")
    except OSError as error:
        return ExportResult(-1, f"无法启动协议导出工具：{error}")
