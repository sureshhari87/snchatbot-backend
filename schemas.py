from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

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
    products: List[ProductOut] = []
    session_id: Optional[str] = None


class UserRegister(BaseModel):
    username: str
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    email: str

    model_config = ConfigDict(from_attributes=True)