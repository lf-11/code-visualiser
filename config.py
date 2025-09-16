"""
Global configuration for the Code Visualiser project.
"""

# --- Parsing Configuration ---

# A set of directory names to completely ignore during project parsing.
# The parser will not descend into these directories.
# This is useful for ignoring virtual environments, build artifacts, etc.
IGNORED_DIRECTORIES = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".idea",
    "node_modules",
    "dist",
    "build",
    ".specstory",
}

# A set of file extensions to ignore.
# Files with these extensions will not be parsed.
IGNORED_FILE_EXTENSIONS = {
    ".pyc",
    ".log",
    ".tmp",
    ".swp",
    ".bak",
}

# A set of specific file names to ignore.
IGNORED_FILES = {
    ".DS_Store",
} 