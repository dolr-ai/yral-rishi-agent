import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form

from auth import get_current_user
from services import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Media"])


@router.post("/media/upload")
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    type: str = Form(...),
):
    user_id = get_current_user(request)

    if type not in ("image", "audio"):
        raise HTTPException(
            status_code=422, detail="Invalid type. Must be 'image' or 'audio'"
        )

    file_bytes = await file.read()
    file_name = file.filename or "upload"
    file_size = len(file_bytes)

    if file_size == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    try:
        if type == "image":
            storage.validate_image(file_name, file_size)
        else:
            storage.validate_audio(file_name, file_size)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    ext = storage._get_extension(file_name)
    content_type = file.content_type or storage.mime_from_extension(ext)

    try:
        storage_key, size = await storage.upload(
            user_id=user_id,
            file_bytes=file_bytes,
            file_extension=ext,
            content_type=content_type,
        )
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        raise HTTPException(status_code=503, detail="Upload failed — please try again")

    presigned_url = storage.generate_presigned_url(storage_key)

    return {
        "url": presigned_url,
        "storage_key": storage_key,
        "type": type,
        "size": size,
        "mime_type": content_type,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
