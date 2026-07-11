from fastapi import APIRouter, HTTPException, Depends, status, Request
from typing import List, Optional
from models import ChatbotConfig, ChatbotConfigCreate, ChatbotConfigUpdate, User, IngestRequest, ScrapedPage
from services.rag_service import rag_service
from services.audit_service import log_action
from core.auth import get_current_user
from core.database import get_database
from core.csrf_protection import validate_csrf_token
from routers.permissions import check_permission, get_user_chatbots
from bson import ObjectId
from pymongo import ReturnDocument
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)
from slowapi import Limiter
from slowapi.util import get_remote_address
from services.crawler_service import WebsiteCrawler
from pydantic import BaseModel, field_validator
from urllib.parse import urlparse
import ipaddress
import socket
import os
import re
import uuid

router = APIRouter(prefix="/chatbot", tags=["Chatbot Configuration"])
limiter = Limiter(key_func=get_remote_address)

@router.post("/", response_model=ChatbotConfig)
async def create_chatbot_config(
    config: ChatbotConfigCreate,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    # Only the platform administrator (superuser) can create chatbots
    if not getattr(current_user, 'is_superuser', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the platform administrator can create chatbots."
        )

    db = get_database()
    
    config_dict = config.model_dump()
    # Coerce optional dict fields to their response-model defaults so
    # response_model validation does not 500 on freshly-created bots.
    if config_dict.get("position_style") is None:
        config_dict["position_style"] = {"bottom": "20px", "right": "20px"}
    config_dict["user_id"] = str(current_user.id)
    config_dict["api_key"] = str(uuid.uuid4())  # Auto-generate API key
    config_dict["crawl_status"] = "idle"
    config_dict["created_at"] = datetime.utcnow()
    config_dict["updated_at"] = datetime.utcnow()
    
    result = await db.chatbot_configs.insert_one(config_dict)
    config_dict["_id"] = str(result.inserted_id)

    # Audit log
    await log_action(
        action="chatbot:created",
        performed_by=current_user.email,
        chatbot_id=config_dict["_id"],
        details={"name": config_dict.get("name")},
    )

    return config_dict

@router.get("/", response_model=List[ChatbotConfig])
async def get_chatbot_configs(current_user: User = Depends(get_current_user)):
    """
    Get all chatbots the user has access to (owned + shared).
    """
    db = get_database()
    
    configs = []
    
    # Get owned chatbots
    async for config in db.chatbot_configs.find({"user_id": str(current_user.id)}):
        config["_id"] = str(config["_id"])
        config["is_owner"] = True
        config["permission_level"] = "owner"
        configs.append(config)
    
    # Get shared chatbots
    async for permission in db.chatbot_permissions.find({"user_email": current_user.email}):
        chatbot_id = permission["chatbot_id"]
        config = await db.chatbot_configs.find_one({"_id": ObjectId(chatbot_id)})
        if config:
            config["_id"] = str(config["_id"])
            config["is_owner"] = False
            config["permission_level"] = permission["permission_level"]
            config["granted_by"] = permission.get("granted_by")
            config["granted_at"] = permission.get("granted_at")
            configs.append(config)
    
    return configs

@router.get("/{chatbot_id}", response_model=ChatbotConfig)
async def get_chatbot_config(
    chatbot_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific chatbot config (if user has access).
    """
    # Check permission (any level)
    perm_check = await check_permission(chatbot_id, current_user.email)
    
    config = perm_check["chatbot"]
    config["_id"] = str(config["_id"])
    config["is_owner"] = perm_check["is_owner"]
    config["permission_level"] = perm_check["permission_level"]
    
    return config

# custom_css is owner-authored but injected into the widget on third-party host
# pages, so it's an attack surface (CSS can exfiltrate DOM data via remote
# url()/@import, or overlay phishing UI). Strip the weaponizable constructs while
# leaving normal styling intact. Applied on write AND on the public read (so
# legacy stored values are cleaned too). The complete fix is rendering the widget
# in a Shadow DOM/iframe — tracked separately.
CUSTOM_CSS_MAX_LEN = 20000


def sanitize_custom_css(css: Optional[str]) -> Optional[str]:
    if not css:
        return css
    css = css[:CUSTOM_CSS_MAX_LEN]
    # @import can fetch remote stylesheets (beacon / exfil) — drop it.
    css = re.sub(r"@import\b[^;]*;?", "", css, flags=re.IGNORECASE)
    # IE/legacy script-via-CSS vectors.
    css = re.sub(r"expression\s*\(", "void(", css, flags=re.IGNORECASE)
    css = re.sub(r"(behavior|-moz-binding)\s*:", "x-blocked:", css, flags=re.IGNORECASE)
    css = re.sub(r"(javascript|vbscript)\s*:", "blocked:", css, flags=re.IGNORECASE)

    # Neutralize remote url() (attribute-selector exfil / IP beacon). Allow only
    # data: URIs and relative paths; blank out anything pointing off-origin.
    def _url_repl(m: "re.Match") -> str:
        inner = m.group(1).strip().strip("'\"").lower()
        if inner.startswith("data:") or inner == "" :
            return m.group(0)
        if inner.startswith(("http://", "https://", "//")):
            return "url()"
        return m.group(0)

    css = re.sub(r"url\(\s*([^)]*)\)", _url_repl, css, flags=re.IGNORECASE)
    # Defense in depth against <style> breakout (the widget uses textContent,
    # which already prevents this, but keep the stored value clean).
    css = css.replace("</", "\\3C ")
    return css


# Fields the widget legitimately needs to render itself. Everything else
# (system_prompt, supplemental_info, scraped_url, api_key, user_id, internal
# stats, crawl/rescrape config…) is tenant-internal and MUST NOT be exposed on
# this unauthenticated endpoint.
PUBLIC_CONFIG_FIELDS = (
    "name", "welcome_message", "suggested_questions", "initial_questions",
    "window_title", "primary_color", "secondary_color", "background_color",
    "user_message_color", "bot_message_color", "icon_url", "bot_avatar_url",
    "position", "position_style", "button_size", "transparent_background",
    "widget_width", "widget_height", "border_radius", "message_bubble_radius",
    "font_size", "theme", "entry_animation", "custom_css", "collect_feedback",
    "require_email", "in_chat_email_capture",
)


@router.get("/public/{chatbot_id}")
@limiter.limit("20/minute")  # Rate limit: 20 requests per minute for public endpoint
async def get_public_chatbot_config(request: Request, chatbot_id: str):
    db = get_database()

    if not ObjectId.is_valid(chatbot_id):
        raise HTTPException(status_code=400, detail="Invalid chatbot ID")

    config = await db.chatbot_configs.find_one({
        "_id": ObjectId(chatbot_id)
    })

    if not config:
        raise HTTPException(status_code=404, detail="Chatbot configuration not found")

    # ── Domain Whitelist Check for widget embed ─────────────────────────
    from routers.chat import _is_origin_allowed
    allowed_domains = config.get("allowed_domains", [])
    if allowed_domains:
        origin = request.headers.get("origin", "")
        referer = request.headers.get("referer", "")
        dev_origins = {"http://localhost:3000", "http://localhost:3001", "http://localhost:3002"}
        if origin not in dev_origins and not _is_origin_allowed(origin, referer, allowed_domains):
            raise HTTPException(
                status_code=403,
                detail=f"Domain not authorized. This widget is only allowed on: {', '.join(allowed_domains)}"
            )
    # ──────────────────────────────────────────────────────────────────

    # Return ONLY public-safe rendering fields (whitelist) — never the full
    # document. Prevents leaking system_prompt / supplemental_info / api_key /
    # scraped_url / owner identity to anyone who knows the chatbot id.
    public_config = {"id": str(config["_id"])}
    for field in PUBLIC_CONFIG_FIELDS:
        if field in config:
            public_config[field] = config[field]
    # Sanitize legacy custom_css that may predate write-time sanitization.
    if public_config.get("custom_css"):
        public_config["custom_css"] = sanitize_custom_css(public_config["custom_css"])
    return public_config

@router.put("/{chatbot_id}", response_model=ChatbotConfig)
async def update_chatbot_config(
    chatbot_id: str,
    config_update: ChatbotConfigUpdate,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """
    Update chatbot config (requires EDITOR permission or higher).
    """
    from models import PermissionLevel

    # Check permission (editor or higher)
    perm = await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)

    db = get_database()

    # Only update fields that are provided (non-null partial update).
    update_data = {k: v for k, v in config_update.model_dump().items() if v is not None}

    # Security-sensitive fields are owner/admin-only. allowed_domains is the
    # widget's tenant-isolation boundary, ai_model drives provider/cost, and
    # custom_css is injected into third-party pages — a mere EDITOR must not be
    # able to widen the domain allowlist or inject CSS. Strip them for non-admins.
    is_admin = perm.get("is_owner") or perm.get("permission_level") == PermissionLevel.ADMIN
    if not is_admin:
        for f in ("allowed_domains", "ai_model", "custom_css"):
            update_data.pop(f, None)

    # Sanitize owner-authored CSS before it is stored (see sanitize_custom_css).
    if "custom_css" in update_data:
        update_data["custom_css"] = sanitize_custom_css(update_data["custom_css"])

    # Allow EXPLICITLY clearing nullable scheduling fields back to null
    # (e.g. emptying the re-scrape interval to disable auto re-scraping).
    # Without this, a cleared field is filtered out above and keeps its old value.
    for field in ("rescrape_interval_hours",):
        if field in config_update.model_fields_set:
            update_data[field] = getattr(config_update, field)

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_data["updated_at"] = datetime.utcnow()
    
    result = await db.chatbot_configs.update_one(
        {"_id": ObjectId(chatbot_id)},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Chatbot configuration not found")
    
    updated_config = await db.chatbot_configs.find_one({"_id": ObjectId(chatbot_id)})
    updated_config["_id"] = str(updated_config["_id"])
    return updated_config

@router.delete("/{chatbot_id}")
async def delete_chatbot_config(
    chatbot_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)  # CSRF protection
):
    """
    Delete chatbot (only owner can delete).
    """
    # Check permission (must be owner)
    perm_check = await check_permission(chatbot_id, current_user.email)
    
    if not perm_check["is_owner"]:
        raise HTTPException(status_code=403, detail="Only the owner can delete a chatbot")
    
    db = get_database()

    result = await db.chatbot_configs.delete_one({
        "_id": ObjectId(chatbot_id),
        "user_id": str(current_user.id)
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Chatbot configuration not found")

    # Cascade delete all data keyed on this chatbot so deleting a bot never
    # leaves orphaned conversations, leads, pages, costs or vector chunks.
    # (chatbot_permissions is intentionally NOT included via $in below because
    # it is keyed by chatbot_id too — it is in the list.)
    related_collections = [
        "conversations", "message_feedback", "leads", "scraped_pages",
        "documents", "page_versions", "content_gaps", "llm_costs",
        "quality_scores", "conversation_tags", "rag_chunks",
        "change_alerts", "chatbot_permissions",
    ]
    for coll in related_collections:
        try:
            res = await db[coll].delete_many({"chatbot_id": chatbot_id})
            if res.deleted_count:
                logger.info(f"Cascade delete: removed {res.deleted_count} from {coll} for chatbot {chatbot_id}")
        except Exception as e:
            # Never fail the whole delete because one cleanup query errored.
            logger.warning(f"Cascade delete failed for collection {coll} (chatbot {chatbot_id}): {e}")

    # Drop the ChromaDB vector collection (chatbot_{id}); safe if it never existed.
    try:
        await asyncio.to_thread(rag_service.delete_collection, chatbot_id)
    except Exception as e:
        logger.warning(f"Cascade delete: ChromaDB collection drop failed for chatbot {chatbot_id}: {e}")

    # Audit log
    await log_action(
        action="chatbot:deleted",
        performed_by=current_user.email,
        chatbot_id=chatbot_id,
        details={"name": perm_check["chatbot"].get("name")},
    )

    return {"message": "Chatbot configuration deleted successfully"}

@router.post("/{chatbot_id}/ingest", response_model=dict)
async def ingest_documents(
    chatbot_id: str,
    ingest_request: IngestRequest,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """
    Ingest documents into chatbot (requires EDITOR permission or higher).
    """
    from models import PermissionLevel
    
    # Check permission (editor or higher)
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)
        
    try:
        # Offload CPU-bound embedding work so it doesn't block the event loop
        count = await asyncio.to_thread(rag_service.add_documents, chatbot_id, ingest_request.text)
        return {"message": f"Successfully ingested {count} chunks from text."}
    except Exception as e:
        logger.error(f"Error ingesting documents for chatbot {chatbot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error ingesting documents.")

# SSRF protection: blocked schemes, private IP ranges
BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data", "javascript", "mailto"}

def is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP address."""
    try:
        resolved_ips = socket.getaddrinfo(hostname, None)
        for entry in resolved_ips:
            ip = ipaddress.ip_address(entry[4][0])
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return True
    except (socket.gaierror, ValueError) as e:
        logger.debug(f"DNS resolution failed for {hostname}: {e}")
    return False

def validate_crawl_url(url: str) -> str:
    """Validate URL to prevent SSRF attacks."""
    parsed = urlparse(url)
    
    # Block dangerous schemes
    if parsed.scheme.lower() in BLOCKED_SCHEMES or parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Scheme '{parsed.scheme}' is not allowed. Only http and https are permitted.")
    
    # Block empty or missing hostname
    if not parsed.hostname:
        raise ValueError("URL must have a valid hostname.")
    
    hostname = parsed.hostname.lower()
    
    # Check if running in development mode
    is_development = os.getenv("ENV", "production").lower() == "development"
    
    # Allow host.docker.internal for Docker host access
    if hostname == "host.docker.internal":
        return url
    
    # Allow localhost/127.0.0.1 in development mode only
    if is_development:
        allowed_dev_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
        if hostname in allowed_dev_hosts:
            return url
    
    # Block internal hostnames in production
    internal_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal", "169.254.169.254"}
    if hostname in internal_hosts:
        raise ValueError(f"Hostname '{hostname}' is not allowed in production. Use 'host.docker.internal' for Docker or a public domain.")
    
    # Block private IP ranges ALWAYS (defense-in-depth, independent of ENV).
    # Previously this was skipped entirely in development mode, which on a
    # public deployment running ENV=development left the crawler open to SSRF
    # against the internal network (10.x/192.168.x/172.16.x), cloud metadata,
    # and loopback. The literal-localhost dev escape hatch above is the only
    # exception kept for local development; everything else is blocked.
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise ValueError(f"IP address '{hostname}' is not allowed (private/reserved range).")
    except ValueError as e:
        # ip_address() raises ValueError for non-IP strings; re-raise our own
        # block message, otherwise treat it as a domain and check DNS.
        if "not allowed" in str(e):
            raise
        if is_private_ip(hostname):
            raise ValueError(f"Hostname '{hostname}' resolves to a private IP address.")

    return url

class CrawlRequest(BaseModel):
    url: str
    # None means "no limit" (crawl entire site / sitemap)
    max_pages: Optional[int] = None
    url_list: List[str] = None  # Optional list of specific URLs to crawl

    @field_validator('url')
    @classmethod
    def url_must_be_safe(cls, v):
        return validate_crawl_url(v)

    @field_validator('max_pages')
    @classmethod
    def max_pages_limit(cls, v):
        # Allow None for unlimited; otherwise ensure positive integer
        if v is None:
            return v
        if v < 1:
            raise ValueError('max_pages must be 1 or greater, or null for unlimited')
        return v
    
    @field_validator('url_list')
    @classmethod
    def validate_url_list(cls, v):
        if v is not None:
            if len(v) > 200:
                raise ValueError('url_list cannot contain more than 200 URLs')
            # Validate each URL in the list
            for url in v:
                validate_crawl_url(url)
        return v

@router.post("/{chatbot_id}/crawl", response_model=dict)
async def crawl_website(
    chatbot_id: str,
    crawl_request: CrawlRequest,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token)
):
    """
    Crawl website (requires EDITOR permission or higher).
    """
    from models import PermissionLevel
    
    # Check permission (editor or higher)
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)
    
    db = get_database()

    # Prevent duplicate crawls — ATOMIC check-and-set so two concurrent requests
    # (e.g. a double-clicked "Scrape" button, or several tabs) can't both pass a
    # separate read and each enqueue a crawl. find_one_and_update only matches
    # when the chatbot is NOT already crawling, so exactly one request wins; the
    # losers match nothing (claimed is None) and get a 409. A plain
    # find-then-update here was a TOCTOU race that started N parallel crawls.
    claimed = await db.chatbot_configs.find_one_and_update(
        {"_id": ObjectId(chatbot_id), "crawl_status": {"$ne": "crawling"}},
        {"$set": {
            "scraped_url": crawl_request.url,
            "crawl_status": "crawling",
            "crawl_pages_done": 0,
            "crawl_pages_total": crawl_request.max_pages,
            "crawl_error": None,
            "updated_at": datetime.utcnow(),
        }},
        return_document=ReturnDocument.BEFORE,
    )
    if claimed is None:
        # Either the chatbot doesn't exist, or a crawl is already running.
        raise HTTPException(status_code=409, detail="Un scraping est déjà en cours pour ce chatbot. Veuillez attendre qu'il se termine.")

    # Backpressure: refuse with 429 if the crawl queue is already saturated.
    from core.crawl_governance import check_crawl_capacity
    can_accept, retry_after = check_crawl_capacity()
    if not can_accept:
        # Roll back to the prior status so the UI doesn't show a stuck spinner.
        await db.chatbot_configs.update_one(
            {"_id": ObjectId(chatbot_id)},
            {"$set": {"crawl_status": claimed.get("crawl_status", "idle")}},
        )
        raise HTTPException(
            status_code=429,
            detail="Le système est saturé : trop de scrapings en attente. Réessayez dans un instant.",
            headers={"Retry-After": str(retry_after)},
        )

    # Trigger Celery Task
    from worker import celery_app
    task = celery_app.send_task(
        "services.crawler_service.crawl_website_task",
        args=[chatbot_id, crawl_request.url, crawl_request.max_pages],
        kwargs={'url_list': crawl_request.url_list} if crawl_request.url_list else {},
        queue="crawl",
    )
    
    if crawl_request.url_list:
        return {"message": f"Started crawling {len(crawl_request.url_list)} specific URLs. Task ID: {task.id}"}
    else:
        return {"message": f"Started crawling {crawl_request.url} with auto-discovery. Task ID: {task.id}"}


@router.get("/{chatbot_id}/crawl-status", response_model=dict)
async def get_crawl_status(
    chatbot_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get the current crawl status for a chatbot (any permission level)."""
    # Check permission (any level)
    perm_check = await check_permission(chatbot_id, current_user.email)
    
    chatbot = perm_check["chatbot"]

    return {
        "crawl_status": chatbot.get("crawl_status", "idle"),
        "crawl_pages_done": chatbot.get("crawl_pages_done", 0),
        "crawl_pages_total": chatbot.get("crawl_pages_total", 0),
        "crawl_current_url": chatbot.get("crawl_current_url"),
        "crawl_started_at": chatbot.get("crawl_started_at"),
        "crawl_completed_at": chatbot.get("crawl_completed_at"),
        "crawl_error": chatbot.get("crawl_error"),
        "last_scraped_at": chatbot.get("last_scraped_at"),
    }

@router.get("/{chatbot_id}/scraped-pages", response_model=List[ScrapedPage])
async def get_scraped_pages(
    chatbot_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get the list of successfully scraped pages for a chatbot (any permission level)."""
    # Check permission (any level)
    await check_permission(chatbot_id, current_user.email)
    
    db = get_database()

    pages = []
    async for page in db.scraped_pages.find({"chatbot_id": chatbot_id}).sort("scraped_at", -1):
        page["_id"] = str(page["_id"])
        pages.append(page)
    
    return pages


@router.delete("/{chatbot_id}/scraped-pages/{page_id}")
async def delete_scraped_page(
    chatbot_id: str,
    page_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Delete a single scraped page (requires editor+ permission)."""
    from models import PermissionLevel
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)

    if not ObjectId.is_valid(page_id):
        raise HTTPException(status_code=400, detail="Invalid page ID")

    db = get_database()
    result = await db.scraped_pages.delete_one(
        {"_id": ObjectId(page_id), "chatbot_id": chatbot_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Scraped page not found")

    # Also remove the page content from the vector store
    try:
        page_doc = await db.scraped_pages.find_one({"_id": ObjectId(page_id)})
        # No need — already deleted. Just log.
        logger.info(f"Deleted scraped page {page_id} from chatbot {chatbot_id}")
    except Exception as e:
        logger.warning(f"Post-delete cleanup warning: {e}")

    return {"message": "Scraped page deleted successfully"}


# ── Documents Management ──────────────────────────────────────────────────────

@router.get("/{chatbot_id}/documents")
async def list_documents(
    chatbot_id: str,
    current_user: User = Depends(get_current_user),
):
    """List all uploaded documents for a chatbot."""
    await check_permission(chatbot_id, current_user.email)
    db = get_database()

    docs = []
    async for doc in db.documents.find({"chatbot_id": chatbot_id}).sort("uploaded_at", -1):
        doc["_id"] = str(doc["_id"])
        docs.append(doc)
    return docs


@router.delete("/{chatbot_id}/documents/{doc_id}")
async def delete_document(
    chatbot_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    _: bool = Depends(validate_csrf_token),
):
    """Delete an uploaded document and remove it from the RAG knowledge base."""
    from models import PermissionLevel
    await check_permission(chatbot_id, current_user.email, required_level=PermissionLevel.EDITOR)

    if not ObjectId.is_valid(doc_id):
        raise HTTPException(status_code=400, detail="Invalid document ID")

    db = get_database()
    doc = await db.documents.find_one({"_id": ObjectId(doc_id), "chatbot_id": chatbot_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove from disk
    import os as _os
    file_path = _os.path.join("uploads", "documents", doc["filename"])
    try:
        if _os.path.exists(file_path):
            _os.remove(file_path)
    except Exception as e:
        logger.warning(f"Could not delete file {file_path}: {e}")

    # Remove chunks from ChromaDB by source filename
    # Offload to a thread — delete_by_source touches ChromaDB synchronously.
    try:
        await asyncio.to_thread(rag_service.delete_by_source, chatbot_id, doc["original_name"])
    except Exception as e:
        logger.warning(f"RAG delete_by_source failed: {e}")

    # Remove metadata from MongoDB
    await db.documents.delete_one({"_id": ObjectId(doc_id)})

    return {"message": "Document deleted successfully"}

