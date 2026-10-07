from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from receiver import MeshReceiver

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
receiver = MeshReceiver()

app = FastAPI(title="ESP32 Mesh Dashboard")
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "dashboard.html")


@app.get("/health")
async def health():
    return receiver.get_health()


@app.get("/nodes")
async def list_nodes():
    return {"nodes": receiver.list_nodes()}


@app.get("/nodes/{node_id}")
async def get_node(node_id: str):
    node = receiver.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


@app.get("/nodes/{node_id}/history")
async def get_history(node_id: str, since: str | None = None, limit: int = Query(default=100, ge=1, le=1000)):
    history = receiver.get_history(node_id, since=since, limit=limit)
    return {"nodeId": node_id, "samples": history}


@app.websocket("/live")
async def live_updates(websocket: WebSocket):
    await websocket.accept()
    receiver.add_client(websocket)
    await websocket.send_json({
        "type": "status",
        "status": "connected",
        "gatewayConnected": receiver.connected,
    })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        receiver.remove_client(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
