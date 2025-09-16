import ast
import io
import tokenize
try:
    from .base_parser import BaseParser
except ImportError:
    class BaseParser:
        def parse(self, content: str):
            raise NotImplementedError

class PythonParser(BaseParser):
    def parse(self, content: str):
        """
        Parses Python code into a hierarchical list of logical blocks (elements).
        """
        if not content.strip():
            return []

        elements_tree = self._parse_to_hierarchy(content)
        return self._enrich_tree(elements_tree, content)

    def _enrich_tree(self, elements_tree, source_code):
        """
        Recursively walks the element tree and adds the raw content of each element.
        """
        source_lines = source_code.splitlines(True)

        def process_node(element):
            start = element['start_line'] - 1
            end = element['end_line']
            element['content'] = "".join(source_lines[start:end])

            for child in element.get("children", []):
                process_node(child)
            
            return element

        return [process_node(element) for element in elements_tree]

    def _flatten_and_enrich(self, elements_tree, source_code):
        """
        Flattens the hierarchical tree from the parser and adds the raw
        content of each element.
        """
        flat_list = []
        source_lines = source_code.splitlines(True)

        def process_element(element):
            start = element['start_line'] - 1
            end = element['end_line']
            
            new_element = {
                "kind": element["kind"],
                "name": element["name"],
                "start_line": element["start_line"],
                "end_line": element["end_line"],
                "content": "".join(source_lines[start:end]),
                "metadata": element.get("metadata", {}),
            }
            flat_list.append(new_element)

            for child in element.get("children", []):
                process_element(child)

        for element in elements_tree:
            process_element(element)
        
        return flat_list

    def _parse_api_decorator(self, decorator_node):
        """
        Parses a decorator node to see if it's a FastAPI/Flask-style route.
        e.g., @app.get("/path")
        """
        if not isinstance(decorator_node, ast.Call):
            return None
        if not isinstance(decorator_node.func, ast.Attribute):
            return None

        # Check for common HTTP methods
        method = decorator_node.func.attr.lower()
        if method not in {'get', 'post', 'put', 'delete', 'patch', 'options', 'head', 'trace'}:
            return None

        # Extract path from the first argument, which should be a string constant
        if not decorator_node.args or not isinstance(decorator_node.args[0], ast.Constant):
            return None
        
        path = decorator_node.args[0].value
        if not isinstance(path, str):
            return None

        return {"method": method.upper(), "path": path}

    def _get_internal_calls(self, body_nodes):
        """
        Walks the body of a function to find all function and method calls.
        """
        calls = set()
        # We wrap the body in a Module so ast.walk has a valid tree structure
        for node in ast.walk(ast.Module(body=body_nodes)):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        return sorted(list(calls))

    def _parse_to_hierarchy(self, source_code: str):
        """
        Parses Python code into a hierarchical structure of logical blocks.
        This is the parser logic from your batch_analyzer.py.
        """
        elements = []
        
        def process_node(node, is_child=False, parent_name=None):
            element = {
                "kind": "unknown", "name": "", "children": [],
                "start_line": node.lineno,
                "end_line": getattr(node, 'end_lineno', node.lineno),
                "metadata": {}
            }

            if hasattr(node, 'decorator_list') and node.decorator_list:
                element['start_line'] = node.decorator_list[0].lineno

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name_str = ast.get_source_segment(source_code, node).strip()
                unique_name = f"{name_str} (L{node.lineno})"
                element.update(kind='import', name=unique_name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = 'function'
                metadata = {}
                
                # Parse decorators for API endpoints
                if hasattr(node, 'decorator_list'):
                    api_routes = []
                    for decorator in node.decorator_list:
                        api_info = self._parse_api_decorator(decorator)
                        if api_info:
                            api_routes.append(api_info)
                    
                    if api_routes:
                        metadata['api_routes'] = api_routes
                
                # Parse function body for internal calls
                internal_calls = self._get_internal_calls(node.body)
                if internal_calls:
                    metadata['internal_calls'] = internal_calls

                func_name = node.name
                if parent_name:
                    func_name = f"{parent_name}.{func_name}"

                unique_name = f"{func_name} (L{node.lineno})"
                element.update(kind=kind, name=unique_name, metadata=metadata)

                for child_node in node.body:
                    if isinstance(child_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        child_element = process_node(child_node, is_child=True, parent_name=func_name)
                        if child_element:
                            element['children'].append(child_element)
            elif isinstance(node, ast.ClassDef):
                class_name = node.name
                if parent_name:
                    class_name = f"{parent_name}.{class_name}"
                
                unique_name = f"{class_name} (L{node.lineno})"
                element.update(kind='class', name=unique_name)
                
                for child_node in node.body:
                    child_element = process_node(child_node, is_child=True, parent_name=class_name)
                    if child_element:
                        element['children'].append(child_element)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                name_str = ast.get_source_segment(source_code, node).strip().splitlines()[0]
                unique_name = f"{name_str} (L{node.lineno})"
                element.update(kind='class_variable' if is_child else 'variable_definition', 
                             name=unique_name)
            else:
                if is_child:
                    return None 
                name_str = ast.get_source_segment(source_code, node).strip().splitlines()[0]
                unique_name = f"{name_str} (L{node.lineno})"
                element.update(kind='statement_block', name=unique_name)

            if len(element['name']) > 80:
                element['name'] = element['name'][:77] + "..."
            
            return element

        try:
            tree = ast.parse(source_code)
            for node in tree.body:
                element = process_node(node)
                if element:
                    elements.append(element)
        except (SyntaxError, ValueError) as e:
            return [{"kind": "error", "name": f"Syntax Error: {e}", "start_line": 1, "end_line": 1}]

        code_lines = set()
        q = list(elements)
        while q:
            el = q.pop(0)
            code_lines.update(range(el['start_line'], el['end_line'] + 1))
            q.extend(el.get('children', []))

        comment_blocks = []
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source_code).readline)
            current_block = None
            for tok in tokens:
                if tok.type == tokenize.COMMENT and tok.start[0] not in code_lines:
                    if current_block and tok.start[0] == current_block['end_line'] + 1:
                        current_block['end_line'] += 1
                    else:
                        unique_name = f"{tok.string.strip()} (L{tok.start[0]})"
                        current_block = {'kind': 'comment_block', 'name': unique_name, 'start_line': tok.start[0], 'end_line': tok.start[0]}
                        comment_blocks.append(current_block)
        except (tokenize.TokenError, IndentationError):
            pass

        all_elements = elements + comment_blocks
        all_elements.sort(key=lambda x: x['start_line'])
        return all_elements 

if __name__ == '__main__':
    import os
    import sys
    import pprint
    import json

    # --- Test Configuration ---
    # Set the path to the Python file you want to test.
    TEST_FILE_PATH = "examples/api_server.py"

    VERBOSE_TEST = True

    # --- Test Harness ---
    if not os.path.exists(TEST_FILE_PATH):
        print(f"\n[!] Test file not found at '{TEST_FILE_PATH}'.")
        try:
            dir_name = os.path.dirname(TEST_FILE_PATH)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(TEST_FILE_PATH, 'w', encoding='utf-8') as f:
                f.write("""
from fastapi import FastAPI

app = FastAPI()

def get_db_connection():
    # In a real app, this would connect to a database.
    print("Getting DB connection")
    return {"status": "connected"}

@app.get("/users/{user_id}")
async def read_user(user_id: int):
    \"\"\"
    Retrieves a user from the database.
    \"\"\"
    db = get_db_connection()
    return {"user_id": user_id, "db": db}

@app.post("/items/")
def create_item(item: dict):
    # This is a regular function, not async
    print(f"Creating item: {item}")
    return {"status": "created"}

class SomeService:
    def process_data(self, data):
        return data.upper()

@app.put("/data/")
def update_data(data: str):
    service = SomeService()
    processed = service.process_data(data)
    return {"processed": processed}
""")
            print(f"    A sample file has been created for you at '{TEST_FILE_PATH}'.")
            print("    Please run the script again to test with the new file.")
        except Exception as e:
            print(f"    Could not create a sample file: {e}")
        sys.exit(1)

    print(f"\n--- Parsing Test File: {os.path.basename(TEST_FILE_PATH)} ---")

    try:
        with open(TEST_FILE_PATH, 'r', encoding='utf-8') as f:
            py_content = f.read()

        parser = PythonParser()
        parsed_elements = parser.parse(py_content)

        print(f"\nFound {len(parsed_elements)} code element(s):")
        print("-" * 60)

        if VERBOSE_TEST:
            for i, el in enumerate(parsed_elements, 1):
                print(f"[{i}] {el['kind']}: {el['name']} @ L{el['start_line']}-L{el['end_line']}")
                if el.get('metadata'):
                    metadata_str = json.dumps(el['metadata'], indent=4)
                    indented_metadata = "    " + metadata_str.replace("\n", "\n    ")
                    print(indented_metadata)
        else:
            pprint.pprint(parsed_elements)

        print("-" * 60)

        file_nonempty = os.path.getsize(TEST_FILE_PATH) > 0
        if file_nonempty and parsed_elements:
            print("\n[✓] Success: Parser ran and extracted code elements.")
        elif file_nonempty:
            print("\n[!] Notice: Parser ran, but no code elements were found.")
        else:
            print("\n[!] Notice: The test file appears to be empty.")

    except FileNotFoundError:
        print(f"\nError: The file '{TEST_FILE_PATH}' was not found.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}") 