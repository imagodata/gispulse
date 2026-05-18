"""
Portal router — aggregator that composes all portal sub-routers.

Sub-routers:
- portal_upload_router   : POST /datasets/upload, POST /datasets/import-url
- portal_datasets_router : GET/DELETE/PATCH /datasets/*, GET /capabilities
- portal_features_router : PUT/POST/DELETE /datasets|features/*
- portal_sql_router      : POST /sql/execute, POST /sql/export
"""

from __future__ import annotations

from fastapi import APIRouter

from gispulse.adapters.http.routers.portal_datasets_router import router as _datasets_router
from gispulse.adapters.http.routers.portal_features_router import router as _features_router
from gispulse.adapters.http.routers.portal_sql_router import router as _sql_router
from gispulse.adapters.http.routers.portal_upload_router import router as _upload_router

router = APIRouter(prefix="/api/portal", tags=["portal"])

router.include_router(_upload_router)
router.include_router(_datasets_router)
router.include_router(_features_router)
router.include_router(_sql_router)
