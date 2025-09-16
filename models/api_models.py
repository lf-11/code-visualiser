from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class Project(BaseModel):
    id: Optional[int] = None
    name: str
    path: str
    is_parsed: bool = False

class File(BaseModel):
    id: int
    path: str
    kind: Optional[str] = None
    loc: Optional[int] = None

class CodeElement(BaseModel):
    id: int
    kind: str
    name: str
    parent_id: Optional[int] = None
    start_line: int
    end_line: int
    metadata: Optional[Dict[str, Any]] = None

class FileDetails(BaseModel):
    content: str
    elements: List[CodeElement] 