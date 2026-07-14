from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_verified = Column(Boolean, nullable=False, default=False)
    is_admin = Column(Boolean, nullable=False, default=False)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    sku = Column(String, unique=True, nullable=True, index=True)
    category = Column(String, index=True)
    metal = Column(String, index=True)
    price = Column(Float, nullable=False)
    image = Column(String)
    in_stock = Column(Boolean, default=True)
    stock_quantity = Column(Integer, nullable=False, default=0)
    is_featured = Column(Boolean, nullable=False, default=False)


class ProductCategory(Base):
    __tablename__ = "product_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class SeasonalCollection(Base):
    __tablename__ = "seasonal_collections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    season = Column(String, nullable=True)
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class FeaturedItem(Base):
    __tablename__ = "featured_items"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    title = Column(String, nullable=True)
    subtitle = Column(String, nullable=True)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class KnowledgeBaseItem(Base):
    __tablename__ = "knowledge_base_items"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, nullable=False)


class AppConfigEntry(Base):
    __tablename__ = "app_config_entries"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    is_public = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(String, index=True)
    last_filters = Column(Text, nullable=True)
    preferences = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("chat_sessions.session_id"), index=True)
    user_id = Column(String, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChatResponseAnalytics(Base):
    __tablename__ = "chat_response_analytics"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String, index=True, nullable=False)
    user_message = Column(Text, nullable=False)
    assistant_reply = Column(Text, nullable=False)
    intent = Column(String, nullable=False, index=True)
    applied_filters = Column(Text, nullable=True)
    product_ids = Column(Text, nullable=True)
    product_names = Column(Text, nullable=True)
    result_count = Column(Integer, nullable=False, default=0)
    unmatched = Column(Boolean, nullable=False, default=False)
    low_conversion = Column(Boolean, nullable=False, default=False)
    lead_captured = Column(Boolean, nullable=False, default=False)
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class ResponseFeedback(Base):
    __tablename__ = "response_feedback"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(String, nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String, nullable=True, index=True)
    feedback_type = Column(String, nullable=False, index=True)
    helpful = Column(Boolean, nullable=True)
    rating = Column(Integer, nullable=True)
    context = Column(String, nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class SaveForLaterItem(Base):
    __tablename__ = "save_for_later_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class CallbackRequest(Base):
    __tablename__ = "callback_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    preferred_time = Column(String, nullable=True)
    status = Column(String, nullable=False, default="new")
    created_at = Column(DateTime, default=utc_now, nullable=False)


class AppointmentBooking(Base):
    __tablename__ = "appointment_bookings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    store_location = Column(String, nullable=False)
    appointment_time = Column(DateTime, nullable=False)
    purpose = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="requested")
    created_at = Column(DateTime, default=utc_now, nullable=False)


class CustomOrderRequest(Base):
    __tablename__ = "custom_order_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    description = Column(Text, nullable=False)
    budget = Column(Float, nullable=True)
    metal = Column(String, nullable=True)
    category = Column(String, nullable=True)
    status = Column(String, nullable=False, default="requested")
    created_at = Column(DateTime, default=utc_now, nullable=False)


class ComplaintTicket(Base):
    __tablename__ = "complaint_tickets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_reference = Column(String, nullable=True, index=True)
    category = Column(String, nullable=False, default="general")
    message = Column(Text, nullable=False)
    priority = Column(String, nullable=False, default="normal")
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=utc_now, nullable=False)


class OrderSupportRequest(Base):
    __tablename__ = "order_support_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    order_reference = Column(String, nullable=True, index=True)
    request_type = Column(String, nullable=False, index=True)
    message = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="received")
    created_at = Column(DateTime, default=utc_now, nullable=False)


class LeadCapture(Base):
    __tablename__ = "lead_captures"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String, index=True, nullable=True)
    source = Column(String, nullable=False, default="chat")
    intent = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    contact_name = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    status = Column(String, nullable=False, default="new")
    created_at = Column(DateTime, default=utc_now, nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    token_jti = Column(String, unique=True, nullable=True, index=True)
    family_id = Column(String, nullable=True, index=True)
    parent_token_id = Column(Integer, ForeignKey("refresh_tokens.id"), nullable=True)
    replaced_by_token_id = Column(Integer, ForeignKey("refresh_tokens.id"), nullable=True)
    is_revoked = Column(Boolean, default=False, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_ip = Column(String, nullable=True)
    created_user_agent = Column(String, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String, nullable=True)
    last_used_user_agent = Column(String, nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    is_used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    is_used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
