import hashlib
import os
import uuid
from pathlib import Path
from flask import current_app


ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_SIZE_BYTES = 20 * 1024 * 1024
MAX_SIZE_MB = MAX_SIZE_BYTES // (1024 * 1024)

_EXTENSION = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


class UploadError(Exception):
    """Raised when an upload fails validation. Message is safe to show to the user."""


def _uploads_root() -> Path:
    return Path(current_app.instance_path) / "uploads"


def _user_dir(user_id: int) -> Path:
    return _uploads_root() / str(user_id)


def upload_path(user_id: int, stored_filename: str) -> Path:
    return _user_dir(user_id) / stored_filename


def save_upload(file_storage, user_id: int) -> dict:
    """Validate, hash, and persist an uploaded file. Returns metadata for the DB row."""
    if not file_storage or not file_storage.filename:
        raise UploadError("Please choose a file to upload.")

    original_filename = os.path.basename(file_storage.filename)
    extension = os.path.splitext(original_filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadError(f"Unsupported file type. Allowed: PDF, JPG, PNG.")

    data = file_storage.read()
    if not data:
        raise UploadError("The selected file is empty.")
    if len(data) > MAX_SIZE_BYTES:
        raise UploadError(f"File is too large. Maximum size is {MAX_SIZE_MB} MB.")

    user_dir = _user_dir(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    target = user_dir / stored_filename
    target.write_bytes(data)

    return {
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "mime_type": _EXTENSION[extension],
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def delete_upload(user_id: int, stored_filename: str) -> None:
    """Remove a stored file. Missing files are tolerated — the audit row is the source of truth."""
    try:
        upload_path(user_id, stored_filename).unlink()
    except FileNotFoundError:
        pass
