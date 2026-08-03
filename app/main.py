from fastapi import FastAPI

from app.routers import task

app = FastAPI(title="Task Manager API")

app.include_router(task.router)
