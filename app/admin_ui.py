"""Serve the Vite React admin SPA from web/dist, with a legacy fallback."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(include_in_schema=False)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPA_ROOT = _REPO_ROOT / "web" / "dist"
_LEGACY_ROOT = Path(__file__).with_name("static") / "admin"


def _spa_index() -> Optional[Path]:
    candidate = _SPA_ROOT / "index.html"
    return candidate if candidate.is_file() else None


def _legacy_page() -> str:
    css = (_LEGACY_ROOT / "admin.css").read_text(encoding="utf-8") if (_LEGACY_ROOT / "admin.css").is_file() else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>FinSight Admin</title>
<style>{css}</style></head>
<body class="login-shell">
  <div class="login-card">
    <h1>管理后台尚未构建</h1>
    <p class="muted">请在仓库根目录执行 <code>cd web && npm ci && npm run build</code>，
    或开发时运行 <code>npm run dev</code>（代理到 API :8000）。</p>
  </div>
</body></html>"""


@router.get("/admin", response_class=HTMLResponse)
@router.get("/admin/", response_class=HTMLResponse)
def admin_console() -> HTMLResponse:
    index = _spa_index()
    if index:
        return HTMLResponse(
            content=index.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    return HTMLResponse(
        content=_legacy_page(),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/admin/assets/{asset_path:path}")
def admin_assets(asset_path: str) -> FileResponse:
    """Compatibility aliases used by older experience tests."""
    mapping = {
        "admin.css": _LEGACY_ROOT / "admin.css",
        "admin.js": _LEGACY_ROOT / "admin.js",
    }
    target = mapping.get(asset_path)
    if target and target.is_file():
        media = "text/css" if asset_path.endswith(".css") else "application/javascript"
        return FileResponse(target, media_type=media, headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(status_code=404, detail="ASSET_NOT_FOUND")


@router.get("/admin/{asset_path:path}")
def admin_spa_assets(asset_path: str):
    if asset_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    index = _spa_index()
    if not index:
        return admin_console()
    candidate = (_SPA_ROOT / asset_path).resolve()
    if str(candidate).startswith(str(_SPA_ROOT.resolve())) and candidate.is_file():
        return FileResponse(candidate, headers={"Cache-Control": "public, max-age=3600"})
    # SPA client-side routes
    return HTMLResponse(
        content=index.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )
