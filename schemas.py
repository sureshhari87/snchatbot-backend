from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, EmailStr

class ProductOut(BaseModel):
    id: int
    name: str
    category: Optional[str] = None
    metal: Optional[str] = None
    price: float
    image: Optional[str] = None
    in_stock: bool

    model_config = ConfigDict(from_attributes=True)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    products: List[ProductOut] = Field(default_factory=list)
    session_id: Optional[str] = None
    suggestions: List[str] = Field(default_factory=list)


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
