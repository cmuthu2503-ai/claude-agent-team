"""WebSocket endpoints — real-time event streaming."""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.core.events import EventEmitter

router = APIRouter()


@router.websocket("/ws/activity")
async def activity_stream(websocket: WebSocket):
    """Global activity stream for Command Center."""
    await websocket.accept()
    emitter: EventEmitter = websocket.app.state.events
    queue = emitter.subscribe()

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        emitter.unsubscribe(queue)
    except Exception:
        emitter.unsubscribe(queue)


@router.websocket("/ws/requests/{request_id}")
async def request_stream(websocket: WebSocket, request_id: str):
    """Per-request event stream for Request Detail / Story Board."""
    await websocket.accept()
    emitter: EventEmitter = websocket.app.state.events
    queue = emitter.subscribe(request_id=request_id)

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        emitter.unsubscribe(queue, request_id=request_id)
    except Exception:
        emitter.unsubscribe(queue, request_id=request_id)
