from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class SavedItemCreate(BaseModel):
    product_id: int
    note: Optional[str] = None


class SavedProductOut(BaseModel):
    id: int
    product: ProductOut
    note: Optional[str] = None
    created_at: datetime


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
    lead_captured: bool = False
    handoff: Optional[HandoffInfo] = None


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


class MessageResponse(BaseModel):
    message: str
