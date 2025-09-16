import os
import hashlib
import uuid
import json
from typing import Dict, Type

import config
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
    in the database. If the project already exists, it will be deleted and
    re-parsed from scratch. This is a single, large transaction.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            try:
                # --- Handle Re-parsing ---
                # 1. Check if project exists and delete it for a clean slate.
                cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
                project_row = cur.fetchone()
                if project_row:
                    project_id_to_delete = project_row[0]
                    print(f"Project '{project_name}' already exists. Deleting old data first...")
                    # ON DELETE CASCADE handles deleting files, elements, etc.
                    cur.execute("DELETE FROM projects WHERE id = %s", (project_id_to_delete,))
                    print(f"Old data for project '{project_name}' deleted.")

                # 2. Create the project record
                cur.execute(
                    "INSERT INTO projects (name, path) VALUES (%s, %s) RETURNING id",
                    (project_name, project_path)
                )
                project_id = cur.fetchone()[0]
                print(f"Created project '{project_name}' with ID: {project_id}")

                # 3. Walk the directory and parse files
                for root, dirs, files in os.walk(project_path, topdown=True):
                    # Modify dirs in-place to skip ignored directories
                    dirs[:] = [d for d in dirs if d not in config.IGNORED_DIRECTORIES]

                    for filename in files:
                        _, extension = os.path.splitext(filename)
                        if filename in config.IGNORED_FILES or extension.lower() in config.IGNORED_FILE_EXTENSIONS:
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

                        # 4. Insert file record
                        stable_file_id = uuid.uuid4()
                        cur.execute(
                            """
                            INSERT INTO files (project_id, stable_file_id, path, kind, checksum, loc, content)
                            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                            """,
                            (project_id, stable_file_id, relative_path, ParserClass.__name__, checksum, loc, content)
                        )
                        file_id = cur.fetchone()[0]

                        # 5. Parse content and insert code elements hierarchically
                        parser = ParserClass()
                        elements_tree = parser.parse(content)

                        def insert_elements_recursively(elements, parent_id=None, level=0):
                            indent = "    " * level
                            print(f"{indent} -> Inserting {len(elements)} elements under parent_id: {parent_id}")
                            for el in elements:
                                stable_element_id = uuid.uuid4()
                                metadata = el.get('metadata')
                                
                                print(f"{indent}   - Inserting: {el['kind']} '{el['name']}' (L{el['start_line']})")
                                
                                cur.execute(
                                    """
                                    INSERT INTO code_elements (file_id, parent_id, stable_element_id, kind, name, content, start_line, end_line, metadata)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                                    """,
                                    (
                                        file_id, parent_id, stable_element_id, el['kind'], el['name'],
                                        el['content'], el['start_line'], el['end_line'],
                                        json.dumps(metadata) if metadata else None
                                    )
                                )
                                element_id = cur.fetchone()[0]

                                children = el.get('children', [])
                                if children:
                                    insert_elements_recursively(children, element_id, level + 1)

                        insert_elements_recursively(elements_tree)

                conn.commit()
                print(f"Successfully parsed and stored project '{project_name}'")

            except Exception as e:
                conn.rollback()
                print(f"ERROR: Transaction rolled back for project '{project_name}'. Reason: {e}")
                raise 