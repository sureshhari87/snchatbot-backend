"""Read-only projection of delivery configuration; unrelated settings stay private."""

import json

from fastapi import Depends
from sqlalchemy.orm import Session

from delivery_rules import project
from models import AppConfigEntry, User


def document(entry):
    if entry is None:
        return {
            "configured": False,
            "valid": True,
            "routes": [],
            "route_count": 0,
            "weekday_base": 0,
            "editable": False,
        }
    try:
        routes = json.loads(entry.value)
        if not isinstance(routes, list) or len(routes) > 1000:
            raise ValueError()
    except (ValueError, TypeError):
        return {
            "configured": True,
            "valid": False,
            "routes": [],
            "route_count": 0,
            "weekday_base": 0,
            "editable": False,
        }
    result = []
    for index, value in enumerate(routes):
        try:
            route = project(value)
            result.append({"index": index, "valid": True, "route": route})
        except (ValueError, TypeError, KeyError):
            result.append({"index": index, "valid": False, "route": None})
    return {
        "configured": bool(routes),
        "valid": all(v["valid"] for v in result),
        "routes": result,
        "route_count": len(routes),
        "weekday_base": 0,
        "editable": False,
    }


def install(app, get_db, require_permission):
    @app.get("/admin/delivery-configuration")
    def read(
        admin: User = Depends(require_permission("config:manage")), db: Session = Depends(get_db)
    ):
        entry = db.query(AppConfigEntry).filter_by(key="commerce.delivery_routes").first()
        return document(entry)
