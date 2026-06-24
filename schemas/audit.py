from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from datetime import datetime

class AuditRequest(BaseModel):
    url: str
    keyword: str

class AuditResponse(BaseModel):
    id: str
    url: str
    keyword: str
    created_at: str
    on_page: Dict[str, Any]
    prediction: Dict[str, Any]
    recommendations: List[Dict[str, Any]]
    competitors: List[Dict[str, Any]]
    serp_features: List[Dict[str, Any]] = []
    generated_schema: Optional[Dict[str, Any]] = None
