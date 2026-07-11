from pydantic import BaseModel, Field, EmailStr, field_validator
from pydantic_core import core_schema
from typing import Optional, List, Dict, Any
from datetime import datetime
from bson import ObjectId
import uuid

class PyObjectId(str):
    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler):
        return core_schema.json_or_python_schema(
            json_schema=core_schema.str_schema(),
            python_schema=core_schema.union_schema([
                core_schema.is_instance_schema(ObjectId),
                core_schema.chain_schema([
                    core_schema.str_schema(),
                    core_schema.no_info_plain_validator_function(cls.validate),
                ])
            ]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda x: str(x)
            ),
        )

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if isinstance(v, str) and ObjectId.is_valid(v):
            return ObjectId(v)
        raise ValueError("Invalid ObjectId")

class User(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    email: EmailStr
    username: str
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Password reset
    reset_password_token: Optional[str] = None
    reset_password_token_expires: Optional[datetime] = None
    # Two-factor authentication
    two_factor_enabled: bool = False
    two_factor_secret: Optional[str] = None
    backup_codes: Optional[List[str]] = []
    # Role & permissions
    is_superuser: bool = False
    can_create_chatbot: bool = True
    created_by: Optional[str] = None  # email of superuser who created this user
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str

    @field_validator('username')
    @classmethod
    def username_valid(cls, v):
        import re
        if not v or len(v) < 3 or len(v) > 50:
            raise ValueError('Username must be between 3 and 50 characters')
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Username can only contain letters, numbers, underscores and hyphens')
        return v

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

class TwoFactorSetupResponse(BaseModel):
    secret: str
    qr_code: str  # Base64 encoded QR code image
    backup_codes: List[str]

class TwoFactorVerifyRequest(BaseModel):
    code: str

class TwoFactorLoginRequest(BaseModel):
    email: EmailStr
    password: str
    two_factor_code: str

class ChatbotConfig(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    name: str
    website_url: Optional[str] = None
    system_prompt: str
    primary_color: str = "#3B82F6"
    secondary_color: str = "#1E40AF"
    icon_url: Optional[str] = None
    position: str = "bottom-right"
    position_style: dict = {"bottom": "20px", "right": "20px"}
    button_size: int = 60
    transparent_background: bool = False
    welcome_message: Optional[str] = "Hello! How can I help you today?"
    suggested_questions: Optional[List[str]] = []
    window_title: Optional[str] = "Chat Assistant"
    temperature: Optional[float] = 0.7
    # New design options
    widget_width: Optional[int] = 400
    widget_height: Optional[int] = 600
    background_color: Optional[str] = "#FFFFFF"
    border_radius: Optional[int] = 16
    message_bubble_radius: Optional[int] = 12
    user_message_color: Optional[str] = "#3B82F6"
    bot_message_color: Optional[str] = "#F3F4F6"
    font_size: Optional[int] = 14
    scraped_url: Optional[str] = None
    api_key: Optional[str] = Field(default_factory=lambda: str(uuid.uuid4()))
    theme: str = "light"  # light, dark, glass
    collect_feedback: bool = True
    # Advanced Widget Customization
    custom_css: Optional[str] = None  # Free-form CSS injected into the widget
    entry_animation: str = "slide"  # slide, fade, bounce, none
    bot_avatar_url: Optional[str] = None  # Avatar shown next to bot messages
    # Supplemental Knowledge
    supplemental_info: Optional[str] = None  # Fallback knowledge when RAG has no answer
    # Lead Generation
    require_email: bool = False
    in_chat_email_capture: bool = False
    initial_questions: Optional[List[Dict[str, Any]]] = []
    # Crawl status tracking
    crawl_status: Optional[str] = None  # idle, crawling, completed, failed
    crawl_pages_done: Optional[int] = 0
    crawl_pages_total: Optional[int] = 0
    crawl_current_url: Optional[str] = None
    crawl_started_at: Optional[datetime] = None
    crawl_completed_at: Optional[datetime] = None
    crawl_error: Optional[str] = None
    # Re-scraping scheduler
    rescrape_interval_hours: Optional[int] = None  # null = disabled
    last_scraped_at: Optional[datetime] = None
    # Smart re-scraping settings
    rescrape_strategy: str = "smart"  # simple, smart, aggressive, conservative
    excluded_url_patterns: Optional[List[str]] = []  # ["/blog/archive/*", "*.pdf"]
    included_url_patterns: Optional[List[str]] = []  # If set, ONLY these patterns
    enable_smart_scheduling: bool = True  # Adapt frequency based on page stability
    enable_etag_checking: bool = True  # Use HTTP ETags for change detection
    enable_content_hash: bool = True  # Compare content hash before updating
    max_pages_priority_high: Optional[int] = 20
    max_pages_priority_normal: Optional[int] = 100
    max_pages_priority_low: Optional[int] = 50
    # Global statistics
    total_pages_active: int = 0
    total_pages_pinned: int = 0
    total_pages_excluded: int = 0
    total_bytes_stored: int = 0
    # Security & AI Settings
    allowed_domains: Optional[List[str]] = []
    ignored_urls: Optional[List[str]] = []
    sitemap_url: Optional[str] = None
    ai_model: str = "gemini-2.5-flash-lite"
    documents: Optional[List[Dict[str, Any]]] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # Permission fields (dynamically added by API)
    is_owner: Optional[bool] = None
    permission_level: Optional[str] = None
    granted_by: Optional[str] = None
    granted_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class ScrapedPage(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    url: str
    title: Optional[str] = ""
    char_count: Optional[int] = 0
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "success" # success, failed, skipped
    depth: int = 0
    error: Optional[str] = None
    
    # Enhanced metadata for smart re-scraping
    content_hash: Optional[str] = None  # SHA256 of content for change detection
    http_etag: Optional[str] = None
    http_last_modified: Optional[str] = None
    size_bytes: Optional[int] = None
    
    # Quality metrics
    links_count: Optional[int] = 0
    images_count: Optional[int] = 0
    tokens_estimate: Optional[int] = 0
    
    # Versioning
    version: int = 1  # Increment on each re-scrape
    previous_version_id: Optional[str] = None
    
    # Re-scrape policy reference
    rescrape_policy: Optional[Dict[str, Any]] = None
    last_rescrape_check: Optional[datetime] = None
    skip_reason: Optional[str] = None  # "pinned", "unchanged", "conditional_not_met"
    
    # Quality scoring
    quality_score: Optional[int] = None  # 0-100
    quality_grade: Optional[str] = None  # A+, A, B, C, D, F
    quality_issues: Optional[List[str]] = None
    
    # Health monitoring
    is_broken: bool = False
    http_status: Optional[int] = None
    last_health_check: Optional[datetime] = None
    broken_since: Optional[datetime] = None
    
    # SEO metadata
    seo_metadata: Optional[Dict[str, Any]] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class PageRescrapePolicy(BaseModel):
    """Smart re-scrape policy for individual pages"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    page_id: str  # Reference to ScrapedPage
    url: str
    
    # Re-scrape strategy
    strategy: str = "auto"  # auto, manual, scheduled, never, conditional
    
    # For strategy="never" (pinned pages)
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None
    pinned_by: Optional[str] = None  # user_id
    pin_reason: Optional[str] = None
    
    # For strategy="scheduled" (custom interval)
    custom_interval_hours: Optional[int] = None  # Override global interval
    
    # For strategy="conditional" (intelligent re-scraping)
    rescrape_conditions: Optional[Dict[str, Any]] = None
    
    # Metadata
    priority: int = 0  # -1=low, 0=normal, 1=high
    check_frequency: Optional[str] = "inherit"  # inherit, daily, weekly, monthly, never
    
    # Statistics
    scrape_count: int = 0
    last_change_detected: Optional[datetime] = None
    consecutive_unchanged: int = 0  # Auto-reduce frequency if stable
    
    # Exclusions
    excluded_from_crawl: bool = False
    exclusion_reason: Optional[str] = None
    
    # Change alerts
    alert_on_change: bool = False  # Send notification when page changes
    alert_severity: str = "normal"  # "critical", "important", "normal", "minor"
    alert_emails: Optional[List[str]] = None
    alert_webhooks: Optional[List[str]] = None
    last_alert_sent: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class MessageFeedback(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    conversation_id: str
    message_index: int
    rating: int  # 1 for up, -1 for down
    feedback_text: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class ChatbotConfigCreate(BaseModel):
    name: str = "My Chatbot"
    website_url: Optional[str] = None
    system_prompt: str = "You are a helpful assistant that answers questions based on the content of the current web page."
    primary_color: str = "#3B82F6"
    secondary_color: str = "#1E40AF"
    icon_url: Optional[str] = None
    position: str = "bottom-right"
    position_style: Optional[Dict[str, str]] = None
    button_size: int = 60
    transparent_background: bool = False
    welcome_message: str = "Hello! How can I help you today?"
    suggested_questions: List[str] = []
    window_title: str = "Chat Assistant"
    temperature: float = 0.7
    # New design options
    widget_width: Optional[int] = 400
    widget_height: Optional[int] = 600
    background_color: Optional[str] = "#FFFFFF"
    border_radius: Optional[int] = 16
    message_bubble_radius: Optional[int] = 12
    user_message_color: Optional[str] = "#3B82F6"
    bot_message_color: Optional[str] = "#F3F4F6"
    font_size: Optional[int] = 14
    rescrape_interval_hours: Optional[int] = None  # null = disabled
    allowed_domains: List[str] = []
    ignored_urls: List[str] = []
    sitemap_url: Optional[str] = None
    ai_model: str = "gemini-2.5-flash-lite"
    documents: List[Dict[str, Any]] = []
    theme: str = "light"
    collect_feedback: bool = True
    custom_css: Optional[str] = None
    entry_animation: str = "slide"
    bot_avatar_url: Optional[str] = None
    require_email: bool = False
    in_chat_email_capture: bool = False
    initial_questions: List[Dict[str, Any]] = []
    supplemental_info: Optional[str] = None  # Fallback knowledge when RAG has no answer

class ChatbotConfigUpdate(BaseModel):
    name: Optional[str] = None
    website_url: Optional[str] = None
    system_prompt: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    icon_url: Optional[str] = None
    position: Optional[str] = None
    position_style: Optional[Dict[str, str]] = None
    button_size: Optional[int] = None
    transparent_background: Optional[bool] = None
    welcome_message: Optional[str] = None
    suggested_questions: Optional[List[str]] = None
    window_title: Optional[str] = None
    temperature: Optional[float] = None
    # New design options
    widget_width: Optional[int] = None
    widget_height: Optional[int] = None
    background_color: Optional[str] = None
    border_radius: Optional[int] = None
    message_bubble_radius: Optional[int] = None
    user_message_color: Optional[str] = None
    bot_message_color: Optional[str] = None
    font_size: Optional[int] = None
    rescrape_interval_hours: Optional[int] = None
    allowed_domains: Optional[List[str]] = None
    ignored_urls: Optional[List[str]] = None
    sitemap_url: Optional[str] = None
    ai_model: Optional[str] = None
    documents: Optional[List[Dict[str, Any]]] = None
    theme: Optional[str] = None
    collect_feedback: Optional[bool] = None
    custom_css: Optional[str] = None
    entry_animation: Optional[str] = None
    bot_avatar_url: Optional[str] = None
    require_email: Optional[bool] = None
    in_chat_email_capture: Optional[bool] = None
    initial_questions: Optional[List[Dict[str, Any]]] = None
    supplemental_info: Optional[str] = None  # Fallback knowledge when RAG has no answer

class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    reasoning_details: Optional[Dict[str, Any]] = None  # For OpenRouter reasoning
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class Conversation(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    user_id: Optional[str] = None
    chatbot_id: str
    messages: List[Message] = []
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    page_content: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class ChatRequest(BaseModel):
    chatbot_id: str
    message: str
    conversation_id: Optional[str] = None
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    page_content: Optional[str] = None

    @field_validator('message')
    @classmethod
    def message_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Message cannot be empty')
        if len(v) > 5000:
            raise ValueError('Message must be 5000 characters or fewer')
        return v.strip()

    @field_validator('chatbot_id')
    @classmethod
    def valid_chatbot_id(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError('Invalid chatbot ID format')
        return v

    @field_validator('page_content')
    @classmethod
    def limit_page_content(cls, v):
        if v and len(v) > 10000:
            return v[:10000]
        return v

class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    sources: Optional[List[dict]] = None

class IngestRequest(BaseModel):
    text: str

    @field_validator('text')
    @classmethod
    def text_not_empty_and_bounded(cls, v):
        if not v or not v.strip():
            raise ValueError('Text cannot be empty')
        # Cap to ~500 KB so a single ingest can't drive unbounded embedding
        # CPU / ChromaDB growth (consistent with the other content size limits).
        if len(v) > 500_000:
            raise ValueError('Text must be 500000 characters or fewer')
        return v

# Chatbot Permission Models
class PermissionLevel:
    VIEWER = "viewer"      # Can only view conversations and scraped pages
    EDITOR = "editor"      # Can modify chatbot config, view conversations
    ADMIN = "admin"        # Full access except delete chatbot

class ChatbotPermission(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    user_email: EmailStr
    permission_level: str = PermissionLevel.VIEWER  # viewer, editor, admin
    granted_by: str  # email of the user who granted this permission
    granted_at: datetime = Field(default_factory=datetime.utcnow)
    
    @field_validator('permission_level')
    @classmethod
    def valid_permission(cls, v):
        valid_levels = [PermissionLevel.VIEWER, PermissionLevel.EDITOR, PermissionLevel.ADMIN]
        if v not in valid_levels:
            raise ValueError(f'Permission level must be one of: {", ".join(valid_levels)}')
        return v
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class ChatbotPermissionCreate(BaseModel):
    chatbot_id: str
    user_email: EmailStr
    permission_level: str = PermissionLevel.VIEWER
    
    @field_validator('permission_level')
    @classmethod
    def valid_permission(cls, v):
        valid_levels = [PermissionLevel.VIEWER, PermissionLevel.EDITOR, PermissionLevel.ADMIN]
        if v not in valid_levels:
            raise ValueError(f'Permission level must be one of: {", ".join(valid_levels)}')
        return v

class ChatbotPermissionUpdate(BaseModel):
    permission_level: str
    
    @field_validator('permission_level')
    @classmethod
    def valid_permission(cls, v):
        valid_levels = [PermissionLevel.VIEWER, PermissionLevel.EDITOR, PermissionLevel.ADMIN]
        if v not in valid_levels:
            raise ValueError(f'Permission level must be one of: {", ".join(valid_levels)}')
        return v


# ── Audit Log ─────────────────────────────────────────────────────────────────
class AuditLog(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    # Action identifier, e.g. "permission:granted", "chatbot:created", "chatbot:deleted",
    # "widget:blocked_domain", "permission:revoked", "permission:updated"
    action: str
    performed_by: str           # email of the user who triggered the event
    chatbot_id: Optional[str] = None
    target_user: Optional[str] = None   # email of the affected user (permission actions)
    details: Optional[Dict[str, Any]] = None  # arbitrary extra context
    ip_address: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


# ══════════════════════════════════════════════════════════════════════════════
# Page Version History & Change Tracking
# ══════════════════════════════════════════════════════════════════════════════

class PageVersion(BaseModel):
    """Historical version of a scraped page for audit and rollback"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    page_id: str  # Reference to ScrapedPage
    url: str
    
    # Version metadata
    version_number: int
    content_hash: str
    scraped_at: datetime
    
    # Content snapshot
    title: str
    char_count: int
    size_bytes: Optional[int] = None
    
    # Change detection
    change_magnitude: Optional[str] = None  # "trivial", "minor", "moderate", "major", "complete_rewrite"
    change_score: Optional[int] = None  # 1-5
    changes_summary: Optional[Dict[str, Any]] = None  # {"added": 120, "removed": 45, "modified": 30}
    
    # Quality at this version
    quality_score: Optional[int] = None
    quality_grade: Optional[str] = None
    
    # SEO snapshot
    seo_metadata: Optional[Dict[str, Any]] = None
    
    # Storage
    content_stored: bool = False  # Whether full content is stored
    content_storage_url: Optional[str] = None  # S3/storage link
    diff_storage_url: Optional[str] = None  # Link to diff HTML
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

# ══════════════════════════════════════════════════════════════════════════════
# Leads (captured visitor emails)
# ══════════════════════════════════════════════════════════════════════════════

class Lead(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    email: EmailStr
    name: Optional[str] = None
    conversation_id: Optional[str] = None
    source_url: Optional[str] = None
    status: str = "new"  # new, contacted, qualified, unqualified
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class LeadCreate(BaseModel):
    chatbot_id: str
    email: EmailStr
    name: Optional[str] = None
    conversation_id: Optional[str] = None
    source_url: Optional[str] = None

class LeadStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None

    @field_validator('status')
    @classmethod
    def valid_status(cls, v):
        valid = ["new", "contacted", "qualified", "unqualified"]
        if v not in valid:
            raise ValueError(f'Status must be one of: {", ".join(valid)}')
        return v


# ══════════════════════════════════════════════════════════════════════════════
# Document metadata (uploaded knowledge base files)
# ══════════════════════════════════════════════════════════════════════════════

class DocumentMeta(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    user_id: str
    original_name: str
    filename: str       # UUID-based filename stored on disk
    file_url: str
    file_size: int
    file_ext: str
    chunks_count: int = 0
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class ChangeAlert(BaseModel):
    """Alert notification for page changes"""
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    chatbot_id: str
    page_id: str
    url: str
    
    # Alert metadata
    alert_type: str  # "page_changed", "page_broken", "quality_degraded", "major_change"
    severity: str  # "critical", "important", "normal", "minor"
    
    # Change details
    old_version_number: int
    new_version_number: int
    change_magnitude: str
    change_summary: str
    
    # Notification status
    sent_at: Optional[datetime] = None
    sent_to_emails: Optional[List[str]] = None
    sent_to_webhooks: Optional[List[str]] = None
    delivery_status: str = "pending"  # "pending", "sent", "failed"
    delivery_error: Optional[str] = None
    
    # User actions
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None  # user_id
    acknowledged_at: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
