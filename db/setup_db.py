import psycopg2
from psycopg2 import sql, extensions
import getpass
import sys

# --- Database Configuration ---
# Fill in your PostgreSQL connection details.
# The script will interactively ask for any values left as None.
DB_CONFIG = {
    "host": "localhost",      # Or your server's network IP to connect from other devices
    "port": "5432",
    "dbname": "code_visualiser_db",
    "user": "postgres",       # Your PostgreSQL username
    "password": None          # Leave as None to be asked securely at runtime
}

# --- SQL Schema Definition ---

EXTENSIONS_SQL = [
    'CREATE EXTENSION IF NOT EXISTS "pgcrypto";'
]

TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,
        file_tree JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        id SERIAL PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        stable_file_id UUID NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        is_latest BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
        path TEXT NOT NULL,
        kind TEXT,
        checksum TEXT,
        loc INTEGER,
        content TEXT,
        last_parsed_at TIMESTAMP WITH TIME ZONE,
        UNIQUE (stable_file_id, version)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS code_elements (
        id SERIAL PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        parent_id INTEGER REFERENCES code_elements(id) ON DELETE CASCADE,
        stable_element_id UUID NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        is_latest BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
        kind TEXT NOT NULL,
        name TEXT,
        content TEXT,
        checksum TEXT,
        start_line INTEGER,
        end_line INTEGER,
        metadata JSONB,
        CHECK (start_line IS NULL OR end_line IS NULL OR start_line <= end_line),
        UNIQUE (stable_element_id, version)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS project_summaries (
        project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        summary TEXT,
        last_updated_at TIMESTAMP WITH TIME ZONE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS file_summaries (
        file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        summary TEXT,
        last_updated_at TIMESTAMP WITH TIME ZONE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS element_summaries (
        element_id INTEGER PRIMARY KEY REFERENCES code_elements(id) ON DELETE CASCADE,
        summary TEXT,
        last_updated_at TIMESTAMP WITH TIME ZONE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS workflows (
        id SERIAL PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
        UNIQUE (project_id, name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_steps (
        id SERIAL PRIMARY KEY,
        workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        element_id INTEGER NOT NULL REFERENCES code_elements(id) ON DELETE CASCADE,
        step_number INTEGER NOT NULL,
        metadata JSONB,
        UNIQUE (workflow_id, step_number)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_summaries (
        workflow_id INTEGER PRIMARY KEY REFERENCES workflows(id) ON DELETE CASCADE,
        summary TEXT,
        last_updated_at TIMESTAMP WITH TIME ZONE
    );
    """
]

INDEXES_SQL = [
    """
    CREATE UNIQUE INDEX IF NOT EXISTS files_project_id_path_latest_idx 
    ON files (project_id, path) WHERE is_latest;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS code_elements_file_id_name_kind_latest_idx 
    ON code_elements (file_id, name, kind) WHERE is_latest;
    """
]

TABLE_NAMES = [
    "projects", "files", "code_elements",
    "project_summaries", "file_summaries", "element_summaries",
    "workflows", "workflow_steps", "workflow_summaries"
]

def get_db_connection_details():
    """Interactively get any missing database connection details."""
    config = DB_CONFIG.copy()
    print("Please provide your database connection details.")
    for key, value in config.items():
        if value is None:
            if key == 'password':
                config[key] = getpass.getpass(f"Enter database password for user '{config['user']}': ")
            else:
                user_input = input(f"Enter {key}: ")
                if user_input:
                    config[key] = user_input
    return config

def create_database(config):
    """Connects to the default 'postgres' db to create the target database."""
    db_to_create = config["dbname"]
    temp_config = config.copy()
    temp_config["dbname"] = "postgres"
    conn = None
    try:
        conn = psycopg2.connect(**temp_config)
        conn.set_isolation_level(extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_to_create,))
        if not cur.fetchone():
            print(f"Database '{db_to_create}' not found. Creating...")
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_to_create)))
            print(f"Database '{db_to_create}' created successfully.")
        else:
            print(f"Database '{db_to_create}' already exists.")
            
        cur.close()
    except psycopg2.OperationalError as e:
        print(f"Error: Could not connect to PostgreSQL server on '{temp_config['host']}'.", file=sys.stderr)
        print("Please ensure PostgreSQL is running and credentials are correct.", file=sys.stderr)
        return False
    except psycopg2.Error as e:
        print(f"An error occurred during database creation: {e}", file=sys.stderr)
        return False
    finally:
        if conn:
            conn.close()
    return True

def create_tables(config):
    """Creates the tables in the specified database."""
    conn = None
    try:
        conn = psycopg2.connect(**config)
        cur = conn.cursor()
        
        print("\nEnabling extensions...")
        for extension_sql in EXTENSIONS_SQL:
            cur.execute(extension_sql)

        print("\nCreating tables...")
        for table_sql in TABLES_SQL:
            cur.execute(table_sql)
        
        print("\nCreating indexes...")
        for index_sql in INDEXES_SQL:
            cur.execute(index_sql)
        
        conn.commit()
        print("Schema setup complete.")
        cur.close()
    except psycopg2.Error as e:
        print(f"An error occurred during table creation: {e}", file=sys.stderr)
        return False
    finally:
        if conn:
            conn.close()
    return True

def verify_tables(config):
    """Verify that all expected tables exist in the database."""
    conn = None
    all_tables_found = True
    try:
        conn = psycopg2.connect(**config)
        cur = conn.cursor()
        
        for table_name in TABLE_NAMES:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);",
                (table_name,)
            )
            if not cur.fetchone()[0]:
                print(f"Verification FAILED: Table '{table_name}' was not found.", file=sys.stderr)
                all_tables_found = False
            else:
                 print(f"  [✓] Found table '{table_name}'")

        cur.close()
    except psycopg2.Error as e:
        print(f"An error occurred during verification: {e}", file=sys.stderr)
        return False
    finally:
        if conn:
            conn.close()
    
    return all_tables_found


def main():
    """Main function to set up the database."""
    print("--- Code Helper Database Setup ---")
    
    config = get_db_connection_details()
    
    if not create_database(config):
        sys.exit(1)

    if not create_tables(config):
        sys.exit(1)
        
    print("\n--- Verifying Setup ---")
    if verify_tables(config):
        print("\nDatabase setup completed successfully!")
    else:
        print("\nDatabase setup failed. Please check the errors above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main() 