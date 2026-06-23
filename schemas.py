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
    user_id: Optional[str] = None
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    products: List[ProductOut] = []
    session_id: Optional[str] = None