from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from api import projects as projects_api, files as files_api
from core.database import close_pool

# Load .env file at the application's entry point
load_dotenv()

app = FastAPI(title="Code Visualiser API")

# Include API routers
app.include_router(projects_api.router, prefix="/api", tags=["projects"])
app.include_router(files_api.router, prefix="/api", tags=["files"])


# Mount static files (for the frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("shutdown")
def shutdown_event():
    close_pool()

@app.get("/", include_in_schema=False)
async def read_index():
    return FileResponse('static/index.html') 