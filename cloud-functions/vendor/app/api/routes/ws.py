from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["ws"])
connections = []

@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    connections.append(websocket)
    try:
        await websocket.send_json({"type": "connected", "payload": {"message": "ws connected"}})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connections:
            connections.remove(websocket)

async def broadcast(event):
    dead = []
    for ws in connections:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in connections:
            connections.remove(ws)


def broadcast_sync(event_type: str, channel: str = "jd"):
    """同步环境广播（在同步线程中调用 async broadcast）"""
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(broadcast({"type": event_type, "channel": channel}))
    except Exception:
        pass
