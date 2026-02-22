"""
Pydantic models for chat API requests and responses
"""
from pydantic import BaseModel, Field
from typing import Optional

class Message(BaseModel):
    """Chat message model"""
    session_id: str = Field(..., description="Unique session identifier")
    message: str = Field(..., description="User message")
    stream: bool = Field(default=False, description="Enable streaming response")
    
class Session(BaseModel):
    """Session identifier model"""
    session_id: str = Field(..., description="Unique session identifier")

class AgentStatusResponse(BaseModel):
    """Agent status response"""
    status: str
    agents: dict
    
class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    service: str
    version: str
    framework: str
