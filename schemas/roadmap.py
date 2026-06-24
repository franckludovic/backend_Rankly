from pydantic import BaseModel
from typing import Literal

class RoadmapTaskStatusUpdate(BaseModel):
    status: Literal["todo", "in_progress", "done", "skipped"]
