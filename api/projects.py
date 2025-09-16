from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List
import os
from dotenv import load_dotenv
import psycopg2.extras

from core.database import get_db_connection
from models.api_models import Project, File
from core import parser_manager

# Load environment variables from .env file
load_dotenv()

router = APIRouter()

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "/home/p5i3/projects")

@router.post("/projects/parse")
async def parse_project(project: Project, background_tasks: BackgroundTasks):
    """
    Triggers the parsing of a project. If it already exists, it will be
    re-parsed. This is a long-running task, so it's added to the background.
    """
    project_path = os.path.join(PROJECTS_DIR, project.name)
    if not os.path.isdir(project_path):
        raise HTTPException(
            status_code=404,
            detail=f"Project directory not found: {project.name}"
        )
    
    # This check is no longer needed, as the parser manager handles it.

    # Use BackgroundTasks to run the parsing without blocking the response
    background_tasks.add_task(
        parser_manager.parse_and_store_project,
        project_name=project.name,
        project_path=project_path
    )
    
    return {"message": f"Parsing started for project '{project.name}'. This may take a few minutes."}


@router.get("/projects/{project_name}/parsing_status", response_model=List[File])
async def get_project_parsing_status(project_name: str):
    """
    Checks all files in a project for unparsed lines of code using the
    content stored in the database at parse time.
    """
    unparsed_files: List[File] = []
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            # 1. Get Project ID
            cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
            project_row = cur.fetchone()
            if not project_row:
                raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
            project_id = project_row['id']

            # 2. Get all files and all elements for the project
            cur.execute("SELECT id, path, content FROM files WHERE project_id = %s AND is_latest = TRUE", (project_id,))
            files = cur.fetchall()
            
            file_ids = [f['id'] for f in files]
            if not file_ids:
                return []

            cur.execute(
                "SELECT file_id, start_line, end_line FROM code_elements WHERE file_id = ANY(%s) AND is_latest = TRUE",
                (file_ids,)
            )
            elements = cur.fetchall()

            # 3. Group elements by file for efficient lookup
            elements_by_file = {}
            for el in elements:
                if el['file_id'] not in elements_by_file:
                    elements_by_file[el['file_id']] = []
                elements_by_file[el['file_id']].append(el)

            # 4. Perform the check for each file
            for file_row in files:
                file_id = file_row['id']
                content = file_row['content'] or ''
                file_elements = elements_by_file.get(file_id, [])
                
                covered_lines = set()
                for el in file_elements:
                    for i in range(el['start_line'], el['end_line'] + 1):
                        covered_lines.add(i)
                
                content_lines = content.splitlines()
                is_unparsed = False
                for i, line_text in enumerate(content_lines, 1):
                    if line_text.strip() and i not in covered_lines:
                        is_unparsed = True
                        break
                
                if is_unparsed:
                    unparsed_files.append(File(id=file_id, path=file_row['path']))

    return sorted(unparsed_files, key=lambda f: f.path)


@router.get("/projects/{project_name}/files", response_model=List[File])
async def list_files_for_project(project_name: str):
    """
    Lists all the parsed files for a given project.
    """
    files: List[File] = []
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
            project_row = cur.fetchone()
            if not project_row:
                raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found.")
            project_id = project_row['id']

            cur.execute(
                "SELECT id, path, kind, loc FROM files WHERE project_id = %s AND is_latest = TRUE ORDER BY path",
                (project_id,)
            )
            for row in cur.fetchall():
                files.append(File(**row))
    return files


@router.get("/projects", response_model=List[Project])
async def list_projects():
    """
    Lists all directories in the PROJECTS_DIR and checks their parsing status
    against the database.
    """
    if not os.path.isdir(PROJECTS_DIR):
        raise HTTPException(
            status_code=500,
            detail=f"The configured projects directory does not exist: {PROJECTS_DIR}"
        )

    # 1. Get all directories from the filesystem
    try:
        fs_projects = {
            p for p in os.listdir(PROJECTS_DIR)
            if os.path.isdir(os.path.join(PROJECTS_DIR, p))
        }
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot read projects directory: {e}")

    # 2. Get all projects from the database
    db_projects = {}
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, path FROM projects")
            for row in cur.fetchall():
                db_projects[row[1]] = Project(id=row[0], name=row[1], path=row[2], is_parsed=True)

    # 3. Merge the two lists
    all_project_names = sorted(list(fs_projects.union(db_projects.keys())))

    response: List[Project] = []
    for name in all_project_names:
        if name in db_projects:
            response.append(db_projects[name])
        elif name in fs_projects:
            full_path = os.path.join(PROJECTS_DIR, name)
            response.append(Project(name=name, path=full_path, is_parsed=False))

    return response 