import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ai_assistant import ask_mesh_ai, get_ai_config
from insights import calculate_insights
from pydantic import BaseModel
from receiver import MeshReceiver

logger = logging.getLogger("esp_mesh_dashboard")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
receiver = MeshReceiver()
def _safe_broadcast(receiver_inst: MeshReceiver, event: dict, loop: asyncio.AbstractEventLoop):
    try:
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(receiver_inst.broadcast_event(event), loop)
    except RuntimeError:
        pass


async def _process_line(receiver_inst: MeshReceiver, line: str, transport_name: str):
    try:
        event = receiver_inst.handle_message(line)
        await receiver_inst.broadcast_event(event)
    except Exception as exc:
        logger.warning("Malformed or ignored %s gateway line: %s (%s)", transport_name, line, exc)


def _safe_dispatch_line(receiver_inst: MeshReceiver, line: str, loop: asyncio.AbstractEventLoop, transport_name: str):
    try:
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_process_line(receiver_inst, line, transport_name), loop)
    except RuntimeError:
        pass


def _serial_worker(receiver_inst: MeshReceiver, loop: asyncio.AbstractEventLoop, stop_event: threading.Event):
    import serial

    while not stop_event.is_set():
        if not receiver_inst.connected or receiver_inst.transport is None:
            serial_port = os.getenv("MESH_SERIAL_PORT")
            if not serial_port:
                receiver_inst.connected = False
                receiver_inst.gateway_message = "Serial transport selected but no serial port configured."
                if stop_event.wait(2.0):
                    break
                continue

            try:
                ser = serial.Serial(serial_port, 115200, timeout=1.0)
                receiver_inst.transport = ser
                receiver_inst.connected = True
                receiver_inst.gateway_message = f"Connected to serial gateway {serial_port}"
                _safe_broadcast(
                    receiver_inst,
                    {
                        "type": "status",
                        "status": "connected",
                        "gatewayConnected": True,
                    },
                    loop,
                )
            except Exception as exc:
                receiver_inst.connected = False
                receiver_inst.gateway_message = f"Unable to open serial port {serial_port}"
                if stop_event.wait(2.0):
                    break
                continue

        ser = receiver_inst.transport
        try:
            raw_line = ser.readline()
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            _safe_dispatch_line(receiver_inst, line, loop, "serial")

        except (serial.SerialException, OSError) as exc:
            logger.warning("Serial disconnect error: %s", exc)
            receiver_inst.connected = False
            receiver_inst.gateway_message = "Serial port disconnected"
            try:
                ser.close()
            except Exception:
                pass
            receiver_inst.transport = None
            _safe_broadcast(
                receiver_inst,
                {
                    "type": "status",
                    "status": "disconnected",
                    "gatewayConnected": False,
                },
                loop,
            )
            if stop_event.wait(2.0):
                break

    if receiver_inst.transport:
        try:
            receiver_inst.transport.close()
        except Exception:
            pass
        receiver_inst.transport = None
    receiver_inst.connected = False

def _tcp_worker(receiver_inst: MeshReceiver, loop: asyncio.AbstractEventLoop, stop_event: threading.Event):
    import socket

    while not stop_event.is_set():
        if not receiver_inst.connected or receiver_inst.transport is None:
            host = os.getenv("MESH_HOST")
            port = os.getenv("MESH_PORT")
            if not host or not port:
                receiver_inst.connected = False
                receiver_inst.gateway_message = "TCP transport configured but no host/port provided."
                if stop_event.wait(2.0):
                    break
                continue

            try:
                port_value = int(port)
                sock = socket.create_connection((host, port_value), timeout=5.0)
                sock.settimeout(1.0)
                receiver_inst.transport = sock
                receiver_inst.connected = True
                receiver_inst.gateway_message = f"Connected to gateway at {host}:{port_value}"
                _safe_broadcast(
                    receiver_inst,
                    {
                        "type": "status",
                        "status": "connected",
                        "gatewayConnected": True,
                    },
                    loop,
                )
            except (OSError, ValueError) as exc:
                receiver_inst.connected = False
                receiver_inst.gateway_message = f"Unable to connect to {host}:{port}"
                if stop_event.wait(2.0):
                    break
                continue

        sock = receiver_inst.transport
        buffer = b""
        try:
            while not stop_event.is_set():
                try:
                    chunk = sock.recv(1024)
                    if not chunk:
                        raise OSError("TCP connection closed by remote gateway")
                    buffer += chunk
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        _safe_dispatch_line(receiver_inst, line, loop, "TCP")
                except socket.timeout:
                    continue
        except (OSError, socket.error) as exc:
            logger.warning("TCP gateway disconnect error: %s", exc)
            receiver_inst.connected = False
            receiver_inst.gateway_message = "TCP gateway disconnected"
            try:
                sock.close()
            except Exception:
                pass
            receiver_inst.transport = None
            _safe_broadcast(
                receiver_inst,
                {
                    "type": "status",
                    "status": "disconnected",
                    "gatewayConnected": False,
                },
                loop,
            )
            if stop_event.wait(2.0):
                break

    if receiver_inst.transport:
        try:
            receiver_inst.transport.close()
        except Exception:
            pass
        receiver_inst.transport = None
    receiver_inst.connected = False


@asynccontextmanager
async def lifespan(app_inst: FastAPI):
    transport = os.getenv("MESH_TRANSPORT", "serial").lower()
    stop_event = threading.Event()
    worker_thread = None

    loop = asyncio.get_running_loop()
    if transport == "serial":
        worker_thread = threading.Thread(
            target=_serial_worker,
            args=(receiver, loop, stop_event),
            name="serial-gateway-worker",
            daemon=True,
        )
        worker_thread.start()
    elif transport == "tcp":
        worker_thread = threading.Thread(
            target=_tcp_worker,
            args=(receiver, loop, stop_event),
            name="tcp-gateway-worker",
            daemon=True,
        )
        worker_thread.start()
    yield

    if worker_thread:
        stop_event.set()
        worker_thread.join(timeout=2.0)


app = FastAPI(title="ESP32 Mesh Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response



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


@app.get("/nodes/{node_id}/insights")
async def get_insights(node_id: str):
    node = receiver.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    history = receiver.get_history(node_id, limit=1000)
    return calculate_insights(node_id, history, node=node)


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


class ChatRequest(BaseModel):
    message: str
    nodeId: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None


ChatRequest.model_rebuild()


@app.get("/api/ai/status")
async def ai_status():
    config = get_ai_config()
    return {
        "configured": config["configured"],
        "model": config["model"] if config["configured"] else None,
        "provider": os.getenv("AI_PROVIDER", "deepseek"),
    }


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    result = await ask_mesh_ai(
        message=request.message,
        history=request.history,
        selected_node_id=request.nodeId,
        receiver=receiver,
    )
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

