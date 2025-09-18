import re
import os
import sys
import psycopg2
import psycopg2.extras

# Add the project root to the Python path to allow imports like 'core.database'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection

def get_all_python_elements(cur, project_id, kinds):
    """Fetches all code elements of specific kinds from all Python files in a project."""
    query = """
        SELECT ce.id, ce.name, ce.kind, f.path, f.id as file_id
        FROM code_elements ce
        JOIN files f ON ce.file_id = f.id
        WHERE f.project_id = %s
          AND f.path LIKE '%%.py'
          AND ce.kind IN %s
          AND f.is_latest = TRUE;
    """
    cur.execute(query, (project_id, tuple(kinds)))
    return cur.fetchall()

def resolve_module_path(current_file_path, module_str):
    """Converts a Python module string into a file path."""
    if module_str.startswith('.'):
        # Relative import: from .base_parser
        # Go up one level from the current file's directory
        current_dir = os.path.dirname(current_file_path)
        # remove the '.' and replace with path
        relative_path = module_str[1:].replace('.', os.path.sep)
        return os.path.normpath(os.path.join(current_dir, relative_path + ".py"))
    else:
        # Absolute import: from core.database
        return module_str.replace('.', os.path.sep) + ".py"

def build_global_alias_map(imports, definitions):
    """
    Builds a project-wide map linking imported symbols to their original definitions.
    
    Returns a dictionary:
    {(importing_file_path, alias_name): original_element_id}
    """
    print("--- Building Global Alias Map ---")
    
    # 1. Create a lookup for all defined functions and classes in the project
    # Format: {(file_path, symbol_name): element_id}
    definitions_map = {}
    for element in definitions:
        clean_name = element['name'].split('(')[0].strip()
        definitions_map[(element['path'], clean_name)] = element['id']
    
    print(f"Found {len(definitions)} total class/function definitions to map against.")

    # 2. Iterate through all imports and create the alias map
    alias_map = {}
    for imp in imports:
        importing_file = imp['path']
        import_str = imp['name'].split('(')[0].strip()

        # Regex for 'from module import name [as alias]'
        match = re.match(r"from\s+([\w\.]+)\s+import\s+([\w\s,]+)", import_str)
        if not match:
            continue

        module_str, names_str = match.groups()
        original_file_path = resolve_module_path(importing_file, module_str)

        # Handle multiple imports on one line, e.g., 'import a, b as c'
        for name_part in names_str.split(','):
            name_part = name_part.strip()
            alias_match = re.match(r"(\w+)\s+as\s+(\w+)", name_part)
            if alias_match:
                original_name, alias_name = alias_match.groups()
            else:
                original_name, alias_name = name_part, name_part

            # Find the ID of the original class/function definition
            original_element_id = definitions_map.get((original_file_path, original_name))
            
            if original_element_id:
                alias_map[(importing_file, alias_name)] = original_element_id
                print(f"  - Mapped: In '{importing_file}', alias '{alias_name}' -> original '{original_name}' from '{original_file_path}'")
            else:
                print(f"  - [WARN] Could not find original definition for '{original_name}' from '{original_file_path}'")
    
    print("--- Global Alias Map Complete ---\n")
    return alias_map, definitions_map

def main():
    TARGET_PROJECT_NAME = 'code_visualiser'
    VALIDATION_FILE = 'core/parser_manager.py'
    SYMBOLS_TO_VALIDATE = ['PythonParser', 'JavascriptParser', 'HTMLParser']

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                
                # Get Project ID
                cur.execute("SELECT id FROM projects WHERE name = %s;", (TARGET_PROJECT_NAME,))
                project_id = cur.fetchone()['id']

                # 1. Fetch all necessary data from the database in one go
                all_imports = get_all_python_elements(cur, project_id, ['import'])
                all_definitions = get_all_python_elements(cur, project_id, ['class', 'function'])
                
                # Create a simple lookup from element ID to the full element record
                all_elements_by_id = {el['id']: el for el in all_definitions}

                # 2. Build the comprehensive, project-wide alias map
                alias_map, _ = build_global_alias_map(all_imports, all_definitions)

                # 3. Perform the specific validation for parser_manager.py
                print(f"--- Validating Imports for '{VALIDATION_FILE}' ---")
                
                all_found = True
                for symbol in SYMBOLS_TO_VALIDATE:
                    lookup_key = (VALIDATION_FILE, symbol)
                    
                    if lookup_key in alias_map:
                        original_element_id = alias_map[lookup_key]
                        original_element = all_elements_by_id[original_element_id]
                        
                        print(f"[OK] Found link for '{symbol}':")
                        print(f"     It correctly maps to the '{original_element['name'].split('(')[0]}' {original_element['kind']} in '{original_element['path']}'.")
                    else:
                        print(f"[FAIL] Could not find a link for '{symbol}' in the alias map.")
                        all_found = False
                
                print("\n--- Validation Summary ---")
                if all_found:
                    print("[SUCCESS] All specified parser classes in 'parser_manager.py' were correctly linked to their definitions.")
                else:
                    print("[FAILURE] One or more parser classes could not be linked. Check warnings above.")

    except psycopg2.OperationalError as e:
        print(f"\nDatabase connection error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
