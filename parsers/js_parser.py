import sys
# import pkg_resources # Removed
# try: # Removed section
#     print(f"--- Python Executable: {sys.executable}", file=sys.stderr)
#     print(f"--- Loaded tree-sitter version: {pkg_resources.get_distribution('tree-sitter').version}", file=sys.stderr)
# except Exception as e:
#     print(f"--- Could not determine tree-sitter version: {e}", file=sys.stderr)

try:
    from .base_parser import BaseParser
except ImportError:
    class BaseParser:
        def parse(self, content: str):
            raise NotImplementedError

try:
    from tree_sitter import Language, Parser
    from tree_sitter_languages import get_language
except ImportError:
    # Set to None so we can handle the absence of the library gracefully.
    Parser = None
    get_language = None

# --- Tree-sitter Language Setup ---
# This is done once when the module is loaded for efficiency.
if Parser:
    JS_LANGUAGE = get_language('javascript')
    # Parser instances are created per JavascriptParser to avoid reuse issues.

    # --- Tree-sitter Queries for Metadata Extraction ---
    # These queries are now broader, omitting text-based predicates to avoid C-level bugs.
    # The filtering logic is moved into the Python code.
    API_CALL_QUERY = JS_LANGUAGE.query("""
    [
      (call_expression
        function: (identifier) @call_name
        arguments: (arguments (string) @path . (object) @options)
      )
      (call_expression
        function: (identifier) @call_name
        arguments: (arguments (template_string) @path . (object) @options)
      )
      (call_expression
        function: (identifier) @call_name
        arguments: (arguments (string) @path)
      )
      (call_expression
        function: (identifier) @call_name
        arguments: (arguments (template_string) @path)
      )
      (call_expression
        function: (member_expression
          object: (identifier) @lib
          property: (property_identifier) @method
        )
        arguments: (arguments (string) @path)
      )
      (call_expression
        function: (member_expression
          object: (identifier) @lib
          property: (property_identifier) @method
        )
        arguments: (arguments (template_string) @path)
      )
    ]
    """)

    # This query finds potential event listener calls and assignments.
    EVENT_LISTENER_QUERY = JS_LANGUAGE.query("""
    [
      (call_expression
        function: (member_expression
          object: (_) @selector_obj
          property: (property_identifier) @method_name
        )
        arguments: (arguments
          (string) @event_type
          .
          (_) @handler
        )
      )
      (assignment_expression
        left: (member_expression
          object: (_) @selector_obj
          property: (property_identifier) @prop
        )
        right: (_) @handler
      )
    ]
    """)

    DOM_ELEMENT_QUERY = JS_LANGUAGE.query("""
    (call_expression
        function: (member_expression
            object: (identifier) @obj
            property: (property_identifier) @method
        )
        arguments: (arguments . (_) @selector_node)
    ) @dom_call
    (#match? @method "^(getElementById|querySelector|querySelectorAll)$")
    (#eq? @obj "document")
    """)

    DOM_LOADED_WRAPPER_QUERY = JS_LANGUAGE.query("""
    (call_expression
      function: (member_expression
        object: (identifier) @obj
        property: (property_identifier) @prop
      )
      arguments: (arguments
        (string) @event
        .
        (arrow_function body: (statement_block) @body)
      )
    ) @wrapper
    (#eq? @obj "document")
    (#eq? @prop "addEventListener")
    """)

else:
    # We no longer need a global parser instance.
    pass

class JavascriptParser(BaseParser):

    def __init__(self):
        self._ts_parser = None
        if Parser:
            self._ts_parser = Parser()
            self._ts_parser.set_language(JS_LANGUAGE)

    def parse(self, content: str):
        """
        Parses Javascript code into a flat list of logical blocks (elements).
        """
        if not self._ts_parser:
            print("Warning: Javascript parsing skipped.", file=sys.stderr)
            print("Please install tree-sitter libraries: pip install 'tree-sitter>=0.21.0,<0.22.0' tree-sitter-languages", file=sys.stderr)
            return []

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
            del element['node'] # Clean up node object before returning

            for child in element.get("children", []):
                process_node(child)
            
            return element

        return [process_node(element) for element in elements_tree]

    def _flatten_and_enrich(self, elements_tree, source_code):
        """
        Flattens the hierarchical tree and adds the raw content of each element.
        """
        flat_list = []
        source_lines = source_code.splitlines(True)

        def process_element(element):
            # Skip error elements after they've been handled
            if element.get("kind") == "error":
                return

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

    def _get_first_line(self, text: str) -> str:
        """Safely gets the first non-empty line of a string."""
        stripped = text.strip()
        return stripped.splitlines()[0] if stripped else ""

    def _parse_to_hierarchy(self, source_code: str):
        """
        Parses JavaScript code into a flat list of elements using a multi-pass,
        query-based approach with tree-sitter.
        """
        try:
            tree = self._ts_parser.parse(bytes(source_code, "utf8"))
            original_root = tree.root_node
        except Exception as e:
            return [{"kind": "error", "name": f"Tree-sitter parsing error: {e}", "start_line": 1, "end_line": 1}]

        root_node = original_root

        def get_node_text(node):
            return source_code[node.start_byte:node.end_byte]

        # --- Pass 0: Unwrap DOMContentLoaded if present (robust AST scan) ---
        # If the whole file is wrapped, treat the inside of the listener as the root.
        def find_dom_loaded_body(root):
            stack = [root]
            file_size = len(source_code.encode("utf8")) or 1
    
            while stack:
                n = stack.pop()
                # Look for: document.addEventListener('DOMContentLoaded', <handler>)
                if n.type == "call_expression":
                    callee = n.child_by_field_name("function")
                    if callee and callee.type == "member_expression":
                        obj = callee.child_by_field_name("object")
                        prop = callee.child_by_field_name("property")
                        if obj and prop and get_node_text(obj) == "document" and get_node_text(prop) == "addEventListener":
                            args = n.child_by_field_name("arguments")
                            if args:
                                arg_nodes = [c for c in args.children if c.is_named]
                                if len(arg_nodes) >= 2:
                                    evt_node, handler_node = arg_nodes[0], arg_nodes[1]
                                    evt_text = get_node_text(evt_node).strip("'\"`")
                                    if evt_text == "DOMContentLoaded":
                                        # Support both arrow functions and classic functions
                                        if handler_node.type in ("arrow_function", "function"):
                                            body = handler_node.child_by_field_name("body")
                                            # Prefer a statement_block body; fall back to handler body if needed
                                            if body and body.type == "statement_block":
                                                # Heuristic: ensure this wrapper likely spans (almost) the whole file
                                                call_span = n.end_byte - n.start_byte
                                                if call_span / file_size > 0.8:
                                                    return body
                                                # If not, still return to allow nested parsing
                                                return body
                # Continue DFS
                for c in n.children:
                    stack.append(c)
            return None
    
        maybe_body = find_dom_loaded_body(original_root)
        if maybe_body:
            root_node = maybe_body

        elements_by_node_id = {}
        processed_node_ids = set()
        descendant_query = JS_LANGUAGE.query("(_) @node")

        # --- Pass 1: Find all functions (named, anonymous, arrow) everywhere in the file ---
        func_query = JS_LANGUAGE.query("[(function_declaration) @func (function) @func (arrow_function) @func]")
        
        for capture in func_query.captures(root_node):
            node = capture[0]
            
            name = "(anonymous)"
            if node.type == 'function_declaration':
                name_node = node.child_by_field_name('name')
                if name_node: name = get_node_text(name_node)
            elif node.parent and node.parent.type == 'variable_declarator':
                name_node = node.parent.child_by_field_name('name')
                if name_node: name = get_node_text(name_node)
            
            if name == "(anonymous)":
                name = f"(anonymous) (L{node.start_point[0] + 1}:{node.start_point[1]})"

            if node.id not in elements_by_node_id:
                elements_by_node_id[node.id] = {
                    "kind": "function", "name": name,
                    "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
                    "metadata": {}, "node": node, "children": []
                }
                processed_node_ids.add(node.id)
                for desc_capture in descendant_query.captures(node):
                    processed_node_ids.add(desc_capture[0].id)
        
        # --- Pass 2: Find DOM element definitions ---
        # This must run before the generic block pass to correctly identify definitions
        # inside object literals before the whole object is marked as a single block.
        for cap_node, cap_name in DOM_ELEMENT_QUERY.captures(root_node):
            if cap_name != 'dom_call':
                continue
            call_node = cap_node
            if not call_node or call_node.id in processed_node_ids:
                continue

            name = "(unassigned)"
            parent = call_node.parent
            element_node = call_node

            if parent:
                if parent.type == 'variable_declarator':
                    name_node = parent.child_by_field_name('name')
                    if name_node: name = get_node_text(name_node)
                    element_node = parent
                elif parent.type == 'pair': # Inside an object literal
                    key_node = parent.child_by_field_name('key')
                    if key_node: name = get_node_text(key_node)
                    element_node = parent
                elif parent.type == 'assignment_expression':
                    left_node = parent.child_by_field_name('left')
                    if left_node: name = get_node_text(left_node)
                    element_node = parent

            if name == "(unassigned)":
                # Walk up to find a nearby declarator or object pair if any
                anc = parent
                while anc:
                    if anc.type == 'variable_declarator':
                        name_node = anc.child_by_field_name('name')
                        if name_node: name = get_node_text(name_node)
                        element_node = anc
                        break
                    if anc.type == 'pair':
                        key_node = anc.child_by_field_name('key')
                        if key_node: name = get_node_text(key_node)
                        element_node = anc
                        break
                    anc = anc.parent
                if name == "(unassigned)":
                    name = get_node_text(call_node)

            # Extract selector and method directly from the call expression
            selector_text = ""
            args_node = call_node.child_by_field_name('arguments')
            if args_node:
                arg_nodes = [child for child in args_node.children if child.is_named]
                if arg_nodes:
                    selector_text = get_node_text(arg_nodes[0]).strip("'\"`")

            method_text = ""
            func_node = call_node.child_by_field_name('function')
            if func_node and func_node.type == 'member_expression':
                prop_node = func_node.child_by_field_name('property')
                if prop_node:
                    method_text = get_node_text(prop_node)

            elements_by_node_id[element_node.id] = {
                "kind": "dom_element_definition",
                "name": name,
                "start_line": element_node.start_point[0] + 1,
                "end_line": element_node.end_point[0] + 1,
                "metadata": {"selector": selector_text, "method": method_text},
                "node": element_node, "children": []
            }
            processed_node_ids.add(element_node.id)
            for desc_capture in descendant_query.captures(element_node):
                processed_node_ids.add(desc_capture[0].id)


        # --- Pass 3: Find event listeners ---
        # This also runs before the generic block pass.
        main_listener_query = JS_LANGUAGE.query("""
        [
          (call_expression
            function: (member_expression)
          ) @listener_call
          (assignment_expression
            left: (member_expression)
          ) @listener_assignment
        ]
        """)
        q_selector = JS_LANGUAGE.query("(member_expression object: (_) @selector)")
        q_method = JS_LANGUAGE.query("(member_expression property: (property_identifier) @method)")
        q_args = JS_LANGUAGE.query("(arguments) @args")
        calls_in_handler_query = JS_LANGUAGE.query("""(call_expression function: (identifier) @callee)""")

        for match in main_listener_query.captures(root_node):
            node, capture_name = match
            if node.id in processed_node_ids:
                continue
            
            selector_node, handler_node, event_type_text = None, None, None

            if capture_name == 'listener_call':
                function_node = node.child_by_field_name('function')
                if not function_node or function_node.type != 'member_expression': continue
                method_captures = q_method.captures(function_node)
                if not method_captures: continue
                method_name = get_node_text(method_captures[0][0])
                if method_name != 'addEventListener': continue
                args_captures = q_args.captures(node)
                if not args_captures: continue
                args_node = args_captures[0][0]
                arg_nodes = [child for child in args_node.children if child.is_named]
                if len(arg_nodes) < 2: continue
                event_type_node, handler_node = arg_nodes[0], arg_nodes[1]
                event_type_text = get_node_text(event_type_node).strip("'\"`")
                selector_captures = q_selector.captures(function_node)
                if selector_captures: selector_node = selector_captures[0][0]

            elif capture_name == 'listener_assignment':
                left_node = node.child_by_field_name('left')
                if not left_node or left_node.type != 'member_expression': continue
                prop_captures = q_method.captures(left_node)
                if not prop_captures: continue
                prop_name = get_node_text(prop_captures[0][0])
                if not prop_name.startswith('on'): continue
                event_type_text = prop_name.replace('on', '')
                handler_node = node.child_by_field_name('right') or None
                selector_captures = q_selector.captures(left_node)
                if selector_captures: selector_node = selector_captures[0][0]

            if not handler_node or not event_type_text: 
                continue

            selector_hint = get_node_text(selector_node) if selector_node else ""
            event_info = {"event": event_type_text, "selector_hint": selector_hint}
            listener_name = f"{event_type_text} on '{selector_hint}'"
            handler_name = get_node_text(handler_node) if handler_node.type == 'identifier' else '(inline handler)'
            
            elements_by_node_id[node.id] = {
                "kind": "event_listener", "name": listener_name,
                "start_line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1,
                "metadata": { "event": event_type_text, "selector_hint": selector_hint, "handler_name": handler_name },
                "node": node, "children": []
            }
            processed_node_ids.add(node.id)
            for desc_capture in descendant_query.captures(node):
                processed_node_ids.add(desc_capture[0].id)

            # --- Annotate the handler function with event listener metadata ---
            func_element = None
            if handler_node.type == 'identifier':
                handler_name_text = get_node_text(handler_node)
                for el in elements_by_node_id.values():
                    if el.get('kind') == 'function' and el['name'] == handler_name_text:
                        func_element = el; break
            elif handler_node.id in elements_by_node_id:
                func_element = elements_by_node_id[handler_node.id]

            if func_element:
                meta = func_element['metadata']
                if 'event_listeners' not in meta: meta['event_listeners'] = []
                meta['event_listeners'].append(event_info)
                if func_element['name'].startswith('(anonymous)'):
                    start_line, start_col = func_element['start_line'], func_element['node'].start_point[1]
                    func_element['name'] = f"(handler for '{event_type_text}') (L{start_line}:{start_col})"
                if handler_node.type in ('function', 'arrow_function'):
                    for cap in calls_in_handler_query.captures(handler_node):
                        callee_node = cap[0] if isinstance(cap, tuple) else getattr(cap, 'node', None)
                        if not callee_node: continue
                        callee_name = get_node_text(callee_node)
                        for el in elements_by_node_id.values():
                            if el['name'] == callee_name:
                                em = el['metadata']
                                if 'event_listeners' not in em: em['event_listeners'] = []
                                em['event_listeners'].append(event_info)

        # --- Pass 4: Find all other top-level code blocks ---
        # This pass now runs last, creating generic elements for any top-level code
        # that hasn't already been identified as a function, listener, or DOM definition.
        # It creates one element per statement to avoid greedy grouping.
        for node in sorted(root_node.children, key=lambda n: n.start_point[0]):
            if node.id in processed_node_ids:
                continue

            # If any child of this node has already been processed, it means this is a
            # container (like a variable declaration with DOM elements). We should skip
            # creating a generic element for it and let the specific child elements show.
            child_ids = {c[0].id for c in descendant_query.captures(node)}
            if any(cid in processed_node_ids for cid in child_ids):
                continue

            name = self._get_first_line(get_node_text(node))
            kind = node.type.replace('_', ' ') # e.g., expression_statement -> expression statement
            if node.type in ['lexical_declaration', 'variable_declaration']:
                kind = 'variable_declaration'

            elements_by_node_id[node.id] = {
                "kind": kind, "name": name,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "metadata": {}, "node": node, "children": []
            }

        # --- Pass 5: Find API calls ---
        main_api_query = JS_LANGUAGE.query("(call_expression) @api_call")
        q_api_function = JS_LANGUAGE.query("(call_expression function: (_) @func)")
        q_api_args = JS_LANGUAGE.query("(call_expression arguments: (arguments) @args)")
        # Queries for finding method in fetch options
        q_pair_key = JS_LANGUAGE.query("(pair key: (property_identifier) @k)")
        q_pair_value = JS_LANGUAGE.query("(pair value: (string) @v)")
        q_pair_nodes = JS_LANGUAGE.query("(pair) @pair")

        for func_element in elements_by_node_id.values():
            if func_element.get('kind') != 'function': continue
            func_node = func_element['node']
            api_calls = []

            for match in main_api_query.captures(func_node):
                call_node = match[0]

                # 1. Identify the function being called
                func_capture = q_api_function.captures(call_node)
                if not func_capture: continue
                
                api_func_node = func_capture[0][0]
                api_func_text = get_node_text(api_func_node)

                # 2. Get arguments
                args_capture = q_api_args.captures(call_node)
                if not args_capture: continue
                args_node = args_capture[0][0]
                arg_nodes = [child for child in args_node.children if child.is_named]
                if not arg_nodes: continue

                path_node = arg_nodes[0]
                path = get_node_text(path_node).strip("'\"`")
                if 'API_BASE_URL' in path:
                    path = path.split('}')[-1]

                options_node = arg_nodes[1] if len(arg_nodes) > 1 else None

                # 3. Check for 'fetch'
                if api_func_text == 'fetch':
                    method = 'GET'
                    if options_node and options_node.type == 'object':
                        for pair_capture in q_pair_nodes.captures(options_node):
                            pair_node = pair_capture[0]
                            
                            key_captures = q_pair_key.captures(pair_node)
                            value_captures = q_pair_value.captures(pair_node)
                            
                            if key_captures and value_captures:
                                key_node = key_captures[0][0]
                                value_node = value_captures[0][0]
                                
                                if get_node_text(key_node) == 'method':
                                    method = get_node_text(value_node).strip("'\"")
                                    break # Found it
                    api_calls.append({"method": method.upper(), "path": path, "library": "fetch"})

                # 4. Check for 'axios'
                elif api_func_node.type == 'member_expression' and get_node_text(api_func_node.child_by_field_name('object')) == 'axios':
                    axios_method = get_node_text(api_func_node.child_by_field_name('property')).upper()
                    api_calls.append({"method": axios_method, "path": path, "library": "axios"})

            if api_calls:
                func_element['metadata']['api_calls'] = api_calls

        # --- Pass 6: Build the hierarchy ---
        all_nodes = list(elements_by_node_id.values())
        node_map = {el['node'].id: el for el in all_nodes}
        
        root_elements = []
        for el in all_nodes:
            parent_node = el['node'].parent
            # Walk up the tree to find the nearest function/declaration parent that we have parsed
            while parent_node:
                if parent_node.id in node_map:
                    node_map[parent_node.id]['children'].append(el)
                    break
                parent_node = parent_node.parent
            
            if not parent_node:
                root_elements.append(el)


        # --- Enrichment Pass: Create synthetic click handlers for form submissions ---
        for el in all_nodes:
            new_listeners = []
            if 'event_listeners' in el.get('metadata', {}):
                for listener in el['metadata']['event_listeners']:
                    if listener.get('event') == 'submit':
                        form_selector = listener.get('selector_hint', '')
                        if form_selector:
                            # Create a synthetic 'click' event for a submit button inside that form.
                            # This helps the workflow builder connect a button click to the form's handler.
                            synthetic_listener = {
                                "event": "click",
                                "selector_hint": f"{form_selector} button[type=submit]",
                                "synthetic": True
                            }
                            new_listeners.append(synthetic_listener)
            if new_listeners:
                el['metadata']['event_listeners'].extend(new_listeners)

        root_elements.sort(key=lambda x: x['start_line'])
        return root_elements

if __name__ == '__main__':
    import os
    import pprint
    import json

    print("Running JS Parser")

    # --- Test Configuration ---
    # Set the path to the Javascript file you want to test.
    TEST_FILE_PATH = "static/app.js"

    VERBOSE_TEST = True

    def print_ast(content, start_line=None, end_line=None):
        """Prints the AST for a specific portion of the code for debugging."""
        if not Parser:
            return
        
        print("\n--- Abstract Syntax Tree (AST) Diagnostic ---")
        lines = content.splitlines()
        snippet = content
        if start_line is not None and end_line is not None:
            snippet = "\n".join(lines[start_line-1:end_line])
            print(f"Analyzing snippet from L{start_line}-L{end_line}:\n```javascript\n{snippet}\n```")
        else:
            print("Analyzing full file content (may be long).")

        local_parser = Parser()
        local_parser.set_language(JS_LANGUAGE)
        tree = local_parser.parse(bytes(snippet, "utf8"))
        
        def walk_tree(node, indent=""):
            print(f"{indent}{node.type} [{node.start_point} - {node.end_point}]")
            for child in node.children:
                walk_tree(child, indent + "  ")
        
        walk_tree(tree.root_node)
        print("---------------------------------------------")

    if not os.path.exists(TEST_FILE_PATH):
        print(f"\n[!] Test file not found at '{TEST_FILE_PATH}'.")
        print("    Please update the TEST_FILE_PATH variable in this script.")
        sys.exit(1)

    print(f"\n--- Parsing Test File: {os.path.basename(TEST_FILE_PATH)} ---")

    try:
        with open(TEST_FILE_PATH, 'r', encoding='utf-8') as f:
            js_content = f.read()

        if not Parser:
            print("\n[!] tree-sitter libraries not installed. Cannot run test.")
            sys.exit(1)
        
        parser = JavascriptParser()
        parsed_elements = parser.parse(js_content)

        # --- Summary of Found Elements ---
        print("\n--- Parser Summary ---")
        summary = {}
        for el in parsed_elements:
            kind = el.get('kind', 'unknown')
            summary[kind] = summary.get(kind, 0) + 1
        
        if summary:
            for kind, count in sorted(summary.items()):
                print(f"Found {count} element(s) of kind '{kind}'")
        else:
            print("No elements were extracted.")
        print("----------------------")


        print(f"\nFound a total of {len(parsed_elements)} code element(s). Detailed view:")
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

        # --- Final Check ---
        file_nonempty = os.path.getsize(TEST_FILE_PATH) > 0
        has_elements = bool(parsed_elements)
        has_metadata = any(el.get('metadata') for el in parsed_elements)

        if file_nonempty and has_elements and has_metadata:
            print("\n[✓] Success: Parser ran, extracted code elements, and found metadata.")
        elif file_nonempty and has_elements:
            print("\n[!] Notice: Parser ran and extracted elements, but found NO metadata.")
            print("    This likely means the queries or Python filtering in '_parse_to_hierarchy' need adjustment.")
        elif file_nonempty:
            print("\n[!] Notice: Parser ran, but no code elements were found.")
        else:
            print("\n[!] Notice: The test file appears to be empty.")

    except FileNotFoundError:
        print(f"\nError: The file '{TEST_FILE_PATH}' was not found.")
    except Exception:
        print(f"\nAn unexpected error occurred. Full traceback:", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)