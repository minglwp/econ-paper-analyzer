from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_desktop_launcher_has_windows_runtime_and_lock_support() -> None:
    source = (PROJECT_ROOT / "desktop.py").read_text(encoding="utf-8")
    assert 'if os.name == "nt":\n    import msvcrt' in source
    assert 'LOCALAPPDATA' in source
    assert "msvcrt.locking" in source
    assert "MessageBoxW" in source
    assert "Python.Runtime.dll" in source
    assert "Zone.Identifier" in source


def test_pyinstaller_spec_selects_platform_specific_gui_backends() -> None:
    source = (PROJECT_ROOT / "EconPaperAnalyzer.spec").read_text(encoding="utf-8")
    assert 'if sys.platform == "darwin":' in source
    assert 'elif sys.platform == "win32":' in source
    assert '"webview.platforms.winforms"' in source
    assert '"webview.platforms.edgechromium"' in source
    assert "icon=executable_icon" in source
    assert "app = coll" in source


def test_windows_packaging_script_creates_portable_zip_and_optional_installer() -> None:
    source = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "PyInstaller" in source
    assert "[switch]$CreateInstaller" in source
    assert "ISCC.exe" in source
    assert "EconPaperAnalyzer.iss" in source
    assert "windows-x64-v$Version-setup.exe" in source
    assert "Get-Command python -CommandType Application" in source
    assert "Select-Object -First 1 -ExpandProperty Path" in source
    assert "econ-paper-analyzer-windows-x64-v$Version.sha256" in source
    assert "Compress-Archive" in source
    assert "Expand-Archive" in source
    assert "Get-FileHash -Algorithm SHA256" in source
    assert "econ-paper-analyzer.exe" in source


def test_inno_setup_script_installs_the_packaged_application() -> None:
    source = (PROJECT_ROOT / "scripts" / "EconPaperAnalyzer.iss").read_text(encoding="utf-8")
    assert "[Setup]" in source
    assert "DefaultDirName={localappdata}\\Programs\\Econ Paper Analyzer" in source
    assert "UninstallDisplayIcon" in source
    assert "[Icons]" in source
    assert "[Run]" in source


def test_github_actions_builds_the_windows_package_on_a_native_runner() -> None:
    source = (PROJECT_ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")
    assert "runs-on: windows-latest" in source
    assert "choco install innosetup" in source
    assert "./scripts/build_windows.ps1 -CreateInstaller" in source
    assert "actions/upload-artifact@v4" in source
    assert "gh release upload" in source
    assert "$Installer.FullName" in source
