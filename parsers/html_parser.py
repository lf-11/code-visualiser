import sys

# This try/except block allows the script to be run directly for testing,
# bypassing the relative import, while still allowing it to be imported
# by other modules in the package.
try:
    from .base_parser import BaseParser
except ImportError:
    class BaseParser:
        def parse(self, content: str):
            raise NotImplementedError

try:
    from bs4 import BeautifulSoup
    import bs4
except ImportError:
    BeautifulSoup = None
    bs4 = None

# Prefer lxml.html for robust sourceline support
try:
    from lxml import html as LH
    from lxml import etree as ET
    LXML_INSTALLED = True
except ImportError:
    LH = None
    ET = None
    LXML_INSTALLED = False


class HTMLParser(BaseParser):
    """
    Parses HTML files to find interactive and structural UI elements.
    Prefers lxml for reliable line numbers and hierarchy.
    """
    def parse(self, content: str):
        if not content or not content.strip():
            return []

        if LXML_INSTALLED and LH is not None:
            try:
                return self._extract_with_lxml(content)
            except Exception:
                pass  # Fall back to BeautifulSoup below if available

        if BeautifulSoup:
            return self._extract_with_bs4(content)

        print("Warning: No HTML parsing library available. Install lxml or beautifulsoup4.", file=sys.stderr)
        return []

    def _extract_with_lxml(self, content: str):
        doc = LH.fromstring(content)
        elements = []
        element_map = {} # To store elements by their lxml object id

        def is_ui_element(el):
            tag = el.tag if isinstance(el.tag, str) else ""
            tag = tag.lower()
            if not tag:
                return False

            # Interactive elements
            if tag in ("button", "select", "textarea"):
                return True
            if tag == "input":
                t = (el.get("type") or "text").strip().lower()
                if t not in ("hidden",): # Exclude hidden inputs, keep others
                    return True
            if el.get("onclick") is not None:
                return True
            if tag == "a":
                href = (el.get("href") or "").strip()
                if el.get("onclick") is not None or href in ("", "#") or href.lower().startswith("javascript:"):
                    return True

            # Structural & Text elements
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "label", "p"):
                 # Only include if it has some stripped text content
                text_content = "".join(el.itertext()).strip()
                return bool(text_content)
            
            # Allow divs as potential parents, but only if they contain something meaningful
            if tag == "div":
                return len(el) > 0 or bool((el.text or "").strip())
                
            return False

        for el in doc.iter():
            try:
                if not is_ui_element(el):
                    continue

                tag = el.tag.lower()
                snippet = LH.tostring(el, encoding="unicode", with_tail=False).strip()
                start_line = getattr(el, "sourceline", 1)
                end_line = start_line + snippet.count("\n")

                classes = el.get("class")
                if isinstance(classes, str):
                    classes = classes.split()

                text_content = "".join(el.itertext()).strip()
                display_text = text_content[:40] + "..." if len(text_content) > 40 else text_content

                name_parts = [f"<{tag}>"]
                if el.get("id"):
                    name_parts.append(f"id='{el.get('id')}'")
                if display_text:
                    name_parts.append(f"'{display_text}'")
                
                # Add line number to ensure uniqueness against the DB constraint.
                name = " ".join(name_parts) + f" (L{start_line})"

                # Generate a unique ID for this element based on its position in the tree
                el_id = f"{tag}_{start_line}"
                id_counter = 2
                while el_id in [e['id'] for e in elements]:
                    el_id = f"{tag}_{start_line}_{id_counter}"
                    id_counter += 1

                metadata = {
                    "tag": tag,
                    "id": el.get("id"),
                    "classes": classes,
                    "onclick": el.get("onclick"),
                }
                if tag == "a":
                    metadata["href"] = el.get("href")
                if tag == "input":
                    metadata["type"] = el.get("type")
                    metadata["value"] = el.get("value")
                if tag == "label":
                    metadata["for"] = el.get("for")

                metadata = {k: v for k, v in metadata.items() if v is not None}
                
                # Find parent by traversing up until a recognized element is found
                parent_id = None
                parent_el = el.getparent()
                while parent_el is not None:
                    if parent_el in element_map:
                        parent_id = element_map[parent_el]['id']
                        break
                    parent_el = parent_el.getparent()

                element_data = {
                    "id": el_id, # Unique ID for parenting
                    "kind": "ui_element",
                    "name": name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content": snippet,
                    "metadata": metadata,
                    "parent_id": parent_id
                }
                elements.append(element_data)
                element_map[el] = element_data
            except Exception:
                continue

        # Build the tree structure from the flat list
        elements_by_id = {el['id']: el for el in elements}
        root_elements = []
        for el in elements:
            el['children'] = []
            parent_id = el.get('parent_id')
            if parent_id in elements_by_id:
                elements_by_id[parent_id]['children'].append(el)
            else:
                root_elements.append(el)
        
        # Clean up temporary IDs used for tree construction
        def cleanup_ids(element_list):
            for el in element_list:
                el.pop('id', None)
                el.pop('parent_id', None)
                if el['children']:
                    cleanup_ids(el['children'])
        
        cleanup_ids(root_elements)
        root_elements.sort(key=lambda x: x["start_line"])
        return root_elements

    def _extract_with_bs4(self, content: str):
        # We avoid relying on BeautifulSoup's sourceline; many builds don't expose it.
        # We will return elements with start_line = 1 as a conservative fallback.
        soup = BeautifulSoup(content, "lxml") if LXML_INSTALLED else BeautifulSoup(content, "html.parser")
        elements = []
        name_counts = {} # Used to disambiguate names when line numbers aren't available

        query = "button, input, select, textarea, [onclick], a, h1, h2, h3, h4, h5, h6, label, p"
        for tag in soup.select(query):
            try:
                # Filter out elements that don't meet our criteria
                if tag.name == "a" and not tag.has_attr("onclick"):
                    href = tag.get("href", "").strip()
                    if href and href not in ("#", "") and not href.lower().startswith("javascript:"):
                        continue
                if tag.name == "input" and tag.get("type", "text").lower() == "hidden":
                    continue
                if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6", "label", "p") and not tag.get_text(strip=True):
                    continue


                snippet = str(tag)
                text_content = tag.get_text(strip=True) or tag.get("value", "")
                display_text = text_content[:30] + "..." if len(text_content) > 30 else text_content

                name_parts = [f"<{tag.name}>"]
                if tag.get("id"):
                    name_parts.append(f"id='{tag.get('id')}'")
                if display_text:
                    name_parts.append(f"'{display_text}'")
                name = " ".join(name_parts)

                # Disambiguate name for bs4 since we don't have reliable line numbers
                if name in name_counts:
                    name_counts[name] += 1
                    name = f"{name} ({name_counts[name]})"
                else:
                    name_counts[name] = 1

                metadata = {
                    "tag": tag.name,
                    "id": tag.get("id"),
                    "classes": tag.get("class"),
                    "onclick": tag.get("onclick"),
                }
                if tag.name == "a":
                    metadata["href"] = tag.get("href")
                if tag.name == "input":
                    metadata["type"] = tag.get("type")
                    metadata["value"] = tag.get("value")
                if tag.name == "label":
                    metadata["for"] = tag.get("for")
                metadata = {k: v for k, v in metadata.items() if v is not None}

                elements.append({
                    "kind": "ui_element",
                    "name": name,
                    "start_line": 1,
                    "end_line": 1 + snippet.count("\n"),
                    "content": snippet,
                    "metadata": metadata,
                    "children": [] # Ensure bs4 output is consistent with the expected structure
                })
            except Exception:
                continue

        return elements


if __name__ == '__main__':
    import os
    import pprint
    from collections import defaultdict

    # --- Test Configuration ---
    # Set the path to the HTML file you want to test.
    # We default to the project's own index.html for self-testing.
    TEST_FILE_PATH = os.path.join(os.path.dirname(__file__), '..', 'static', 'index.html')

    VERBOSE_TEST = True

    def print_env_diagnostics():
        print("=" * 72)
        print("HTML Parser Diagnostics")
        print("=" * 72)
        print(f"- Python: {sys.version.split()[0]}")
        if bs4:
            print(f"- beautifulsoup4: {getattr(bs4, '__version__', 'unknown')}")
        else:
            print("- beautifulsoup4: NOT INSTALLED")
        if LXML_INSTALLED and ET is not None:
            lxml_ver = getattr(ET, "LXML_VERSION", None)
            libxml_ver = getattr(ET, "LIBXML_VERSION", None)
            print(f"- lxml: INSTALLED")
            if lxml_ver:
                print(f"  - lxml version: {'.'.join(map(str, lxml_ver))}")
            if libxml_ver:
                print(f"  - libxml2 version: {'.'.join(map(str, libxml_ver))}")
        else:
            print("- lxml: NOT INSTALLED")
        print("-" * 72)

    def run_bs4_sourceline_diagnostic():
        if not BeautifulSoup:
            print("[bs4] Skipped (not installed)")
            return
        print("[bs4] sourceline diagnostic with parser='lxml':")
        test_html = "<html>\n<body>\n  <button id='test'>Test</button>\n</body>\n</html>"
        try:
            soup = BeautifulSoup(test_html, "lxml") if LXML_INSTALLED else BeautifulSoup(test_html, "html.parser")
            tag = soup.find("button")
            line = getattr(tag, "sourceline", None)
            print(f"  - Tag.sourceline => {line}")
        except Exception as e:
            print(f"  - Exception: {e}")

    def run_lxml_sourceline_diagnostic():
        if not LXML_INSTALLED or LH is None:
            print("[lxml] Skipped (not installed)")
            return
        print("[lxml] sourceline diagnostic using lxml.html:")
        test_html = "<html>\n<body>\n  <button id='test'>Test</button>\n</body>\n</html>"
        try:
            root = LH.fromstring(test_html)
            btns = root.xpath("//button")
            if not btns:
                print("  - No <button> found in diagnostic HTML")
                return
            line = getattr(btns[0], "sourceline", None)
            print(f"  - Element.sourceline => {line}")
        except Exception as e:
            print(f"  - Exception: {e}")

    def print_element_tree(elements):
        if not elements:
            return
            
        print("\n--- Parsed Element Tree ---")
        
        elements_by_id = {el['id']: el for el in elements}
        children_by_parent_id = defaultdict(list)
        root_elements = []

        for el in elements:
            parent_id = el.get('parent_id')
            if parent_id and parent_id in elements_by_id:
                children_by_parent_id[parent_id].append(el)
            else:
                root_elements.append(el)
        
        def print_node(element, indent=""):
            parent_info = f"(parent: {element.get('parent_id')})" if element.get('parent_id') else "(root)"
            print(f"{indent}- {element['name']} @ L{element['start_line']} {parent_info}")
            
            children = children_by_parent_id.get(element['id'], [])
            children.sort(key=lambda x: x['start_line'])
            for child in children:
                print_node(child, indent + "  ")

        for root in root_elements:
            print_node(root)
        print("-" * 60)


    print_env_diagnostics()
    # run_bs4_sourceline_diagnostic() # Disabled for brevity
    # run_lxml_sourceline_diagnostic() # Disabled for brevity

    if not TEST_FILE_PATH or not os.path.exists(TEST_FILE_PATH):
        print(f"\n[!] TEST_FILE_PATH is not set or does not exist.")
        print(f"    Current value: '{TEST_FILE_PATH}'")
        print(f"    Please set TEST_FILE_PATH at the top of {__file__} and rerun.")
        sys.exit(1)

    print(f"\n--- Parsing Test File: {os.path.abspath(TEST_FILE_PATH)} ---")

    try:
        with open(TEST_FILE_PATH, 'r', encoding='utf-8') as f:
            html_content = f.read()

        parser = HTMLParser()
        parsed_elements = parser.parse(html_content)

        print(f"\nFound {len(parsed_elements)} UI element(s):")
        
        if VERBOSE_TEST:
            print_element_tree(parsed_elements)
            
            # The summary is a bit redundant with the tree view now, so we can simplify it.
            # print("\n--- Element Summary ---")
            # for el in parsed_elements:
            #     md = el.get("metadata", {})
            #     details = f"tag={md.get('tag')}, id={md.get('id')}, parent={el.get('parent_id')}"
            #     print(f"- L{el['start_line']}: {el['name']} ({details})")
        else:
            pprint.pprint(parsed_elements)
        print("-" * 60)

        # Success criteria with verification
        file_nonempty = os.path.getsize(TEST_FILE_PATH) > 0
        if file_nonempty and parsed_elements:
            print("\n[✓] Success: Parser ran and extracted UI elements.")
        elif file_nonempty:
            print("\n[!] Notice: Parser ran, but no UI elements were found.")
        else:
            print("\n[!] Notice: The test file appears to be empty.")

    except FileNotFoundError:
        print(f"\nError: The file '{TEST_FILE_PATH}' was not found.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()