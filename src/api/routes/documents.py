"""Document endpoints — search, list, retrieve persisted agent outputs."""

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.service import get_current_user

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.get("")
async def list_documents(
    request: Request,
    doc_type: str | None = None,
    request_id: str | None = None,
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    if request_id:
        docs = await state.get_documents_for_request(request_id)
    elif doc_type:
        docs = await state.search_documents("", doc_type=doc_type, limit=limit)
    else:
        docs = await state.search_documents("", limit=limit)

    return {
        "data": [
            {
                "document_id": d.document_id,
                "request_id": d.request_id,
                "doc_type": d.doc_type,
                "title": d.title,
                "agent_id": d.agent_id,
                "version": d.version,
                "tags": d.tags,
                "content_length": len(d.content),
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "meta": None,
        "error": None,
    }


@router.get("/search")
async def search_documents(
    request: Request,
    q: str = "",
    doc_type: str | None = None,
    limit: int = 10,
    user: dict = Depends(get_current_user),
):
    if not q:
        return {"data": [], "meta": None, "error": None}
    state = request.app.state.state_store
    docs = await state.search_documents(q, doc_type=doc_type, limit=limit)
    return {
        "data": [
            {
                "document_id": d.document_id,
                "request_id": d.request_id,
                "doc_type": d.doc_type,
                "title": d.title,
                "agent_id": d.agent_id,
                "tags": d.tags,
                "content_preview": d.content[:200],
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        "meta": {"query": q},
        "error": None,
    }


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    doc = await state.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "data": {
            "document_id": doc.document_id,
            "request_id": doc.request_id,
            "doc_type": doc.doc_type,
            "title": doc.title,
            "content": doc.content,
            "agent_id": doc.agent_id,
            "version": doc.version,
            "tags": doc.tags,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        },
        "meta": None,
        "error": None,
    }
