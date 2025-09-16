import os
import hashlib
import uuid
import json
from typing import Dict, Type

from parsers.base_parser import BaseParser
from parsers.python_parser import PythonParser
from parsers.js_parser import JavascriptParser
from parsers.html_parser import HTMLParser
from core.database import get_db_connection

# --- Parser Mapping ---
PARSER_MAPPING: Dict[str, Type[BaseParser]] = {
    ".py": PythonParser,
    ".js": JavascriptParser,
    ".html": HTMLParser,
    ".htm": HTMLParser,
}

# --- Files/Directories to Ignore ---
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".vscode", ".idea"}
IGNORE_FILES = {".DS_Store"}

def get_parser_for_file(file_path: str) -> Type[BaseParser] | None:
    """Returns the appropriate parser class for a given file, or None."""
    _, extension = os.path.splitext(file_path)
    return PARSER_MAPPING.get(extension.lower())

def calculate_checksum(content: bytes) -> str:
    """Calculates the SHA-256 checksum of file content."""
    return hashlib.sha256(content).hexdigest()

def parse_and_store_project(project_name: str, project_path: str):
    """
    Parses all supported files in a project directory and stores the results
    in the database. This is a single, large transaction.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            try:
                # 1. Create the project record
                cur.execute(
                    "INSERT INTO projects (name, path) VALUES (%s, %s) RETURNING id",
                    (project_name, project_path)
                )
                project_id = cur.fetchone()[0]
                print(f"Created project '{project_name}' with ID: {project_id}")

                # 2. Walk the directory and parse files
                for root, dirs, files in os.walk(project_path, topdown=True):
                    # Modify dirs in-place to skip ignored directories
                    dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

                    for filename in files:
                        if filename in IGNORE_FILES:
                            continue

                        file_path = os.path.join(root, filename)
                        relative_path = os.path.relpath(file_path, project_path)

                        ParserClass = get_parser_for_file(filename)
                        if not ParserClass:
                            continue  # Skip unsupported file types

                        print(f"  - Parsing: {relative_path}")
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                        except (IOError, UnicodeDecodeError) as e:
                            print(f"    - ERROR: Could not read file {relative_path}: {e}")
                            continue
                        
                        if not content.strip():
                            continue

                        checksum = calculate_checksum(content.encode('utf-8'))
                        loc = len(content.splitlines())

                        # 3. Insert file record
                        stable_file_id = uuid.uuid4()
                        cur.execute(
                            """
                            INSERT INTO files (project_id, stable_file_id, path, kind, checksum, loc)
                            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                            """,
                            (project_id, stable_file_id, relative_path, ParserClass.__name__, checksum, loc)
                        )
                        file_id = cur.fetchone()[0]

                        # 4. Parse content and insert code elements
                        parser = ParserClass()
                        elements = parser.parse(content)

                        for el in elements:
                            stable_element_id = uuid.uuid4()
                            metadata = el.get('metadata')
                            cur.execute(
                                """
                                INSERT INTO code_elements (file_id, stable_element_id, kind, name, content, start_line, end_line, metadata)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    file_id, stable_element_id, el['kind'], el['name'],
                                    el['content'], el['start_line'], el['end_line'],
                                    json.dumps(metadata) if metadata else None
                                )
                            )
                
                conn.commit()
                print(f"Successfully parsed and stored project '{project_name}'")

            except Exception as e:
                conn.rollback()
                print(f"ERROR: Transaction rolled back for project '{project_name}'. Reason: {e}")
                raise 