import re
import sys
import os
import psycopg2
import psycopg2.extras
import json

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from workflows.api_function_mapper import (
    print_call_tree
)

# --- Configuration ---
OUTPUT_FILE_PATH = "workflow_trace.json"
# ---

def get_py_api_endpoints(all_py_definitions):
    """Finds Python API endpoints and constructs their full paths."""
    endpoints = {}
    # From main.py, we know the prefix is /api
    API_PREFIX = "/api"

    for definition in all_py_definitions:
        if definition['kind'] == 'function' and '@router' in definition['content']:
            # Updated regex to handle extra parameters in the decorator (like response_model)
            match = re.search(r'@router\.(get|post|put|delete|patch)\s*\(\s*"([^"]+)"', definition['content'])
            if match:
                method, path = match.groups()
                # Normalize path parameters for matching, e.g., /users/{user_id} -> /users/{VAR}
                normalized_path = re.sub(r'\{[^}]+\}', '{VAR}', path)
                full_path = API_PREFIX + normalized_path
                endpoints[f"{method.upper()} {full_path}"] = definition
    return endpoints

def get_all_py_elements(cur, project_id):
    """Fetches all Python code elements from the database."""
    cur.execute("""
        SELECT ce.id, ce.name, ce.content, f.path, ce.kind, ce.metadata
        FROM code_elements ce
        JOIN files f ON ce.file_id = f.id
        WHERE f.project_id = %s AND f.path LIKE '%%.py' AND f.is_latest = TRUE;
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

def build_call_graph(all_elements, is_python=True):
    """Pre-computes the entire call graph (callers and callees for every element)."""
    lang = 'Python' if is_python else 'Javascript'
    print(f"Building {lang} call graph for {len(all_elements)} elements...")
    
    graph = {el['id']: {'callers': set(), 'callees': set()} for el in all_elements}
    
    # We only care about elements that can be called (functions, classes)
    potential_callees = [el for el in all_elements if el['kind'] in ('function', 'class')]

    for i, caller in enumerate(all_elements):
        if (i + 1) % 100 == 0:
            print(f"  Processed {i+1}/{len(all_elements)} {lang} elements...")

        caller_content = caller.get('content', '')
        if not caller_content:
            continue
            
        # In Python, statement_blocks can call functions, but we don't trace their callees further
        if is_python and caller['kind'] == 'statement_block':
            pass # Allow finding callees, but the trace function will stop there

        for callee in potential_callees:
            if caller['id'] == callee['id']:
                continue

            callee_name = callee['name'].split(' (L')[0]
            if not callee_name or callee_name == '(anonymous)':
                continue

            pattern = r'\b' + re.escape(callee_name) + r'\b'
            if re.search(pattern, caller_content):
                graph[caller['id']]['callees'].add(callee['id'])
                graph[callee['id']]['callers'].add(caller['id'])

    print(f"{lang} call graph build complete.")
    return graph


def trace_py_element(element_id, all_py_elements_map, py_call_graph, trace_cache):
    """Recursively traces Python callers and callees using a cache to prevent re-tracing."""
    element = all_py_elements_map[element_id]
    if element_id in trace_cache:
        # If it's a placeholder, we have a recursion.
        if trace_cache[element_id].get("is_tracing"):
            return {"id": element['id'], "name": element['name'], "kind": element['kind'], "path": element['path'], "recursive": True}
        # It's a fully computed node. Return a lightweight reference instead of the full object.
        return {
            "id": element['id'],
            "name": element['name'],
            "kind": element['kind'],
            "path": element['path'],
            "is_reference": True
        }

    # Put a placeholder in the cache to detect recursion
    trace_cache[element_id] = {"is_tracing": True}
    
    print(f"  [Py] Tracing: {element['kind']} '{element['name']}' in {element['path']}")

    node = {
        "id": element['id'],
        "name": element['name'],
        "kind": element['kind'],
        "path": element['path'],
        "callers": [],
        "callees": []
    }

    # Find and trace callers
    for caller_id in sorted(list(py_call_graph[element_id]['callers'])):
        node['callers'].append(trace_py_element(caller_id, all_py_elements_map, py_call_graph, trace_cache))

    # Find and trace callees, but don't trace further down from a statement block
    if element['kind'] != 'statement_block':
        for callee_id in sorted(list(py_call_graph[element_id]['callees'])):
            node['callees'].append(trace_py_element(callee_id, all_py_elements_map, py_call_graph, trace_cache))

    # Replace the placeholder with the fully traced node
    trace_cache[element_id] = node
    return node

def trace_js_element(element_id, all_js_elements_map, js_call_graph, dom_elements_map, trace_cache):
    """Recursively traces Javascript callers and callees for a given element using a pre-built graph."""
    element = all_js_elements_map[element_id]
    if element_id in trace_cache:
        # If it's a placeholder, we have a recursion.
        if trace_cache[element_id].get("is_tracing"):
            return {"id": element['id'], "name": element['name'], "kind": element['kind'], "path": element['path'], "recursive": True}
        # It's a fully computed node. Return a lightweight reference instead of the full object.
        return {
            "id": element['id'],
            "name": element['name'],
            "kind": element['kind'],
            "path": element['path'],
            "is_reference": True
        }

    # Put a placeholder in the cache to detect recursion
    trace_cache[element_id] = {"is_tracing": True}
    
    print(f"  [JS] Tracing: {element['kind']} '{element['name']}' in {element['path']}")
    
    node = {
        "id": element['id'],
        "name": element['name'],
        "kind": element['kind'],
        "path": element['path'],
        "callers": [],
        "callees": []
    }

    # Special handling for event listeners
    if element['kind'] == 'expression statement':
        content = element.get('content', '')
        for dom_el_name, dom_el in dom_elements_map.items():
            if dom_el_name and re.search(r'\b' + re.escape(dom_el_name) + r'\b', content):
                node['triggered_by_dom_element'] = {
                    "id": dom_el['id'],
                    "name": dom_el['name'],
                    "kind": dom_el['kind'],
                    "selector": dom_el['metadata'].get('selector', '')
                }
                # Stop tracing up from DOM elements, but save the result in the cache
                trace_cache[element_id] = node
                return node

    # Find and trace callers
    for caller_id in sorted(list(js_call_graph[element_id]['callers'])):
         node['callers'].append(trace_js_element(caller_id, all_js_elements_map, js_call_graph, dom_elements_map, trace_cache))

    # Find and trace callees for functions
    if element['kind'] == 'function':
        for callee_id in sorted(list(js_call_graph[element_id]['callees'])):
            node['callees'].append(trace_js_element(callee_id, all_js_elements_map, js_call_graph, dom_elements_map, trace_cache))
            
    # Replace the placeholder with the fully traced node
    trace_cache[element_id] = node
    return node


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
                all_py_elements = get_all_py_elements(cur, project_id)
                all_js_elements = get_all_js_elements(cur, project_id)
                
                all_py_elements_map = {el['id']: el for el in all_py_elements}
                all_js_elements_map = {el['id']: el for el in all_js_elements}
                
                py_functions = [el for el in all_py_elements if el['kind'] == 'function']
                js_functions = [el for el in all_js_elements if el['kind'] == 'function']
                dom_elements = [el for el in all_js_elements if el['kind'] == 'dom_element_definition']
                dom_elements_map = {el['name'].split(' (L')[0]: el for el in dom_elements}

                # --- NEW: Pre-build call graphs for performance ---
                py_call_graph = build_call_graph(all_py_elements, is_python=True)
                js_call_graph = build_call_graph(all_js_elements, is_python=False)

                # --- 2. Map out the Python API endpoints ---
                py_endpoints = get_py_api_endpoints(py_functions)
                print(f"\nFound {len(py_endpoints)} Python API endpoints.")
                if not py_endpoints:
                    print("No Python API endpoints found. Exiting.")
                    return

                # --- 3. Find and trace full-stack workflows for each endpoint ---
                workflows = []
                print("\n--- Tracing Full-Stack Workflows ---")

                py_trace_cache = {}
                js_trace_cache = {}
                for api_key, endpoint_def in py_endpoints.items():
                    print(f"\nTracing workflow for: {api_key}")
                    
                    workflow = {
                        "workflow_name": api_key,
                        "endpoint": {
                            "id": endpoint_def['id'],
                            "name": endpoint_def['name'],
                            "kind": endpoint_def['kind'],
                            "path": endpoint_def['path']
                        },
                        "python_trace": None,
                        "javascript_trace": []
                    }

                    # a. Trace the Python side
                    print(" -> Tracing Python backend...")
                    workflow['python_trace'] = trace_py_element(endpoint_def['id'], all_py_elements_map, py_call_graph, py_trace_cache)

                    # b. Find the corresponding JS call and trace it
                    print(" -> Tracing Javascript frontend...")
                    found_js_link = False
                    for js_func in js_functions:
                        metadata = js_func.get('metadata')
                        if not (metadata and metadata.get('api_calls')):
                            continue
                        
                        for api_call in metadata.get('api_calls', []):
                            path = api_call['path']
                            method = api_call['method']
                            
                            normalized_path = re.sub(r'\$\{[^}]+\}', '{VAR}', path)
                            normalized_api_key = f"{method.upper()} {normalized_path}"

                            if normalized_api_key == api_key:
                                found_js_link = True
                                print(f"   Found JS entry point: '{js_func['name']}'")
                                js_trace_root = trace_js_element(js_func['id'], all_js_elements_map, js_call_graph, dom_elements_map, js_trace_cache)
                                workflow['javascript_trace'].append(js_trace_root)
                    
                    if not found_js_link:
                        print("   No corresponding Javascript API call found for this endpoint.")

                    workflows.append(workflow)

                # --- 4. Write output to JSON file ---
                try:
                    with open(OUTPUT_FILE_PATH, 'w') as f:
                        json.dump(workflows, f, indent=2)
                    
                    # Verification check
                    if os.path.exists(OUTPUT_FILE_PATH) and os.path.getsize(OUTPUT_FILE_PATH) > 0:
                        print(f"\nSuccessfully wrote {len(workflows)} workflows to '{OUTPUT_FILE_PATH}'")
                    else:
                        print(f"\nError: Failed to write workflows to '{OUTPUT_FILE_PATH}'. File is missing or empty.", file=sys.stderr)

                except IOError as e:
                    print(f"\nError writing to file '{OUTPUT_FILE_PATH}': {e}", file=sys.stderr)


    except psycopg2.OperationalError as e:
        print(f"\nDB Error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

if __name__ == "__main__":
    main()
