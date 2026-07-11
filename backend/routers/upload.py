from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends
from models import User
from core.auth import get_current_user
from core.logging_config import security_logger
from core.csrf_protection import validate_csrf_token
import asyncio
import shutil
import os
from uuid import uuid4
from datetime import datetime
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["Upload"])
limiter = Limiter(key_func=get_remote_address)

UPLOAD_DIR = "uploads"
# Ensure directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Security settings
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
# NOTE: legacy binary ".doc" is intentionally NOT allowed — text extraction uses
# python-docx which only handles ".docx" (Office Open XML). Advertising ".doc"
# would let users upload files that always fail extraction.
ALLOWED_EXTENSIONS_DOC = {".pdf", ".txt", ".docx"}
MAX_DOC_SIZE = 50 * 1024 * 1024  # 50MB
UPLOAD_DOC_DIR = os.path.join(UPLOAD_DIR, "documents")
os.makedirs(UPLOAD_DOC_DIR, exist_ok=True)

# Magic numbers for file type validation (prevents file type spoofing)
MAGIC_NUMBERS = {
    b'\xFF\xD8\xFF': 'jpg',  # JPEG
    b'\x89PNG\r\n\x1a\n': 'png',  # PNG
    b'GIF87a': 'gif',  # GIF87a
    b'GIF89a': 'gif',  # GIF89a
    b'RIFF': 'webp',  # WEBP (also checks for WEBP signature later)
}

def validate_file_type(file_content: bytes, file_ext: str) -> bool:
    """Validate file type by checking magic numbers (file signature)"""
    if not file_content:
        return False
    
    # Check magic numbers
    for magic, file_type in MAGIC_NUMBERS.items():
        if file_content.startswith(magic):
            # For WEBP, also verify the WEBP signature
            if magic == b'RIFF' and len(file_content) >= 12:
                if file_content[8:12] == b'WEBP':
                    return file_ext.lower() in ['.webp']
                return False
            # Match file extension with detected type
            return file_ext.lower() in [f'.{file_type}', '.jpeg'] if file_type == 'jpg' else file_ext.lower() == f'.{file_type}'
    
    return False

@router.post("/image")
@limiter.limit("10/minute")  # Rate limit: 10 uploads per minute
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),  # Require authentication
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    # Validate content type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Validate file extension
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS_IMAGE:
        raise HTTPException(
            status_code=400,
            detail=f"File extension not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS_IMAGE)}"
        )
    
    # Read file content to validate size and type
    file_content = await file.read()
    file_size = len(file_content)
    
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.1f}MB"
        )
    
    if file_size == 0:
        raise HTTPException(status_code=400, detail="File is empty")
    
    # Validate file type by magic number (prevents spoofing)
    if not validate_file_type(file_content, file_ext):
        client_ip = request.client.host if request.client else "unknown"
        security_logger.log_file_upload_rejected(
            current_user.email,
            client_ip,
            "Invalid file type - magic number mismatch"
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. File content doesn't match the extension."
        )
    
    # Generate unique filename
    if not file_ext:
        file_ext = ".jpg"
        
    filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    try:
        # Write the already-read content
        with open(file_path, "wb") as buffer:
            buffer.write(file_content)
        
        # Log successful upload
        client_ip = request.client.host if request.client else "unknown"
        security_logger.log_file_upload(
            current_user.email,
            client_ip,
            filename,
            file_size
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Could not save file")
        
    # Construct full URL
    base_url = str(request.base_url).rstrip("/")
    file_url = f"{base_url}/uploads/{filename}"
    
    return {"url": file_url}

@router.post("/document")
@limiter.limit("30/minute")
async def upload_document(
    request: Request,
    chatbot_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    from services.rag_service import rag_service
    from core.database import get_database
    import pypdf
    import io

    # Per-chatbot authorization: only the owner or a collaborator with
    # EDITOR/ADMIN permission may ingest a document into a chatbot. Also
    # validates that the chatbot exists (404) and the id is well-formed (400).
    if chatbot_id and chatbot_id != "undefined":
        from routers.permissions import check_permission
        from models import PermissionLevel
        await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)

    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS_DOC:
        raise HTTPException(status_code=400, detail=f"Unsupported document type. Allowed: {', '.join(ALLOWED_EXTENSIONS_DOC)}")

    file_content = await file.read()
    file_size = len(file_content)
    if file_size > MAX_DOC_SIZE:
        raise HTTPException(status_code=400, detail=f"Document too large. Maximum allowed size is {MAX_DOC_SIZE // (1024 * 1024)} MB.")

    extracted_text = ""
    if file_ext == ".pdf":
        try:
            pdf_reader = pypdf.PdfReader(io.BytesIO(file_content))
            for page in pdf_reader.pages:
                extracted_text += page.extract_text() + "\n"
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            raise HTTPException(status_code=400, detail="Could not extract text from PDF.")
    elif file_ext == ".txt":
        try:
            extracted_text = file_content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                extracted_text = file_content.decode("latin-1")
            except Exception:
                raise HTTPException(status_code=400, detail="Unsupported text encoding.")
    elif file_ext in (".docx", ".doc"):
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(io.BytesIO(file_content))
            extracted_text = "\n".join(par.text for par in doc.paragraphs if par.text)
        except Exception as e:
            logger.error(f"DOCX extraction error: {e}")
            raise HTTPException(status_code=400, detail="Could not extract text from .docx (legacy .doc files are not supported; please convert to .docx or .pdf).")
    else:
        raise HTTPException(status_code=400, detail="Unsupported document type.")

    if not extracted_text.strip():
        raise HTTPException(status_code=400, detail="The document appears to be empty or unreadable.")

    # Save file to disk
    filename = f"{uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DOC_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(file_content)

    base_url = str(request.base_url).rstrip("/")
    file_url = f"{base_url}/uploads/documents/{filename}"

    # Ingest into RAG
    chunks_count = 0
    if chatbot_id and chatbot_id != "undefined":
        try:
            metadata = {
                "source": file.filename,
                "type": "document",
                "filename": filename,
                "uploaded_at": datetime.utcnow().isoformat()
            }
            # Offload CPU-bound embedding work so it doesn't block the event loop
            chunks_count = await asyncio.to_thread(
                rag_service.add_documents, chatbot_id, extracted_text, metadata
            )
        except Exception as e:
            logger.error(f"Ingestion error: {e}")

    # Persist document metadata in MongoDB
    doc_meta = {
        "chatbot_id": chatbot_id,
        "user_id": str(current_user.id),
        "original_name": file.filename,
        "filename": filename,
        "file_url": file_url,
        "file_size": file_size,
        "file_ext": file_ext,
        "chunks_count": chunks_count,
        "uploaded_at": datetime.utcnow(),
    }
    db = get_database()
    result = await db.documents.insert_one(doc_meta)
    document_id = str(result.inserted_id)

    return {
        "document_id": document_id,
        "url": file_url,
        "name": file.filename,
        "chunks_count": chunks_count,
        "message": "Document uploaded and processed successfully."
    }

