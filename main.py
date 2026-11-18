import asyncio
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PHOTOS_DIR = STATIC_DIR / "photos"
TEMPLATE_FILE = BASE_DIR / "templates" / "index.html"
USERS_FILE = BASE_DIR / "users.json"
SESSION_COOKIE = "jaipur_memories_session"
SESSION_DURATION = timedelta(hours=12)
MAX_UPLOAD_SIZE = 15 * 1024 * 1024
ALLOWED_IMAGE_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}


def load_users() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load valid user credentials from {USERS_FILE}") from exc

    entries = data.get("users") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError('users.json must contain a "users" array')

    users: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Each users.json entry must be an object")

        username = entry.get("username")
        password = entry.get("password")
        role = entry.get("role")
        if (
            not isinstance(username, str)
            or not username.strip()
            or not isinstance(password, str)
            or not password
            or role not in {"admin", "uploader"}
        ):
            raise RuntimeError("Each user must have a username, password, and valid role")

        key = username.casefold()
        if key in users:
            raise RuntimeError(f"Duplicate username in users.json: {username}")
        users[key] = {"username": username, "password": password, "role": role}

    if not users:
        raise RuntimeError("users.json must define at least one user")
    return users


USERS = load_users()
SESSIONS: dict[str, tuple[str, datetime]] = {}
SESSION_LOCK = asyncio.Lock()

app = FastAPI(title="Jaipur Memories")


class LoginRequest(BaseModel):
    username: str
    password: str


def current_user(request: Request) -> dict[str, str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in")

    session = SESSIONS.get(token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    username_key, expires_at = session
    if datetime.now(timezone.utc) >= expires_at:
        SESSIONS.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = USERS.get(username_key)
    if not user:
        SESSIONS.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


def require_uploader(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    if user["role"] not in {"admin", "uploader"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Upload permission required")
    return user


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    if not TEMPLATE_FILE.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gallery page is not installed")
    return FileResponse(TEMPLATE_FILE)


@app.post("/login", include_in_schema=False)
@app.post("/api/login")
async def login(credentials: LoginRequest) -> JSONResponse:
    user = USERS.get(credentials.username.strip().casefold())
    valid_password = user and hmac.compare_digest(user["password"], credentials.password)
    if not valid_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + SESSION_DURATION
    async with SESSION_LOCK:
        expired_tokens = [key for key, (_, deadline) in SESSIONS.items() if deadline <= datetime.now(timezone.utc)]
        for key in expired_tokens:
            SESSIONS.pop(key, None)
        SESSIONS[token] = (credentials.username.strip().casefold(), expiry)

    response = {"username": user["username"], "role": user["role"]}
    result = JSONResponse(response)
    result.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_DURATION.total_seconds()),
        httponly=True,
        secure=os.environ.get("COOKIE_SECURE", "").casefold() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )
    return result


@app.post("/logout", include_in_schema=False)
@app.post("/api/logout")
async def logout(request: Request) -> JSONResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        SESSIONS.pop(token, None)
    result = JSONResponse({"message": "Logged out"})
    result.delete_cookie(key=SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return result


@app.get("/me", include_in_schema=False)
@app.get("/api/me")
async def who_am_i(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    return {"username": user["username"], "role": user["role"]}


@app.get("/photos", include_in_schema=False)
@app.get("/api/photos")
async def list_photos(_: dict[str, str] = Depends(current_user)) -> list[dict[str, str]]:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    photos = [
        path for path in PHOTOS_DIR.iterdir()
        if path.is_file() and path.suffix.casefold() in ALLOWED_IMAGE_SIGNATURES
    ]
    photos.sort(key=lambda path: path.name.casefold())
    return [
        {"filename": path.name, "url": f"/static/photos/{quote(path.name)}"}
        for path in photos
    ]


@app.post("/upload", status_code=status.HTTP_201_CREATED, include_in_schema=False)
@app.post("/api/upload", status_code=status.HTTP_201_CREATED)
async def upload_photo(
    file: UploadFile = File(...),
    _: dict[str, str] = Depends(require_uploader),
) -> dict[str, str]:
    original_name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    extension = Path(original_name).suffix.casefold()
    if extension not in ALLOWED_IMAGE_SIGNATURES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use a JPG, PNG, GIF, or WEBP image")

    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(original_name).stem).strip("-_")[:80]
    safe_stem = safe_stem or "photo"
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid4().hex}_{safe_stem}{extension}"
    temporary_path = PHOTOS_DIR / f".{uuid4().hex}.upload"
    destination = PHOTOS_DIR / unique_name
    size = 0
    signature_sample = bytearray()

    try:
        with temporary_path.open("xb") as output:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image exceeds the 15 MB limit")
                if len(signature_sample) < 16:
                    signature_sample.extend(chunk[: 16 - len(signature_sample)])
                output.write(chunk)

        signatures = ALLOWED_IMAGE_SIGNATURES[extension]
        valid_signature = any(signature_sample.startswith(signature) for signature in signatures)
        if extension == ".webp":
            valid_signature = signature_sample.startswith(b"RIFF") and signature_sample[8:12] == b"WEBP"
        if size == 0 or not valid_signature:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is not a valid image")

        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    return {"filename": unique_name, "url": f"/static/photos/{quote(unique_name)}"}


@app.get("/static/{asset_name}", include_in_schema=False)
async def serve_frontend_asset(asset_name: str) -> FileResponse:
    if asset_name not in {"style.css", "script.js"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    asset_path = STATIC_DIR / asset_name
    if not asset_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return FileResponse(asset_path)


@app.get("/static/photos/{filename}")
async def serve_photo(
    filename: str,
    _: dict[str, str] = Depends(current_user),
) -> FileResponse:
    if Path(filename).name != filename or Path(filename).suffix.casefold() not in ALLOWED_IMAGE_SIGNATURES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    photo_path = PHOTOS_DIR / filename
    if not photo_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return FileResponse(photo_path)
