import psycopg2
import psycopg2.extras
import json
from core.database import get_db_connection

def find_workflow():
    """
    Finds and prints a workflow starting from a UI element in the frontend
    to a backend API endpoint.
    """
    workflow = []
    print("--- Starting Experimental Workflow Discovery ---")
    print("Project under analysis: code_visualiser")

    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            
            # --- Step 1: Assume UI element is found ---
            button_id_str = "parse-project-btn"
            print(f"\n[Step 1] Starting with assumed UI element: button with id '{button_id_str}'")
            # We are skipping the HTML search for now.

            # --- Step 2: Find the DOM element selection in JS ---
            print(f"\n[Step 2] Searching for JS DOM element linked to id '{button_id_str}' in 'app.js'")
            cur.execute("""
                SELECT ce.*, f.path
                FROM code_elements ce
                JOIN files f ON ce.file_id = f.id
                JOIN projects p ON f.project_id = p.id
                WHERE p.name = 'code_visualiser'
                  AND ce.kind = 'dom_element_definition'
                  AND f.path = 'static/app.js'
                  AND ce.content LIKE %s
                  AND f.is_latest = TRUE;
            """, (f'%{button_id_str}%',))
            
            dom_element = cur.fetchone()

            if not dom_element:
                print(f"  [FAIL] DOM element for '{button_id_str}' not found.")
                return
            
            workflow.append(dom_element)
            dom_element_name = dom_element['name']
            print(f"  [OK] Found DOM Element: '{dom_element_name}' in {dom_element['path']} (ID: {dom_element['id']})")
            print(f"     Content: {dom_element['content'].strip()}")

            # --- Step 3: Find the event listener attached to the DOM element ---
            print(f"\n[Step 3] Searching for event listener on DOM element '{dom_element_name}'")
            
            # Exclude IDs of elements we've already found in the workflow
            found_element_ids = tuple(el['id'] for el in workflow)

            cur.execute("""
                SELECT ce.*, f.path
                FROM code_elements ce
                JOIN files f ON ce.file_id = f.id
                JOIN projects p ON f.project_id = p.id
                WHERE p.name = 'code_visualiser'
                  AND ce.kind = 'expression statement'
                  AND f.path = 'static/app.js'
                  AND ce.content LIKE %s
                  AND ce.id NOT IN %s
                  AND f.is_latest = TRUE;
            """, (f'%{dom_element_name}%', found_element_ids))
            
            event_listener = cur.fetchone()

            if not event_listener:
                print(f"  [FAIL] Event listener for '{dom_element_name}' not found.")
                return
            
            workflow.append(event_listener)
            # A bit of parsing to find the function name from 'elements.parseBtn.addEventListener('click', parseProject);'
            js_function_name = event_listener['content'].split(',')[-1].strip().replace(');', '')
            print(f"  [OK] Found Event Listener in {event_listener['path']} (ID: {event_listener['id']})")
            print(f"     Content: {event_listener['content'].strip()}")
            print(f"     -> This listener calls function: '{js_function_name}'")


            # --- Step 4: Find the JS function ---
            print(f"\n[Step 4] Searching for JS function '{js_function_name}'")
            cur.execute("""
                SELECT ce.*, f.path
                FROM code_elements ce
                JOIN files f ON ce.file_id = f.id
                JOIN projects p ON f.project_id = p.id
                WHERE p.name = 'code_visualiser'
                  AND f.path = 'static/app.js'
                  AND ce.kind = 'function'
                  AND ce.name = %s
                  AND f.is_latest = TRUE;
            """, (js_function_name,))
            
            js_function = cur.fetchone()

            if not js_function or not js_function['metadata'] or 'api_calls' not in js_function['metadata']:
                print(f"  [FAIL] JS function '{js_function_name}' or its API call metadata not found.")
                return

            workflow.append(js_function)
            print(f"  [OK] Found JS function '{js_function['name']}' in {js_function['path']} (ID: {js_function['id']})")
            
            api_call = js_function['metadata']['api_calls'][0]
            api_path = api_call['path']
            api_method = api_call['method']
            print(f"     -> Makes API call: {api_method} {api_path}")

            # --- Step 5: Find the backend Python function (API endpoint) ---
            # The router might have a prefix like '/api', so we search for the latter part.
            path_for_search = '/projects/parse' # Assuming /api is a prefix in main.py
            print(f"\n[Step 5] Searching for Python API endpoint for '{api_method} {api_path}'")
            print(f"     (Searching for decorator containing '{api_method.lower()}' and path '{path_for_search}')")

            # Use a regular expression to make the search more robust against quote styles
            # e.g., @router.post("/projects/parse") or @router.post('/projects/parse')
            # The pattern looks for the method, optional whitespace, and the path in quotes.
            decorator_pattern = f"@router\\.{api_method.lower()}\\s*\\(\\s*[\"']{path_for_search}[\"']"

            cur.execute("""
                SELECT ce.*, f.path
                FROM code_elements ce
                JOIN files f ON ce.file_id = f.id
                JOIN projects p ON f.project_id = p.id
                WHERE p.name = 'code_visualiser'
                  AND ce.kind = 'function'
                  AND ce.content ~ %s -- '~' is for POSIX regex matching
                  AND f.is_latest = TRUE;
            """, (decorator_pattern,))

            python_function = cur.fetchone()
            
            if not python_function:
                print("  [FAIL] Python API endpoint not found.")
                return
            
            workflow.append(python_function)
            print(f"  [OK] Found Python function '{python_function['name']}' in {python_function['path']} (ID: {python_function['id']})")


    # --- Final Summary ---
    print("\n\n--- Workflow Discovery Complete ---")
    if not workflow:
        print("No workflow was found.")
        return

    print("Successfully traced a workflow from a button click to a backend endpoint.")
    print("Workflow Elements:")
    for i, element in enumerate(workflow, 1):
        print(f"  {i}. Type: {element['kind']}")
        print(f"     Name: {element['name']}")
        print(f"     File: {element['path']} (Lines: {element['start_line']}-{element['end_line']})")
        print(f"     Content Snippet: {element['content'].strip()}")
        print("-" * 20)


if __name__ == "__main__":
    find_workflow()
