from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_desktop_launcher_has_windows_runtime_and_lock_support() -> None:
    source = (PROJECT_ROOT / "desktop.py").read_text(encoding="utf-8")
    assert 'if os.name == "nt":\n    import msvcrt' in source
    assert 'LOCALAPPDATA' in source
    assert "msvcrt.locking" in source
    assert "MessageBoxW" in source


def test_pyinstaller_spec_selects_platform_specific_gui_backends() -> None:
    source = (PROJECT_ROOT / "EconPaperAnalyzer.spec").read_text(encoding="utf-8")
    assert 'if sys.platform == "darwin":' in source
    assert 'elif sys.platform == "win32":' in source
    assert '"webview.platforms.winforms"' in source
    assert '"webview.platforms.edgechromium"' in source
    assert "icon=executable_icon" in source
    assert "app = coll" in source


def test_windows_packaging_script_creates_and_verifies_portable_zip() -> None:
    source = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "PyInstaller" in source
    assert "Get-Command python -CommandType Application" in source
    assert "Select-Object -First 1 -ExpandProperty Path" in source
    assert "econ-paper-analyzer-windows-x64-v$Version.sha256" in source
    assert "Compress-Archive" in source
    assert "Expand-Archive" in source
    assert "Get-FileHash -Algorithm SHA256" in source
    assert "econ-paper-analyzer.exe" in source


def test_github_actions_builds_the_windows_package_on_a_native_runner() -> None:
    source = (PROJECT_ROOT / ".github" / "workflows" / "windows-package.yml").read_text(encoding="utf-8")
    assert "runs-on: windows-latest" in source
    assert "./scripts/build_windows.ps1" in source
    assert "actions/upload-artifact@v4" in source
    assert "gh release upload" in source
