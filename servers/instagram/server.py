"""
Share2Gemini Instagram Media Server
------------------------------------
A lightweight FastAPI server powered by instagrapi to extract
actual Instagram video/image URLs without needing a browser.

Usage:
    pip install -r requirements.txt
    python server.py

The server exposes:
    GET  /health           → Health check
    POST /extract          → Extract media URL from Instagram link
    GET  /extract?url=...  → Same via query param

The Android app calls this at: http://localhost:8765/extract
You can also host it on a VPS/cloud and change the URL in the extension config.
"""

import os
import re
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from instagrapi import Client
    from instagrapi.exceptions import (
        LoginRequired,
        MediaNotFound,
        PleaseWaitFewMinutes,
        ClientError,
    )
    INSTAGRAPI_AVAILABLE = True
except ImportError:
    INSTAGRAPI_AVAILABLE = False
    logging.warning("instagrapi not installed — running in limited mode")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("instagram-server")

# ─────────────────────────────────────────────
# Config — set via environment or .env file
# ─────────────────────────────────────────────
IG_USERNAME = os.environ.get("IG_USERNAME", "")
IG_PASSWORD = os.environ.get("IG_PASSWORD", "")
IG_SESSION_FILE = os.environ.get("IG_SESSION_FILE", "ig_session.json")
PORT = int(os.environ.get("PORT", "8765"))

cl: Optional["Client"] = None


def get_shortcode(url: str) -> Optional[str]:
    """Extract Instagram shortcode from URL."""
    pattern = r"instagram\.com/(?:reel|reels|p|stories|tv)/([A-Za-z0-9_-]+)"
    match = re.search(pattern, url, re.IGNORECASE)
    return match.group(1) if match else None


def init_client() -> Optional["Client"]:
    """Initialize and authenticate the instagrapi client."""
    global cl
    if not INSTAGRAPI_AVAILABLE:
        log.error("instagrapi not installed")
        return None
    if not IG_USERNAME or not IG_PASSWORD:
        log.warning("IG_USERNAME / IG_PASSWORD not set — anonymous mode only")
        cl = Client()
        return cl

    try:
        cl = Client()
        cl.delay_range = [1, 3]  # Be polite to Instagram servers

        # Try loading saved session first (avoids 2FA challenges)
        if os.path.exists(IG_SESSION_FILE):
            try:
                cl.load_settings(IG_SESSION_FILE)
                cl.login(IG_USERNAME, IG_PASSWORD)
                log.info("✅ Logged in using saved session")
                return cl
            except Exception as e:
                log.warning(f"Session load failed ({e}), doing fresh login...")

        # Fresh login
        cl.login(IG_USERNAME, IG_PASSWORD)
        cl.dump_settings(IG_SESSION_FILE)
        log.info("✅ Logged in fresh, session saved")
        return cl

    except Exception as e:
        log.error(f"Login failed: {e}")
        cl = Client()  # Fallback to unauthenticated
        return cl


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Instagram client on startup."""
    log.info("Starting Instagram media server...")
    init_client()
    yield
    log.info("Server shutting down")


app = FastAPI(
    title="Share2Gemini Instagram Server",
    description="Extracts actual video/image URLs from Instagram using instagrapi",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    url: str


class MediaResponse(BaseModel):
    success: bool
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    media_type: Optional[str] = None  # "video", "image", "album"
    shortcode: Optional[str] = None
    error: Optional[str] = None


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "instagrapi": INSTAGRAPI_AVAILABLE,
        "authenticated": bool(IG_USERNAME and IG_PASSWORD),
        "session_exists": os.path.exists(IG_SESSION_FILE),
    }


@app.post("/extract", response_model=MediaResponse)
async def extract_post(request: ExtractRequest):
    return await _extract(request.url)


@app.get("/extract", response_model=MediaResponse)
async def extract_get(url: str = Query(..., description="Instagram URL")):
    return await _extract(url)


async def _extract(url: str) -> MediaResponse:
    """Core extraction logic using instagrapi."""
    if not INSTAGRAPI_AVAILABLE:
        return MediaResponse(
            success=False,
            error="instagrapi not installed. Run: pip install instagrapi"
        )

    shortcode = get_shortcode(url)
    if not shortcode:
        return MediaResponse(success=False, error=f"Could not parse Instagram shortcode from: {url}")

    log.info(f"Extracting media for shortcode: {shortcode}")

    try:
        # Convert shortcode → media PK → media info
        media_pk = cl.media_pk_from_code(shortcode)
        media = cl.media_info(media_pk)

        media_type_num = media.media_type  # 1=photo, 2=video, 8=album
        media_type_str = {1: "image", 2: "video", 8: "album"}.get(media_type_num, "unknown")

        log.info(f"Media type: {media_type_str} (pk={media_pk})")

        if media_type_num == 2:
            # Video post / Reel
            video_url = str(media.video_url) if media.video_url else None
            thumbnail = str(media.thumbnail_url) if media.thumbnail_url else None
            return MediaResponse(
                success=bool(video_url),
                url=video_url,
                thumbnail_url=thumbnail,
                media_type="video",
                shortcode=shortcode,
                error=None if video_url else "No video URL found"
            )

        elif media_type_num == 1:
            # Image post
            image_url = str(media.thumbnail_url) if media.thumbnail_url else None
            return MediaResponse(
                success=bool(image_url),
                url=image_url,
                thumbnail_url=image_url,
                media_type="image",
                shortcode=shortcode,
            )

        elif media_type_num == 8:
            # Album — return first video if available, else first image
            resources = media.resources or []
            for resource in resources:
                if resource.media_type == 2 and resource.video_url:
                    return MediaResponse(
                        success=True,
                        url=str(resource.video_url),
                        thumbnail_url=str(resource.thumbnail_url) if resource.thumbnail_url else None,
                        media_type="video",
                        shortcode=shortcode,
                    )
            # No video found, return first image
            for resource in resources:
                if resource.thumbnail_url:
                    return MediaResponse(
                        success=True,
                        url=str(resource.thumbnail_url),
                        media_type="image",
                        shortcode=shortcode,
                    )
            return MediaResponse(success=False, error="Album has no downloadable media", shortcode=shortcode)

        else:
            return MediaResponse(success=False, error=f"Unsupported media type: {media_type_num}", shortcode=shortcode)

    except LoginRequired:
        log.error("Instagram requires login for this content")
        return MediaResponse(success=False, error="Instagram login required. Set IG_USERNAME and IG_PASSWORD env vars.")
    except MediaNotFound:
        return MediaResponse(success=False, error="Media not found — post may be deleted or private")
    except PleaseWaitFewMinutes:
        return MediaResponse(success=False, error="Instagram rate limit — please wait a few minutes")
    except ClientError as e:
        log.error(f"Instagram client error: {e}")
        return MediaResponse(success=False, error=f"Instagram error: {e}")
    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        return MediaResponse(success=False, error=f"Server error: {e}")


if __name__ == "__main__":
    import uvicorn
    log.info(f"🚀 Starting Instagram media server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
