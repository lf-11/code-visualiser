import re
import sys
import os
import psycopg2
import psycopg2.extras

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection

# --- Data Fetching and Pre-computation ---

def get_all_definitions(cur, project_id):
    """Fetches all Python functions, classes, and methods for a project."""
    cur.execute("""
        SELECT ce.id, ce.name, ce.content, f.path, ce.kind
        FROM code_elements ce
        JOIN files f ON ce.file_id = f.id
        WHERE f.project_id = %s AND ce.kind IN ('function', 'class') AND f.path LIKE '%%.py' AND f.is_latest = TRUE;
    """, (project_id,))
    
    definitions = []
    for el in cur.fetchall():
        clean_name = el['name'].split(' (L')[0]
        definitions.append({'id': el['id'], 'name': clean_name, 'content': el['content'], 'path': el['path'], 'kind': el['kind']})
    return definitions

def get_imports(cur, project_id):
    """Fetches all Python import statements for a given project."""
    cur.execute("""
        SELECT ce.name, f.path FROM code_elements ce JOIN files f ON ce.file_id = f.id
        WHERE f.project_id = %s AND ce.kind = 'import' AND f.path LIKE '%%.py' AND f.is_latest = TRUE;
    """, (project_id,))
    return cur.fetchall()

def resolve_module_path(importing_file, module_str, all_project_paths):
    """Resolves a module string like 'core.database' or '.base_parser' to a file path."""
    if module_str.startswith('.'):
        current_dir = os.path.dirname(importing_file)
        levels_up = len(module_str) - len(module_str.lstrip('.'))
        path_parts = module_str.lstrip('.').split('.')
        base_path_parts = importing_file.split(os.path.sep)[:-(levels_up)]
        final_path = os.path.join(*base_path_parts, *path_parts) + ".py"
        return os.path.normpath(final_path)
    
    path_as_file = module_str.replace('.', os.path.sep) + ".py"
    if path_as_file in all_project_paths:
        return path_as_file
    return module_str.replace('.', os.path.sep)

def build_alias_map(imports, all_definitions):
    """Builds a map to resolve imported names to their original definition or module path."""
    alias_map = {}
    definitions_map = {(el['path'], el['name']): el for el in all_definitions}
    all_paths = {el['path'] for el in all_definitions}

    for imp in imports:
        importing_file, import_str = imp['path'], imp['name'].split(' (L')[0]
        
        match_from = re.match(r"from\s+([\w\.]+)\s+import\s+(.+)", import_str)
        match_import = re.match(r"import\s+([\w\.]+)(?:\s+as\s+(\w+))?", import_str)

        if match_from:
            module_str, names_str = match_from.groups()
            for name_part in names_str.split(','):
                name_part = name_part.strip()
                alias_match = re.match(r"(\w+)\s+as\s+(\w+)", name_part)
                original_name, alias_name = alias_match.groups() if alias_match else (name_part, name_part)

                resolved_path = resolve_module_path(importing_file, module_str, all_paths)
                original_def = definitions_map.get((resolved_path, original_name))

                if original_def:
                    alias_map[(importing_file, alias_name)] = {'type': 'definition', 'def': original_def}
                else:
                    package_module_path = os.path.join(resolved_path, original_name + ".py")
                    if package_module_path in all_paths:
                        alias_map[(importing_file, alias_name)] = {'type': 'module', 'path': package_module_path}
        elif match_import:
            module_str, alias = match_import.groups()
            alias = alias or module_str
            resolved_path = resolve_module_path(importing_file, module_str, all_paths)
            alias_map[(importing_file, alias)] = {'type': 'module', 'path': resolved_path}
    return alias_map

# --- Call Graph Construction ---

def build_call_graph(all_definitions, alias_map):
    """Builds a call graph by resolving functions, modules, and class methods."""
    call_graph = {el['id']: set() for el in all_definitions}
    definitions_map = {(el['path'], el['name']): el for el in all_definitions}
    
    # Pre-calculate all methods for each class
    methods_by_class_id = {}
    for el in all_definitions:
        if '.' in el['name']:
            class_name = el['name'].split('.')[0]
            class_def = definitions_map.get((el['path'], class_name))
            if class_def:
                if class_def['id'] not in methods_by_class_id:
                    methods_by_class_id[class_def['id']] = []
                methods_by_class_id[class_def['id']].append(el)

    for caller in all_definitions:
        if not caller['content']: continue

        # 1. Resolve direct and module-level references
        refs = re.findall(r'\b([\w\.]+)\b', caller['content'])
        for ref in set(refs):
            parts = ref.split('.')
            if len(parts) == 1:
                alias_info = alias_map.get((caller['path'], ref))
                if alias_info and alias_info['type'] == 'definition':
                    call_graph[caller['id']].add(alias_info['def']['id'])
                else:
                    local_def = definitions_map.get((caller['path'], ref))
                    if local_def: call_graph[caller['id']].add(local_def['id'])
            elif len(parts) > 1:
                module_alias, member_name = parts[0], parts[1]
                alias_info = alias_map.get((caller['path'], module_alias))
                if alias_info and alias_info['type'] == 'module':
                    target_def = definitions_map.get((alias_info['path'], member_name))
                    if target_def: call_graph[caller['id']].add(target_def['id'])
        
        # 2. Resolve instance method calls on imported classes
        imported_classes = []
        for key, val in alias_map.items():
            file_path, _ = key
            if file_path == caller['path'] and val['type'] == 'definition' and val['def']['kind'] == 'class':
                imported_classes.append(val['def'])

        method_calls = re.findall(r'\.\b(\w+)\b\(', caller['content'])
        for method_name in set(method_calls):
            for imp_class in imported_classes:
                for method in methods_by_class_id.get(imp_class['id'], []):
                    if method['name'].endswith(f".{method_name}"):
                        call_graph[caller['id']].add(method['id'])

        # 3. --- NEW: Resolve internal method calls (self.method) ---
        if '.' in caller['name']:
            caller_class_name = caller['name'].split('.')[0]
            internal_method_calls = re.findall(r'self\.(\w+)', caller['content'])
            for method_name in set(internal_method_calls):
                target_method_name = f"{caller_class_name}.{method_name}"
                target_def = definitions_map.get((caller['path'], target_method_name))
                if target_def:
                    call_graph[caller['id']].add(target_def['id'])


    # Final cleanup: remove self-references and sort for consistent output
    for caller_id, callees in call_graph.items():
        callees.discard(caller_id)
    return {k: sorted(list(v)) for k, v in call_graph.items()}

# --- Main Execution and Output ---

def get_api_endpoints(all_definitions):
    """Filters the list of all definitions to find API endpoints."""
    return [d for d in all_definitions if d['kind'] == 'function' and '@router' in d['content']]

def print_call_tree(element_id, call_graph, elements_map, indent_level, visited_path):
    """Recursively prints the function and method call tree."""
    if element_id in visited_path:
        print(f"{'  ' * indent_level}└─ (Recursive call to '{elements_map[element_id]['name']}')")
        return
    visited_path.add(element_id)
    
    element = elements_map[element_id]
    kind = element['kind'].capitalize()
    print(f"{'  ' * indent_level}└─ {kind}: {element['name']}")
    print(f"{'  ' * indent_level}   File: {element['path']}")

    for callee_id in call_graph.get(element_id, []):
        print_call_tree(callee_id, call_graph, elements_map, indent_level + 1, visited_path.copy())

def main():
    """Main function to build the full call graph and display API endpoint trees."""
    print("--- API Call Graph Mapper (v4 - Correct Heuristic) ---")
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT id, name FROM projects ORDER BY name;")
                # Simplified: analyzing first project.
                project_id = cur.fetchone()['id']
                
                print(f"\nAnalyzing project ID: {project_id}...")
                
                all_definitions = get_all_definitions(cur, project_id)
                imports = get_imports(cur, project_id)
                
                elements_map = {el['id']: el for el in all_definitions}

                alias_map = build_alias_map(imports, all_definitions)
                call_graph = build_call_graph(all_definitions, alias_map)
                endpoints = get_api_endpoints(all_definitions)

                print("\n--- Analysis Results ---")
                for endpoint in endpoints:
                    print(f"\nAPI Endpoint: {endpoint['name']}")
                    print(f"  File: {endpoint['path']}")
                    
                    if call_graph.get(endpoint['id']):
                        print("  Call Tree:")
                        for callee_id in call_graph[endpoint['id']]:
                            print_call_tree(callee_id, call_graph, elements_map, 2, {endpoint['id']})
                    else:
                        print("  Calls: None found.")
                    print("-" * 30)

    except psycopg2.OperationalError as e:
        print(f"\nDB Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
