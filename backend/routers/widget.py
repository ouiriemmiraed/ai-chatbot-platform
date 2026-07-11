from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from models import User
from core.auth import get_current_user
import asyncio
import subprocess
import os
import sys

router = APIRouter(prefix="/widget", tags=["Widget"])

# Widget directory mounted as volume in Docker at /widget
WIDGET_DIR = '/widget' if os.path.exists('/widget') else os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'widget'))
WIDGET_BUNDLE_PATH = os.path.join(WIDGET_DIR, 'build', 'widget.bundle.js')

# npm is npm.cmd on Windows, npm on Linux/macOS (Docker)
NPM_CMD = 'npm.cmd' if sys.platform == 'win32' else 'npm'


@router.post("/build")
async def build_and_download_widget(
    current_user: User = Depends(get_current_user),
):
    """
    Trigger a full `npm run build:widget` inside the /widget folder,
    then stream the resulting widget.bundle.js as a file download.
    Build runs in a thread so it does not block the event loop.
    """
    if not os.path.isdir(WIDGET_DIR):
        raise HTTPException(
            status_code=500,
            detail=f"Widget directory not found at: {WIDGET_DIR}"
        )

    def run_build():
        return subprocess.run(
            [NPM_CMD, 'run', 'build:widget'],
            cwd=WIDGET_DIR,
            capture_output=True,
            text=True,
            timeout=300,   # 5 min max
        )

    result = await asyncio.to_thread(run_build)

    if result.returncode != 0:
        # Log the full build output server-side; never leak npm stderr (paths,
        # env hints) to the client.
        import logging
        logging.getLogger(__name__).error(
            "Widget build failed (rc=%s):\n%s", result.returncode,
            result.stderr[-3000:] if result.stderr else "No error output",
        )
        raise HTTPException(
            status_code=500,
            detail="Widget build failed. Check server logs for details."
        )

    if not os.path.exists(WIDGET_BUNDLE_PATH):
        raise HTTPException(
            status_code=500,
            detail="Build succeeded but widget.bundle.js was not created."
        )

    return FileResponse(
        WIDGET_BUNDLE_PATH,
        media_type='application/javascript',
        filename='widget.bundle.js',
        headers={"Content-Disposition": "attachment; filename=widget.bundle.js"},
    )


@router.get("/download")
async def download_existing_widget(
    current_user: User = Depends(get_current_user),
):
    """
    Download the already-built widget.bundle.js without running a new build.
    Useful when the file already exists and you just want to grab it.
    """
    if not os.path.exists(WIDGET_BUNDLE_PATH):
        raise HTTPException(
            status_code=404,
            detail="widget.bundle.js not found. Run a build first."
        )

    return FileResponse(
        WIDGET_BUNDLE_PATH,
        media_type='application/javascript',
        filename='widget.bundle.js',
        headers={"Content-Disposition": "attachment; filename=widget.bundle.js"},
    )
