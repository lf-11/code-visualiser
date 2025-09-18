import re
import sys
import os
import psycopg2
import psycopg2.extras

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from workflows.api_function_mapper import (
    get_all_definitions as get_all_py_definitions,
    get_imports,
    build_alias_map,
    build_call_graph,
    print_call_tree
)

def get_py_api_endpoints(all_py_definitions):
    """Finds Python API endpoints and constructs their full paths."""
    endpoints = {}
    # From main.py, we know the prefix is /api
    API_PREFIX = "/api"

    for definition in all_py_definitions:
        if definition['kind'] == 'function' and '@router' in definition['content']:
            # Updated regex to handle extra parameters in the decorator (like response_model)
            match = re.search(r'@router\.(get|post|put|delete|patch)\("([^"]*)"', definition['content'])
            if match:
                method, path = match.groups()
                # Normalize path parameters for matching, e.g., /users/{user_id} -> /users/{VAR}
                normalized_path = re.sub(r'\{[^}]+\}', '{VAR}', path)
                full_path = API_PREFIX + normalized_path
                endpoints[f"{method.upper()} {full_path}"] = definition
    return endpoints

def get_js_functions(cur, project_id):
    """Fetches all Javascript functions from the database."""
    cur.execute("""
        SELECT ce.id, ce.name, ce.content, f.path, ce.metadata
        FROM code_elements ce
        JOIN files f ON ce.file_id = f.id
        WHERE f.project_id = %s AND ce.kind = 'function' AND f.path LIKE '%%.js' AND f.is_latest = TRUE;
    """, (project_id,))
    return cur.fetchall()

def get_all_js_elements(cur, project_id):
    """Fetches all Javascript code elements from the database."""
    cur.execute("""
        SELECT ce.id, ce.name, ce.content, f.path, ce.kind, ce.metadata
        FROM code_elements ce
        JOIN files f ON ce.file_id = f.id
        WHERE f.project_id = %s AND f.path LIKE '%%.js' AND f.is_latest = TRUE;
    """, (project_id,))
    return cur.fetchall()

def find_js_callees(caller_func, all_js_functions):
    """Finds other JS functions called by a given JS function."""
    callees = []
    caller_content = caller_func.get('content', '')
    if not caller_content:
        return []

    for potential_callee in all_js_functions:
        if potential_callee['id'] == caller_func['id']:
            continue

        callee_name = potential_callee['name'].split(' (L')[0]
        if callee_name == '(anonymous)':
            continue

        # Use regex to find function calls, avoiding variable declarations
        # This looks for the function name followed by an opening parenthesis
        pattern = r'\b' + re.escape(callee_name) + r'\s*\('
        if re.search(pattern, caller_content):
            callees.append(potential_callee)
            
    return callees


def main():
    """Traces a workflow from a Javascript API call to the Python backend."""
    print("--- Full-Stack Workflow Tracer ---")
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT id FROM projects WHERE name = 'code_visualiser' LIMIT 1;")
                project_id = cur.fetchone()['id']
                print(f"Analyzing project ID: {project_id}\n")

                # --- 1. Get all necessary data from both Python and Javascript ---
                all_py_definitions = get_all_py_definitions(cur, project_id)
                py_imports = get_imports(cur, project_id)
                all_js_elements = get_all_js_elements(cur, project_id)
                all_js_functions = [el for el in all_js_elements if el['kind'] == 'function']

                # --- 2. Build the Python call graph (reusing our perfected logic) ---
                py_elements_map = {el['id']: el for el in all_py_definitions}
                py_alias_map = build_alias_map(py_imports, all_py_definitions)
                py_call_graph = build_call_graph(all_py_definitions, py_alias_map)
                
                # --- 3. Map out the Python API endpoints ---
                py_endpoints = get_py_api_endpoints(all_py_definitions)
                print(f"Found {len(py_endpoints)} Python API endpoints.")
                # --- DIAGNOSTIC: Show discovered Python endpoints ---
                if py_endpoints:
                    print("\n--- Discovered Python Endpoints ---")
                    for endpoint_key in sorted(py_endpoints.keys()):
                        print(f"  [✓] {endpoint_key}")
                    print("------------------------------------")
                
                # --- Pre-scan for all JS API calls for diagnostics ---
                all_js_api_calls = {}
                for js_func in all_js_functions:
                    metadata = js_func.get('metadata')
                    if metadata and metadata.get('api_calls'):
                        for api_call in metadata.get('api_calls', []):
                            original_api_key = f"{api_call['method']} {api_call['path']}"
                            if original_api_key not in all_js_api_calls:
                                all_js_api_calls[original_api_key] = []
                            all_js_api_calls[original_api_key].append(f"'{js_func['name']}' in {js_func['path']}")
                
                if all_js_api_calls:
                    print("\n--- Discovered Javascript API Calls ---")
                    for original_api_key, locations in sorted(all_js_api_calls.items()):
                        # Check if this JS call matches a Python endpoint by normalizing the path
                        method, path = original_api_key.split(" ", 1)
                        normalized_path = re.sub(r'\$\{[^}]+\}', '{VAR}', path)
                        normalized_key = f"{method} {normalized_path}"
                        match_status = "[✓]" if normalized_key in py_endpoints else "[✗]"
                        print(f"  {match_status} {original_api_key}")
                        for loc in locations:
                            print(f"      - Called from: {loc}")
                    print("---------------------------------------\n")

                # --- 4. Find and trace full-stack workflows ---
                print("--- Tracing Full-Stack Workflows ---")
                found_workflows = 0
                for js_func in all_js_functions:
                    metadata = js_func.get('metadata')
                    if not (metadata and metadata.get('api_calls')):
                        continue
                    
                    for api_call in metadata.get('api_calls', []):
                        path = api_call['path']
                        method = api_call['method']
                        
                        # Normalize JS path parameters to match the Python endpoint keys
                        normalized_path = re.sub(r'\$\{[^}]+\}', '{VAR}', path)
                        normalized_api_key = f"{method} {normalized_path}"
                        
                        # Check if this JS API call matches a known Python endpoint
                        if normalized_api_key in py_endpoints:
                            found_workflows += 1
                            py_endpoint_def = py_endpoints[normalized_api_key]
                            
                            original_api_key = f"{method} {path}"
                            print(f"\n--- Workflow #{found_workflows}: {original_api_key} ---")
                            
                            # a. Trace the JS part upwards from the function making the API call
                            js_func_name = js_func['name'].split(' (L')[0]
                            js_func_file = js_func['path']
                            
                            elements_in_file = [el for el in all_js_elements if el['path'] == js_func_file]
                            dom_elements_in_file = [el for el in elements_in_file if el['kind'] == 'dom_element_definition']
                            
                            # Find the best possible trigger for the JS function call.
                            potential_triggers = []
                            for usage_element in elements_in_file:
                                if usage_element['id'] == js_func['id']:
                                    continue
                                
                                content = usage_element.get('content', '')
                                if re.search(r'\b' + re.escape(js_func_name) + r'\b', content):
                                    # Score potential triggers to find the most likely one.
                                    # Direct event listeners are the most likely candidates.
                                    score = 0
                                    if 'addEventListener' in content:
                                        score += 2  # Highest score for direct listeners
                                    if usage_element['kind'] == 'expression statement':
                                        score += 1
                                    
                                    # Find an associated DOM element mentioned in the same code block.
                                    found_dom_el = None
                                    for dom_el in dom_elements_in_file:
                                        if dom_el['name'] and re.search(r'\b' + re.escape(dom_el['name']) + r'\b', content):
                                            found_dom_el = dom_el
                                            break
                                    
                                    if found_dom_el:
                                        potential_triggers.append({'score': score, 'usage': usage_element, 'dom': found_dom_el})

                            if potential_triggers:
                                # Select the best trigger based on the highest score.
                                best_trigger = sorted(potential_triggers, key=lambda x: x['score'], reverse=True)[0]
                                usage_element = best_trigger['usage']
                                found_dom_el = best_trigger['dom']
                                content = usage_element.get('content', '')

                                selector = found_dom_el['metadata'].get('selector', '')
                                print(f"DOM Element: '{found_dom_el['name']}' (Selector: {selector})")
                                print(f"  File: {found_dom_el['path']}")
                                print("   ↓ (Triggers Event)")
                                
                                # Describe the trigger more accurately.
                                trigger_type = "Event Listener" if 'addEventListener' in content else usage_element['kind']
                                print(f"Trigger: ({trigger_type}) '{usage_element['name']}'")
                                print(f"  File: {usage_element['path']}")
                                print("   ↓ (Calls Handler)")
                            else:
                                # Fallback if no DOM-related trigger is found.
                                print("(No specific DOM trigger found)")
                                print("   ↓ (Calls Handler)")

                            # b. Print the JS function itself and its own callees (downwards trace)
                            print(f"JS Function: '{js_func_name}'")
                            print(f"  File: {js_func['path']}")
                            
                            js_callees = find_js_callees(js_func, all_js_functions)
                            if js_callees:
                                print("  JS Call Tree:")
                                for callee in js_callees:
                                    callee_name = callee['name'].split(' (L')[0]
                                    print(f"    -> Calls '{callee_name}' in {callee['path']}")
                            
                            print(f"   ↓ (Makes API Call: {original_api_key})")

                            # c. Print the Python endpoint that receives the call
                            print(f"Python Endpoint: '{py_endpoint_def['name']}'")
                            print(f"  File: {py_endpoint_def['path']}")
                            
                            # d. Print the Python call tree
                            if py_call_graph.get(py_endpoint_def['id']):
                                print("  Backend Call Tree:")
                                for callee_id in py_call_graph[py_endpoint_def['id']]:
                                    print_call_tree(callee_id, py_call_graph, py_elements_map, 2, {py_endpoint_def['id']})
                            else:
                                print("  (Endpoint makes no further calls)")
                            
                            print("\n" + "="*50)

                if found_workflows == 0:
                    print("\nNo matching full-stack workflows were found.")
                    print("Check the diagnostic prints above for potential mismatches in API paths or methods.")

    except psycopg2.OperationalError as e:
        print(f"\nDB Error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

if __name__ == "__main__":
    main()
