"""Notification endpoints."""

from fastapi import APIRouter, Depends, Request

from src.auth.service import get_current_user

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = 50,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    notifications = await state.get_notifications(
        user_id=user.get("sub"), unread_only=unread_only, limit=limit
    )
    return {
        "data": [
            {
                "notification_id": n.notification_id,
                "event_id": n.event_id,
                "severity": n.severity,
                "title": n.title,
                "message": n.message,
                "request_id": n.request_id,
                "created_at": n.created_at.isoformat(),
                "read_at": n.read_at.isoformat() if n.read_at else None,
            }
            for n in notifications
        ],
        "meta": None,
        "error": None,
    }


@router.put("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    await state.mark_notification_read(notification_id)
    return {"data": {"marked": True}, "meta": None, "error": None}


@router.put("/read-all")
async def mark_all_read(
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    await state.mark_all_notifications_read(user.get("sub", ""))
    return {"data": {"marked": True}, "meta": None, "error": None}
