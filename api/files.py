from fastapi import APIRouter, HTTPException
from typing import List, Optional, Dict, Any
import os
import psycopg2.extras

from core.database import get_db_connection
from models.api_models import FileDetails, CodeElement

router = APIRouter()

@router.get("/files/{file_id}", response_model=FileDetails)
async def get_file_details(file_id: int):
    """
    Retrieves the content and parsed code elements for a specific file.
    """
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT f.path AS file_path, p.path AS project_path
                FROM files f
                JOIN projects p ON f.project_id = p.id
                WHERE f.id = %s AND f.is_latest = TRUE
                """,
                (file_id,)
            )
            path_row = cur.fetchone()
            if not path_row:
                raise HTTPException(status_code=404, detail="File not found or is not the latest version.")
            
            full_path = os.path.join(path_row['project_path'], path_row['file_path'])

            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except IOError as e:
                raise HTTPException(status_code=500, detail=f"Could not read file content: {e}")

            elements: List[CodeElement] = []
            cur.execute(
                """
                SELECT id, kind, name, parent_id, start_line, end_line, metadata
                FROM code_elements
                WHERE file_id = %s AND is_latest = TRUE
                ORDER BY start_line
                """,
                (file_id,)
            )
            for row in cur.fetchall():
                elements.append(CodeElement(**row))
            
            return FileDetails(content=content, elements=elements) 