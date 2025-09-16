import psycopg2.pool
import psycopg2.extras
import os
from contextlib import contextmanager
from db.setup_db import DB_CONFIG

# Use a connection pool for better performance and resource management.
connection_pool = None

def get_pool():
    global connection_pool
    if connection_pool is None:
        print("Creating new database connection pool...")
        # Register the UUID adapter globally for all connections
        psycopg2.extras.register_uuid()
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            dbname=DB_CONFIG["dbname"],
        )
    return connection_pool

@contextmanager
def get_db_connection():
    """Provides a database connection from the pool."""
    pool = get_pool()
    conn = None
    try:
        conn = pool.getconn()
        yield conn
    finally:
        if conn:
            pool.putconn(conn)

def close_pool():
    global connection_pool
    if connection_pool:
        connection_pool.closeall()
        connection_pool = None
        print("Database connection pool closed.") 