from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS WKWebView")
def test_native_desktop_loads_styles_bridge_and_demo(tmp_path: Path) -> None:
    if os.environ.get("EPA_NATIVE_E2E") != "1":
        pytest.skip("set EPA_NATIVE_E2E=1 to run the native desktop test")
    result_path = tmp_path / "native-result.json"
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(result_path)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["runtime"] == "desktop"
    assert result["bridge"] is True
    assert result["body_background"] == "rgb(244, 246, 247)"
    assert result["hidden_display"] == "none"
    assert result["job_calls"] == 0
    assert result["fetch_calls"] == 0
    assert result["upload_state"] == "示例已就绪"
    assert "示例数据" in result["dataset_context"]
    assert "320 行 × 15 列" in result["dataset_context"]
    assert result["scale_count"] == 4
    assert result["model_count"] == 4
    assert result["variables_unlocked"] is True
    assert result["data_review_visible"] is True
    assert result["settings_enabled"] is True
    assert result["settings_saves"] == 1
    assert result["confirmed_predictors"] == ["年龄"]
    assert result["ordinal_confirmation_visible"] is True
    assert result["ordinal_confirmation_variables"] == ["年龄"]
    assert result["control_picker"]["selected_after_add"] == ["年龄", "性别", "工作投入"]
    assert result["control_picker"]["available_after_add"] is False
    assert result["control_picker"]["selected_after_role_change"] == ["年龄", "性别"]
    assert result["control_picker"]["controls_contain_outcome"] is False
    assert result["artifact_saves"] == ["tables.xlsx"]
    assert "已保存 tables.xlsx" in result["toasts"]
    assert any(toast.startswith("当前设置已导出：") for toast in result["toasts"])
    assert "描述性统计" in result["result_preview"]["text"]
    assert "关键回归系数" in result["result_preview"]["text"]
    assert result["result_preview"]["correlation_headers"] == ["变量", "创新氛围", "创新绩效"]
    assert result["result_preview"]["correlation_value"] == "0.420**"
    assert result["result_preview"]["ulmc_headers"] == ["指标", "特质模型拟合结果", "方法因子模型拟合", "对比结果"]
    assert result["upload_error_hidden"] is True
    assert result["upload_error"] == ""
    assert {"pywebviewready", "window.onerror", "unhandledrejection"} <= set(result["events"])


def _run_native_child(temp_root: Path, result_path: Path) -> int:
    os.environ["EPA_RUNTIME_ROOT"] = str(temp_root / "runtime")
    os.environ["EPA_CACHE_ROOT"] = str(temp_root / "cache")
    os.environ["EPA_RUN_ROOT"] = str(temp_root / "runs")
    os.environ["EPA_UPLOAD_ROOT"] = str(temp_root / "uploads")
    for key in ("EPA_RUNTIME_ROOT", "EPA_CACHE_ROOT", "EPA_RUN_ROOT", "EPA_UPLOAD_ROOT"):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)

    import webview

    import desktop
    from app.desktop_bridge import DesktopBridge

    class RecordingBridge(DesktopBridge):
        def __init__(self) -> None:
            super().__init__()
            self._job_calls = 0
            self._events: list[str] = []
            self._settings_saves: list[dict[str, object]] = []
            self._artifact_saves: list[str] = []

        def get_job(self, job_id: str) -> dict[str, object]:
            self._job_calls += 1
            return {
                "job_id": job_id,
                "run_id": "run-native-e2e",
                "status": "completed",
                "progress": 100,
                "message": "saved job recovered",
                "result": {"summary": {}},
                "artifacts": [],
            }

        def log_frontend_event(self, payload):
            if isinstance(payload, dict) and isinstance(payload.get("event"), str):
                self._events.append(payload["event"])
            return super().log_frontend_event(payload)

        def save_settings(self, filename: str, content: str) -> dict[str, object]:
            self._settings_saves.append({"filename": filename, "content": json.loads(content)})
            return {"saved": True, "filename": filename}

        def save_artifact(self, run_id: str, artifact_name: str) -> dict[str, object]:
            self._artifact_saves.append(f"{run_id}/{artifact_name}")
            return {"saved": True, "filename": artifact_name}

    bridge = RecordingBridge()
    prelude = """<script data-native-test-prelude>
window.__nativeFetchCount = 0;
window.fetch = async function () {
  window.__nativeFetchCount += 1;
  throw new Error("Native desktop must not use HTTP");
};
</script>
"""
    html = desktop._desktop_html().replace(
        '<script data-desktop-resource="app.js">',
        prelude + '<script data-desktop-resource="app.js">',
        1,
    )
    window = webview.create_window(
        "Econ Paper Analyzer Native Test",
        html=html,
        js_api=bridge,
        width=1100,
        height=760,
        min_size=(900, 650),
        text_select=True,
        background_color="#ffffff",
    )
    if window is None:
        raise RuntimeError("native test window was not created")
    bridge._set_window(window)
    failure: list[BaseException] = []

    def exercise() -> None:
        try:
            initial = window.evaluate_js("""(() => ({
  runtime: window.__EPA_RUNTIME__,
  bridge: typeof window.pywebview?.api?.load_demo === "function",
  bodyBackground: getComputedStyle(document.body).backgroundColor,
  hiddenDisplay: getComputedStyle(document.querySelector(".is-hidden")).display
}))()""")
            deadline = time.monotonic() + 15
            while bridge._job_calls < 1 and time.monotonic() < deadline:
                time.sleep(0.1)
            window.evaluate_js('document.getElementById("resetButton").click(); true')
            window.evaluate_js('document.getElementById("demoButton").click(); true')
            ui = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                ui = window.evaluate_js("""(() => ({
  uploadState: document.getElementById("uploadState").textContent,
  datasetContext: document.getElementById("datasetContext").textContent,
  scaleCount: document.querySelectorAll("#scaleList .scale-editor").length,
  modelCount: document.querySelectorAll("#pathModelList .path-model-editor").length,
  variablesUnlocked: !document.querySelector('[data-step-target="variables"]').disabled,
  dataReviewVisible: !document.getElementById("dataReview").classList.contains("is-hidden"),
  settingsEnabled: !document.getElementById("importSettingsButton").disabled && !document.getElementById("exportSettingsButton").disabled,
  uploadErrorHidden: document.getElementById("uploadError").classList.contains("is-hidden"),
  uploadError: document.getElementById("uploadError").textContent,
  fetchCalls: window.__nativeFetchCount
}))()""")
                if ui["uploadState"] == "示例已就绪":
                    break
                time.sleep(0.1)
            control_picker = window.evaluate_js("""(() => {
  const editor = document.querySelector(".path-model-editor");
  const available = editor.querySelector(".model-controls-available");
  available.value = "工作投入";
  editor.querySelector(".assign-controls").click();
  const selectedAfterAdd = [...editor.querySelectorAll(".model-controls-selected-list .picker-selected-item")]
    .map((row) => row.dataset.item);
  const availableAfterAdd = [...available.options].some((option) => option.value === "工作投入");
  const outcome = editor.querySelector(".model-y");
  outcome.value = "工作投入";
  outcome.dispatchEvent(new Event("change", { bubbles: true }));
  const selectedAfterRoleChange = [...editor.querySelectorAll(".model-controls-selected-list .picker-selected-item")]
    .map((row) => row.dataset.item);
  return {
    selectedAfterAdd,
    availableAfterAdd,
    selectedAfterRoleChange,
    controlsContainOutcome: selectedAfterRoleChange.includes(outcome.value),
  };
})()""")
            ordinal_confirmation = window.evaluate_js("""(() => {
  state.dataset.uniqueValues["年龄"] = 3;
  updateOrdinalConfirmations();
  const input = document.querySelector("#ordinalVariableList input");
  if (!input) return { visible: false, variables: [] };
  input.checked = true;
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return {
    visible: !document.getElementById("ordinalConfirmation").classList.contains("is-hidden"),
    variables: [...document.querySelectorAll("#ordinalVariableList strong")].map((item) => item.textContent)
  };
})()""")
            result_preview = window.evaluate_js("""(() => {
  renderResults({
    summary: {
      input_rows: 320,
      in_app_preview: {
        "描述性统计": [{ variable: "创新氛围", n: 320, mean: 4.2, sd: 0.8 }],
        "共同方法偏差：ULMC": [{ "指标": "CFI", "特质模型拟合结果": 0.91, "方法因子模型拟合": 0.95, "对比结果": 0.04 }],
        "相关分析": {
          display: "correlation_lower_triangle",
          method: "pearson",
          variables: ["创新氛围", "创新绩效"],
          rows: [
            { variable: "创新氛围", values: [""] },
            { variable: "创新绩效", values: ["0.420**", ""] }
          ],
          truncated: false,
          total_variables: 2
        },
        "回归分析": { "关键回归系数": [{ model: "主效应模型", term: "创新氛围", b: 0.4, p: 0.01 }] }
      }
    },
    diagnostics: {},
    artifacts: []
  });
  return {
    text: document.getElementById("resultSummary").textContent,
    correlationHeaders: [...document.querySelectorAll(".correlation-matrix thead th")].map((item) => item.textContent),
    correlationValue: document.querySelector(".correlation-matrix tbody tr:nth-child(2) td")?.textContent,
    ulmcHeaders: [...document.querySelectorAll(".result-table table thead tr")]
      .map((row) => [...row.querySelectorAll("th")].map((item) => item.textContent))
      .find((headers) => headers.includes("特质模型拟合结果"))
  };
})()""")
            window.evaluate_js('document.getElementById("exportSettingsButton").click(); true')
            window.evaluate_js("""(() => {
  renderArtifacts([{
    name: "论文结果表 Excel",
    filename: "tables.xlsx",
    url: "desktop-artifact://run-native-e2e/tables.xlsx",
    type: "XLSX",
    native: true,
    runId: "run-native-e2e",
    artifactName: "tables.xlsx"
  }]);
  document.querySelector("#artifactList .artifact-item").click();
  return true;
})()""")
            deadline = time.monotonic() + 10
            while (
                (len(bridge._settings_saves) != 1 or len(bridge._artifact_saves) != 1)
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            toasts = window.evaluate_js('([...document.querySelectorAll("#toastRegion .toast")].map((item) => item.textContent))')
            window.evaluate_js("""(() => {
  setTimeout(() => { throw new Error("native-e2e-window-error"); }, 0);
  Promise.reject(new Error("native-e2e-unhandled-rejection"));
  return true;
})()""")
            deadline = time.monotonic() + 10
            required = {"pywebviewready", "window.onerror", "unhandledrejection"}
            while not required.issubset(bridge._events) and time.monotonic() < deadline:
                time.sleep(0.1)
            if ui is None:
                raise RuntimeError("desktop UI state was not read")
            result_path.write_text(
                json.dumps(
                    {
                        "runtime": initial["runtime"],
                        "bridge": initial["bridge"],
                        "body_background": initial["bodyBackground"],
                        "hidden_display": initial["hiddenDisplay"],
                        "job_calls": bridge._job_calls,
                        "events": bridge._events,
                        "upload_state": ui["uploadState"],
                        "dataset_context": ui["datasetContext"],
                        "scale_count": ui["scaleCount"],
                        "model_count": ui["modelCount"],
                        "variables_unlocked": ui["variablesUnlocked"],
                        "data_review_visible": ui["dataReviewVisible"],
                        "settings_enabled": ui["settingsEnabled"],
                        "settings_saves": len(bridge._settings_saves),
                        "confirmed_predictors": bridge._settings_saves[0]["content"]["configuration"]["inference"]["treat_as_continuous"],
                        "ordinal_confirmation_visible": ordinal_confirmation["visible"],
                        "ordinal_confirmation_variables": ordinal_confirmation["variables"],
                        "control_picker": {
                            "selected_after_add": control_picker["selectedAfterAdd"],
                            "available_after_add": control_picker["availableAfterAdd"],
                            "selected_after_role_change": control_picker["selectedAfterRoleChange"],
                            "controls_contain_outcome": control_picker["controlsContainOutcome"],
                        },
                        "artifact_saves": [entry.rsplit("/", 1)[-1] for entry in bridge._artifact_saves],
                        "toasts": toasts,
                        "result_preview": result_preview,
                        "upload_error_hidden": ui["uploadErrorHidden"],
                        "upload_error": ui["uploadError"],
                        "fetch_calls": ui["fetchCalls"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except BaseException as exc:
            failure.append(exc)
        finally:
            window.destroy()

    webview.start(exercise, private_mode=True, http_server=False, storage_path=str(temp_root / "webview"))
    if failure:
        raise failure[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_native_child(Path(sys.argv[1]), Path(sys.argv[2])))
