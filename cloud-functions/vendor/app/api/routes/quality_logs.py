from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.core.response import ok, fail

router = APIRouter(prefix="/api/quality-logs", tags=["quality_logs"])

@router.get("")
def list_quality_logs(db = get_db()):
    data = db.table("quality_logs").select("*").order("id", desc=True).execute().data
    return ok(data)
