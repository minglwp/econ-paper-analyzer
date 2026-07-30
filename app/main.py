from __future__ import annotations

import json
import re
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.concurrency import run_in_threadpool

from .config import (
    ALLOWED_SUFFIXES,
    MAX_ACTIVE_JOBS,
    MAX_RETAINED_JOBS,
    MAX_UPLOAD_BYTES,
    RESOURCE_ROOT,
    RUN_ROOT,
    UPLOAD_ROOT,
)
from .data import excel_sheets, inspect_dataframe, load_dataframe, write_json
from .runner import run_full_analysis, summarize_results
from .schemas import AnalysisRequest


app = FastAPI(title="经管论文数据自动处理器", version="0.1.0")
app.mount("/static", StaticFiles(directory=RESOURCE_ROOT / "app" / "static"), name="static")
templates = Environment(
    loader=FileSystemLoader(RESOURCE_ROOT / "app" / "templates"),
    autoescape=select_autoescape(["html"]),
)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return templates.get_template("index.html").render()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "econ-paper-analyzer"}


def _dataset_path(dataset_id: str) -> Path:
    if len(dataset_id) != 32 or any(character not in "0123456789abcdef" for character in dataset_id):
        raise HTTPException(status_code=404, detail="数据集不存在")
    matches = [
        path
        for path in UPLOAD_ROOT.glob(f"{dataset_id}_*")
        if path.suffix.lower() in ALLOWED_SUFFIXES
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="数据集不存在或已被清理")
    return matches[0]


def _dataset_response(
    dataset_id: str,
    path: Path,
    sheet_name: str | None = None,
    original_filename: str | None = None,
) -> dict[str, Any]:
    try:
        sheets = excel_sheets(path)
        selected = sheet_name or (sheets[0] if sheets else None)
        if sheet_name and sheets and sheet_name not in sheets:
            raise ValueError(f"工作表不存在: {sheet_name}")
        frame = load_dataframe(path, selected)
        inspection = inspect_dataframe(frame)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = original_filename or (
        path.name.split("_", 1)[-1] if "_" in path.name else path.name
    )
    metadata_path = UPLOAD_ROOT / f"{dataset_id}.json"
    if metadata_path.is_file():
        try:
            filename = str(json.loads(metadata_path.read_text(encoding="utf-8"))["filename"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return {
        "dataset_id": dataset_id,
        "filename": filename,
        "sheets": sheets,
        "selected_sheet": selected,
        **inspection,
    }


@app.post("/api/upload")
async def upload_dataset(
    request: Request,
    x_filename: str = Header(..., alias="X-Filename"),
) -> dict[str, Any]:
    filename = Path(unquote(x_filename)).name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持 CSV 与 XLSX 文件")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="文件超过 100 MB 限制")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 无效") from exc
    dataset_id = uuid.uuid4().hex
    path = UPLOAD_ROOT / f"{dataset_id}_upload{suffix}"
    try:
        size = 0
        with path.open("xb") as stream:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件超过 100 MB 限制")
                stream.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        path.chmod(0o600)
        response = await run_in_threadpool(
            _dataset_response, dataset_id, path, None, filename
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    metadata_path = UPLOAD_ROOT / f"{dataset_id}.json"
    write_json(
        metadata_path,
        {"dataset_id": dataset_id, "filename": filename, "stored_name": path.name},
    )
    metadata_path.chmod(0o600)
    return response


@app.get("/api/datasets/{dataset_id}")
def inspect_dataset(dataset_id: str, sheet_name: str | None = Query(default=None)) -> dict[str, Any]:
    return _dataset_response(dataset_id, _dataset_path(dataset_id), sheet_name)


def _update_job(job_id: str, **values: Any) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)


def _artifact_payload(
    run_id: str, artifacts: list[dict[str, str]]
) -> list[dict[str, str | None]]:
    return [
        {
            **artifact,
            "url": (
                f"/api/runs/{run_id}/artifacts/{artifact['name']}?inline=true"
                if artifact["name"] == "report.html"
                else f"/api/runs/{run_id}/artifacts/{artifact['name']}"
            ),
            "view_url": (
                f"/api/runs/{run_id}/artifacts/{artifact['name']}?inline=true"
                if artifact["name"] == "report.html"
                else None
            ),
        }
        for artifact in artifacts
    ]


def _run_job(job_id: str, run_id: str, request: AnalysisRequest, input_path: Path) -> None:
    run_dir = RUN_ROOT / run_id

    def progress(value: int, message: str) -> None:
        _update_job(job_id, progress=value, message=message)

    _update_job(job_id, status="running", progress=1, message="启动分析任务")
    try:
        summary, artifacts = run_full_analysis(input_path, request, run_id, run_dir, progress)
        completed_status = (
            "completed_with_errors" if summary.get("failed_modules") else "completed"
        )
        artifact_payload = _artifact_payload(run_id, artifacts)
        _update_job(
            job_id,
            status=completed_status,
            progress=100,
            message=(
                "分析完成，但部分模块失败"
                if completed_status == "completed_with_errors"
                else "分析完成"
            ),
            result=summary,
            artifacts=artifact_payload,
            run_id=run_id,
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            progress=100,
            message="分析失败",
            error=str(exc),
            run_id=run_id,
        )


@app.post("/api/analyze", status_code=202)
def start_analysis(request: AnalysisRequest) -> dict[str, str]:
    input_path = _dataset_path(request.dataset_id)
    job_id = uuid.uuid4().hex
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    with jobs_lock:
        active_jobs = sum(
            job["status"] in {"queued", "running"} for job in jobs.values()
        )
        if active_jobs >= MAX_ACTIVE_JOBS:
            raise HTTPException(status_code=429, detail="等待中的分析任务过多，请稍后再试")
        removable = [
            key
            for key, job in jobs.items()
            if job["status"] in {"completed", "completed_with_errors", "failed"}
        ]
        for key in removable[: max(0, len(jobs) - MAX_RETAINED_JOBS + 1)]:
            jobs.pop(key, None)
        jobs[job_id] = {
            "job_id": job_id,
            "run_id": run_id,
            "status": "queued",
            "progress": 0,
            "message": "等待执行",
            "result": None,
            "artifacts": [],
            "error": None,
        }
    executor.submit(_run_job, job_id, run_id, request, input_path)
    return {"job_id": job_id, "run_id": run_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在或服务已重启")
        return dict(job)


def _run_directory(run_id: str) -> Path:
    if re.fullmatch(r"run-[a-f0-9]{12}", run_id) is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return (RUN_ROOT / run_id).resolve()


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict[str, Any]:
    run_dir = _run_directory(run_id)
    with jobs_lock:
        active = next(
            (dict(job) for job in jobs.values() if job.get("run_id") == run_id),
            None,
        )
    if active:
        return active
    results_path = run_dir / "results.json"
    marker_path = run_dir / ".complete"
    if marker_path.is_file() and results_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            results = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="运行结果无法读取") from exc
        required = {
            "report.html",
            "tables.xlsx",
            "results.json",
            "analysis_config.json",
            "analysis_bundle.zip",
        }
        completed_names = set(marker.get("artifacts", []))
        missing = sorted(
            name
            for name in required
            if name not in completed_names or not (run_dir / name).is_file()
        )
        bundle_path = run_dir / "analysis_bundle.zip"
        if not missing and not zipfile.is_zipfile(bundle_path):
            missing.append("analysis_bundle.zip（文件损坏）")
        if missing:
            return {
                "run_id": run_id,
                "status": "failed",
                "progress": 100,
                "message": "历史运行产物不完整",
                "result": None,
                "artifacts": [],
                "error": "缺少或损坏的产物: " + ", ".join(missing),
            }
        summary = summarize_results(results)
        artifacts = [
            artifact
            for artifact in results.get("artifacts", [])
            if (run_dir / Path(artifact.get("name", "")).name).is_file()
        ]
        return {
            "run_id": run_id,
            "status": (
                "completed_with_errors"
                if summary.get("failed_modules")
                else "completed"
            ),
            "progress": 100,
            "message": "已恢复历史分析结果",
            "result": summary,
            "artifacts": _artifact_payload(run_id, artifacts),
            "error": None,
        }
    if run_dir.is_dir():
        return {
            "run_id": run_id,
            "status": "failed",
            "progress": 100,
            "message": "分析在生成结果前中断",
            "result": None,
            "artifacts": [],
            "error": "服务曾在任务完成前停止，请重新运行分析。",
        }
    raise HTTPException(status_code=404, detail="运行记录不存在")


@app.get("/api/runs/{run_id}/artifacts/{artifact_name}")
def download_artifact(
    run_id: str,
    artifact_name: str,
    inline: bool = Query(default=False),
) -> FileResponse:
    run_dir = _run_directory(run_id)
    path = (run_dir / Path(artifact_name).name).resolve()
    if path.parent != run_dir or not path.is_file():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    media_types = {
        ".html": "text/html; charset=utf-8",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".json": "application/json",
        ".zip": "application/zip",
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }
    return FileResponse(
        path,
        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
        filename=None if inline else path.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@app.post("/api/demo")
def load_demo_dataset() -> dict[str, Any]:
    source = RESOURCE_ROOT / "examples" / "demo_survey.csv"
    if not source.exists():
        raise HTTPException(status_code=500, detail="示例数据尚未生成")
    dataset_id = uuid.uuid4().hex
    target = UPLOAD_ROOT / f"{dataset_id}_upload{source.suffix.lower()}"
    target.write_bytes(source.read_bytes())
    target.chmod(0o600)
    metadata_path = UPLOAD_ROOT / f"{dataset_id}.json"
    write_json(
        metadata_path,
        {"dataset_id": dataset_id, "filename": source.name, "stored_name": target.name},
    )
    metadata_path.chmod(0o600)
    response = _dataset_response(dataset_id, target)
    response["suggested_config"] = {
        "scales": [
            {"name": "创新氛围", "items": ["创新氛围1", "创新氛围2", "创新氛围3"], "reverse_items": [], "minimum": 1, "maximum": 7, "min_valid_ratio": 0.8},
            {"name": "工作投入", "items": ["工作投入1", "工作投入2", "工作投入3"], "reverse_items": ["工作投入3"], "minimum": 1, "maximum": 7, "min_valid_ratio": 0.8},
            {"name": "领导支持", "items": ["领导支持1", "领导支持2", "领导支持3"], "reverse_items": [], "minimum": 1, "maximum": 7, "min_valid_ratio": 0.8},
            {"name": "创新绩效", "items": ["创新绩效1", "创新绩效2", "创新绩效3"], "reverse_items": [], "minimum": 1, "maximum": 7, "min_valid_ratio": 0.8},
        ],
        "roles": {"x": "创新氛围", "y": "创新绩效", "mediator": "工作投入", "moderator": "领导支持", "controls": ["年龄", "性别"]},
    }
    return response
