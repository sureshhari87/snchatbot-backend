import json
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            decoded = [item.strip() for item in stripped.split(",")]
        return normalize_string_list(decoded)
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


class ProductOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    metal: Optional[str] = None
    price: float
    image: Optional[str] = None
    in_stock: bool
    stock_quantity: int = 0
    is_featured: bool = False
    product_type: Optional[str] = None
    audience: Optional[str] = None
    purity: Optional[str] = None
    weight: Optional[float] = None
    tags: List[str] = Field(default_factory=list)
    occasion: List[str] = Field(default_factory=list)
    style: List[str] = Field(default_factory=list)

    @field_validator("tags", "occasion", "style", mode="before")
    @classmethod
    def normalize_metadata_lists(cls, value: Any) -> list[str]:
        return normalize_string_list(value)

    model_config = ConfigDict(from_attributes=True)


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    metal: Optional[str] = None
    price: float = Field(..., ge=0)
    image: Optional[str] = None
    in_stock: bool = True
    stock_quantity: int = Field(default=0, ge=0)
    is_featured: bool = False
    product_type: Optional[str] = None
    audience: Optional[str] = None
    purity: Optional[str] = None
    weight: Optional[float] = Field(default=None, ge=0)
    tags: List[str] = Field(default_factory=list)
    occasion: List[str] = Field(default_factory=list)
    style: List[str] = Field(default_factory=list)

    @field_validator("tags", "occasion", "style", mode="before")
    @classmethod
    def normalize_metadata_lists(cls, value: Any) -> list[str]:
        return normalize_string_list(value)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    metal: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    image: Optional[str] = None
    in_stock: Optional[bool] = None
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    is_featured: Optional[bool] = None
    product_type: Optional[str] = None
    audience: Optional[str] = None
    purity: Optional[str] = None
    weight: Optional[float] = Field(default=None, ge=0)
    tags: Optional[List[str]] = None
    occasion: Optional[List[str]] = None
    style: Optional[List[str]] = None

    @field_validator("tags", "occasion", "style", mode="before")
    @classmethod
    def normalize_metadata_lists(cls, value: Any) -> list[str] | None:
        return None if value is None else normalize_string_list(value)


class InventoryUpdate(BaseModel):
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    in_stock: Optional[bool] = None


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    slug: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FeaturedItemCreate(BaseModel):
    product_id: int
    title: Optional[str] = None
    subtitle: Optional[str] = None
    display_order: int = 0
    is_active: bool = True


class FeaturedItemUpdate(BaseModel):
    product_id: Optional[int] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class FeaturedItemOut(BaseModel):
    id: int
    product_id: int
    title: Optional[str] = None
    subtitle: Optional[str] = None
    display_order: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SeasonalCollectionCreate(BaseModel):
    name: str = Field(..., min_length=1)
    slug: Optional[str] = None
    description: Optional[str] = None
    season: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: bool = True


class SeasonalCollectionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    slug: Optional[str] = None
    description: Optional[str] = None
    season: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: Optional[bool] = None


class SeasonalCollectionOut(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    season: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeBaseCreate(BaseModel):
    kind: Literal["faq", "policy"] = "faq"
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    slug: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: bool = True


class KnowledgeBaseUpdate(BaseModel):
    kind: Optional[Literal["faq", "policy"]] = None
    title: Optional[str] = Field(default=None, min_length=1)
    content: Optional[str] = Field(default=None, min_length=1)
    slug: Optional[str] = None
    tags: Optional[List[str]] = None
    is_active: Optional[bool] = None


class KnowledgeBaseOut(BaseModel):
    id: int
    kind: str
    slug: str
    title: str
    content: str
    tags: List[str] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AppConfigCreate(BaseModel):
    key: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    description: Optional[str] = None
    is_public: bool = False


class AppConfigUpdate(BaseModel):
    value: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    is_public: Optional[bool] = None


class AppConfigOut(BaseModel):
    id: int
    key: str
    value: str
    description: Optional[str] = None
    is_public: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserAddressCreate(BaseModel):
    label: str = "home"
    full_name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    line1: str = Field(..., min_length=1)
    line2: Optional[str] = None
    city: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1)
    postal_code: str = Field(..., min_length=1)
    country: str = "India"
    is_default: bool = False


class UserAddressUpdate(BaseModel):
    label: Optional[str] = None
    full_name: Optional[str] = Field(default=None, min_length=1)
    phone: Optional[str] = None
    line1: Optional[str] = Field(default=None, min_length=1)
    line2: Optional[str] = None
    city: Optional[str] = Field(default=None, min_length=1)
    state: Optional[str] = Field(default=None, min_length=1)
    postal_code: Optional[str] = Field(default=None, min_length=1)
    country: Optional[str] = None
    is_default: Optional[bool] = None


class UserAddressOut(BaseModel):
    id: int
    user_id: int
    label: str
    full_name: str
    phone: Optional[str] = None
    line1: str
    line2: Optional[str] = None
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationSettingsUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    sms_enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None
    marketing_enabled: Optional[bool] = None
    order_updates_enabled: Optional[bool] = None
    chat_updates_enabled: Optional[bool] = None
    appointment_reminders_enabled: Optional[bool] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    push_token: Optional[str] = None


class NotificationSettingsOut(BaseModel):
    id: int
    user_id: int
    email_enabled: bool
    sms_enabled: bool
    push_enabled: bool
    marketing_enabled: bool
    order_updates_enabled: bool
    chat_updates_enabled: bool
    appointment_reminders_enabled: bool
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    push_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SavedItemCreate(BaseModel):
    product_id: int
    note: Optional[str] = None


class SavedProductOut(BaseModel):
    id: int
    product: ProductOut
    note: Optional[str] = None
    created_at: datetime


class BackInStockSubscribeRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    size: Optional[str] = None
    variant: Optional[str] = None


class BackInStockSubscriptionOut(BaseModel):
    id: int
    user_id: int
    product: ProductOut
    email: Optional[str] = None
    phone: Optional[str] = None
    size: Optional[str] = None
    variant: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    notified_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None


class BackInStockNotifyOut(BaseModel):
    product_id: int
    notified_count: int
    message: str


class CallbackRequestCreate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    reason: Optional[str] = None
    preferred_time: Optional[str] = None


class CallbackRequestOut(BaseModel):
    id: int
    user_id: int
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    reason: Optional[str] = None
    preferred_time: Optional[str] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AppointmentCreate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    store_location: str = Field(..., min_length=1)
    appointment_time: datetime
    purpose: Optional[str] = None


class AppointmentOut(BaseModel):
    id: int
    user_id: int
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    store_location: str
    appointment_time: datetime
    purpose: Optional[str] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CustomOrderCreate(BaseModel):
    product_id: Optional[int] = None
    session_id: Optional[str] = None
    description: str = Field(..., min_length=1)
    budget: Optional[float] = Field(default=None, ge=0)
    metal: Optional[str] = None
    category: Optional[str] = None


class CustomOrderOut(BaseModel):
    id: int
    user_id: int
    product_id: Optional[int] = None
    session_id: Optional[str] = None
    description: str
    budget: Optional[float] = None
    metal: Optional[str] = None
    category: Optional[str] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AiGeneratedConceptCreate(BaseModel):
    concept_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    source_prompt: str = Field(..., min_length=1)
    design_brief: str = Field(..., min_length=1)
    materials: List[str] = Field(default_factory=list)
    craft_notes: List[str] = Field(default_factory=list)
    image_base64: Optional[str] = None
    image_mime_type: Optional[str] = None
    answer_source: str = "template"
    category: Optional[str] = None
    metal: Optional[str] = None
    gemstones: List[str] = Field(default_factory=list)
    budget: Optional[float] = Field(default=None, ge=0)
    related_product_ids: List[str] = Field(default_factory=list)


class AiGeneratedConceptOut(BaseModel):
    id: int
    concept_id: str
    user_id: int
    name: str
    source_prompt: str
    design_brief: str
    materials: List[str]
    craft_notes: List[str]
    image_base64: Optional[str] = None
    image_mime_type: Optional[str] = None
    answer_source: str
    category: Optional[str] = None
    metal: Optional[str] = None
    gemstones: List[str]
    budget: Optional[float] = None
    related_product_ids: List[str]
    status: str
    created_at: datetime


class ComplaintCreate(BaseModel):
    order_reference: Optional[str] = None
    category: str = "general"
    message: str = Field(..., min_length=1)
    priority: str = "normal"


class ComplaintOut(BaseModel):
    id: int
    user_id: int
    order_reference: Optional[str] = None
    category: str
    message: str
    priority: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderItemSync(BaseModel):
    product_id: Optional[str] = None
    backend_product_id: Optional[int] = None
    name: str = Field(..., min_length=1)
    qty: int = Field(default=1, ge=1)
    price: float = Field(default=0, ge=0)
    image: Optional[str] = None


class PaymentCartItem(BaseModel):
    product_id: Optional[str] = None
    backend_product_id: Optional[int] = None
    qty: int = Field(default=1, ge=1)
    selected_size: Optional[str] = None
    selected_variant: Optional[str] = None
    cart_item_id: Optional[str] = None


class OrderSyncRequest(BaseModel):
    order_reference: str = Field(..., min_length=1)
    status: str = "placed"
    total: float = Field(default=0, ge=0)
    currency: str = "INR"
    items: List[OrderItemSync] = Field(default_factory=list)
    customer_name: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    customer_phone: Optional[str] = None
    delivery_address: Optional[dict[str, Any] | str] = None
    payment_status: Optional[str] = None
    payment_reference: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    expected_delivery: Optional[str] = None
    source: str = "android_app"
    metadata: Optional[dict[str, Any]] = None
    raw_payload: Optional[dict[str, Any]] = None


class RazorpayOrderCreate(BaseModel):
    commerce_source: Literal["auto", "fastapi"] = "auto"
    amount: Optional[int] = Field(default=None, ge=100)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    receipt: Optional[str] = Field(default=None, max_length=40)
    notes: dict[str, Any] = Field(default_factory=dict)
    items: List[PaymentCartItem] = Field(default_factory=list)
    coupon_code: Optional[str] = Field(default=None, max_length=80)
    reward_points_requested: int = Field(default=0, ge=0)
    gift_voucher_code: Optional[str] = Field(default=None, max_length=80)
    customer_name: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    customer_phone: Optional[str] = None
    delivery_address: Optional[dict[str, Any]] = None
    firebase_id_token: Optional[str] = Field(default=None, min_length=20)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("receipt")
    @classmethod
    def normalize_receipt(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class RazorpayOrderOut(BaseModel):
    key_id: str
    keyId: str
    order_id: str
    razorpayOrderId: str
    order_reference: str
    local_order_id: int
    amount: int
    currency: str
    receipt: Optional[str] = None
    status: str
    payable_total: float
    payableTotal: float
    coupon_discount: float = 0
    couponDiscount: float = 0
    reward_points_used: int = 0
    rewardPointsUsed: int = 0
    server_calculated: bool = True


class RazorpayPaymentVerifyRequest(BaseModel):
    razorpay_order_id: str = Field(..., min_length=1)
    razorpay_payment_id: str = Field(..., min_length=1)
    razorpay_signature: str = Field(..., min_length=1)


class OrderItemOut(BaseModel):
    id: int
    product_reference: Optional[str] = None
    backend_product_id: Optional[int] = None
    name: str
    qty: int
    unit_price: float
    image: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OrderSnapshotOut(BaseModel):
    id: int
    user_id: int
    order_reference: str
    status: str
    total: float
    currency: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    delivery_address: dict[str, Any] = Field(default_factory=dict)
    payment_status: Optional[str] = None
    payment_reference: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    expected_delivery: Optional[str] = None
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    delivery_promises: List[dict[str, Any]] = Field(default_factory=list)
    items: List[OrderItemOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RazorpayPaymentVerifyOut(BaseModel):
    message: str
    verified: bool
    payment_id: str
    paymentId: str
    order_reference: str
    orderId: str
    status: str
    payment_status: str
    paymentStatus: str
    total: float
    currency: str
    items: List[OrderItemOut] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    order: OrderSnapshotOut


class OrderStatusUpdate(BaseModel):
    status: Optional[str] = None
    payment_status: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    expected_delivery: Optional[str] = None
    delivery_address: Optional[dict[str, Any]] = None


class OrderSupportCreate(BaseModel):
    order_reference: Optional[str] = None
    request_type: Literal["status", "cancel", "return", "refund", "delivery", "other"] = "status"
    message: Optional[str] = None


class OrderSupportOut(BaseModel):
    id: int
    user_id: int
    order_reference: Optional[str] = None
    request_type: str
    message: Optional[str] = None
    status: str
    created_at: datetime
    integration_status: Optional[str] = None
    oms_response: Optional[dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class OrderActionRequest(BaseModel):
    reason: Optional[str] = None
    message: Optional[str] = None


class OrderActionOut(BaseModel):
    order_reference: str
    action: str
    integration_status: str
    data: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class OrderLookupOut(BaseModel):
    order_reference: str
    integration_status: str
    data: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class FeedbackCreate(BaseModel):
    response_id: Optional[str] = None
    feedback_type: Optional[Literal["thumbs_up", "thumbs_down", "not_helpful", "neutral"]] = None
    helpful: Optional[bool] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    context: Optional[str] = None
    comment: Optional[str] = None


class HandoffInfo(BaseModel):
    reason: str
    message: str
    channels: List[str] = Field(default_factory=list)
    lead_id: Optional[int] = None


class LeadCaptureOut(BaseModel):
    id: int
    user_id: int
    session_id: Optional[str] = None
    source: str
    intent: str
    message: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LeadStatusUpdate(BaseModel):
    status: str = Field(..., min_length=1)


class TranscriptReviewUpdate(BaseModel):
    notes: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response_id: str
    reply: str
    products: List[ProductOut] = Field(default_factory=list)
    session_id: Optional[str] = None
    suggestions: List[str] = Field(default_factory=list)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    result_count: int = 0
    suggested_next_questions: List[str] = Field(default_factory=list)
    intent: Optional[str] = None
    confidence: float = 0.0
    answer_source: str = "rules"
    tool_calls: List[str] = Field(default_factory=list)
    guardrails: List[str] = Field(default_factory=list)
    lead_captured: bool = False
    handoff: Optional[HandoffInfo] = None


class ChatMessageOut(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatSessionOut(BaseModel):
    session_id: str
    last_filters: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    message_count: int = 0
    last_message_at: Optional[datetime] = None


class ChatSessionDetailOut(ChatSessionOut):
    messages: List[ChatMessageOut] = Field(default_factory=list)


class ExternalIntegrationEventOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    service: str
    action: str
    reference: Optional[str] = None
    status: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserRegister(BaseModel):
    username: str
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class FirebaseAuthRequest(BaseModel):
    id_token: str = Field(min_length=20)


class OtpRequestCreate(BaseModel):
    phone: str = Field(..., min_length=8, max_length=20)


class OtpRequestOut(BaseModel):
    message: str
    phone_masked: str
    expires_in_seconds: int
    resend_after_seconds: int


class OtpVerifyRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=20)
    otp: str = Field(..., min_length=4, max_length=8)


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool = False

    model_config = ConfigDict(from_attributes=True)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=6)


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class EmailTestRequest(BaseModel):
    to_email: Optional[EmailStr] = None


class MessageResponse(BaseModel):
    message: str
