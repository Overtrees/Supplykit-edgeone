from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.core.response import ok, fail
from app.core.scheduler import get_status as scheduler_status, start as restart_scheduler

router = APIRouter(prefix="/api/sync-tasks", tags=["sync_tasks"])

@router.get("")
def list_sync_tasks(db = get_db()):
    try:
        data = db.table("sync_tasks").select("*").order("id", desc=True).execute().data
        return ok(data)
    except Exception:
        return ok([])

@router.get("/scheduler")
def get_scheduler_status():
    return scheduler_status()
