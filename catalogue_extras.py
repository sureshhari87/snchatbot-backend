"""Server-owned recent history and audience-filtered Home content."""

from datetime import timezone
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator
from sqlalchemy import case, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (
    CatalogueCampaign,
    CatalogueCampaignRecipient,
    CatalogueContentAudit,
    CatalogueHomeLayout,
    Product,
    RecentlyViewedProduct,
    User,
    utc_now,
)
from schemas import ProductOut

HISTORY_LIMIT = 10
DEFAULT_LAYOUT = {
    key: True for key in ("banners", "latest", "trending", "categories", "voucher", "schemes")
}
PositiveId = Annotated[int, Field(gt=0, strict=True)]


class ViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: PositiveId


class Sections(BaseModel):
    model_config = ConfigDict(extra="forbid")
    banners: bool = Field(default=True, strict=True)
    latest: bool = Field(default=True, strict=True)
    trending: bool = Field(default=True, strict=True)
    categories: bool = Field(default=True, strict=True)
    voucher: bool = Field(default=True, strict=True)
    schemes: bool = Field(default=True, strict=True)


class LayoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=0, strict=True)
    sections: Sections
    reason: str = Field(min_length=5, max_length=500)


class CampaignInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default="", max_length=500)
    image: HttpUrl | None = None
    cta_text: str = Field(min_length=1, max_length=60)
    target: Literal[
        "All",
        "Gold",
        "Diamond",
        "Silver",
        "Platinum",
        "Men",
        "Women",
        "Kids",
        "/search",
        "/voucher",
        "/cart",
        "/schemes",
    ]
    active: bool = Field(default=False, strict=True)
    audience: Literal["global", "customer"] = "global"
    customer_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    sort_order: int = Field(default=0, ge=-10000, le=10000, strict=True)
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def validate_terms(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("End must be after start")
        if self.image is not None and (
            self.image.scheme != "https" or self.image.username or self.image.password
        ):
            raise ValueError("Campaign images require HTTPS")
        if (self.audience == "global" and self.customer_ids) or (
            self.audience == "customer" and not self.customer_ids
        ):
            raise ValueError("Audience and recipients do not match")
        if len(set(self.customer_ids)) != len(self.customer_ids):
            raise ValueError("Duplicate recipients")
        return self


class CampaignUpdate(CampaignInput):
    expected_version: int = Field(ge=1, strict=True)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


def campaign_document(row, recipients=None):
    data = {
        **row.content,
        "active": row.active,
        "startAt": iso(row.starts_at),
        "endAt": iso(row.ends_at),
        "sortOrder": row.sort_order,
    }
    result = {"id": str(row.id), "version": row.version, "data": data}
    if recipients is not None:
        result.update(audience=row.audience, customer_ids=recipients)
    return result


def layout_document(row):
    return {
        "version": row.version if row else 0,
        "sections": row.sections if row else DEFAULT_LAYOUT,
    }


def install(app, get_db, get_current_user, require_permission):
    permission = require_permission("catalog:manage")

    @app.get("/catalogue/recently-viewed")
    def history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        rows = (
            db.query(RecentlyViewedProduct, Product)
            .join(Product, Product.id == RecentlyViewedProduct.product_id)
            .filter(RecentlyViewedProduct.user_id == user.id)
            .order_by(
                RecentlyViewedProduct.viewed_at.desc(), RecentlyViewedProduct.product_id.desc()
            )
            .limit(HISTORY_LIMIT)
            .all()
        )
        return [
            {
                "product": ProductOut.model_validate(product).model_dump(mode="json"),
                "viewed_at": iso(view.viewed_at),
            }
            for view, product in rows
        ]

    @app.post("/catalogue/recently-viewed")
    def record(
        payload: ViewInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)
    ):
        # Serialize per-customer posts; a revisit updates one row, never a snapshot.
        db.query(User).filter_by(id=user.id).with_for_update().one()
        if db.get(Product, payload.product_id) is None:
            raise HTTPException(404, "Product not found")
        row = db.get(RecentlyViewedProduct, (user.id, payload.product_id))
        if row is None:
            row = RecentlyViewedProduct(user_id=user.id, product_id=payload.product_id)
            db.add(row)
        row.viewed_at = utc_now()
        db.flush()
        old = (
            db.query(RecentlyViewedProduct)
            .filter_by(user_id=user.id)
            .order_by(
                RecentlyViewedProduct.viewed_at.desc(), RecentlyViewedProduct.product_id.desc()
            )
            .offset(HISTORY_LIMIT)
            .all()
        )
        for stale in old:
            db.delete(stale)
        db.commit()
        return {"ok": True}

    def home(db, user_id=None):
        now = utc_now()
        eligible = CatalogueCampaign.audience == "global"
        if user_id is not None:
            targeted = db.query(CatalogueCampaignRecipient.campaign_id).filter_by(user_id=user_id)
            eligible = or_(eligible, CatalogueCampaign.id.in_(targeted))
        row = (
            db.query(CatalogueCampaign)
            .filter(
                CatalogueCampaign.active.is_(True),
                CatalogueCampaign.starts_at <= now,
                CatalogueCampaign.ends_at > now,
                eligible,
            )
            .order_by(
                case((CatalogueCampaign.audience == "customer", 0), else_=1),
                CatalogueCampaign.sort_order,
                CatalogueCampaign.id,
            )
            .first()
        )
        layout = layout_document(db.get(CatalogueHomeLayout, 1))
        return {
            **layout,
            "campaign": campaign_document(row) if row and layout["sections"]["banners"] else None,
        }

    @app.get("/catalogue/home")
    def public_home(db: Session = Depends(get_db)):
        return home(db)

    @app.get("/catalogue/home/my")
    def customer_home(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        return home(db, user.id)

    def recipients(db, campaign_id):
        return [
            value
            for (value,) in db.query(CatalogueCampaignRecipient.user_id)
            .filter_by(campaign_id=campaign_id)
            .order_by(CatalogueCampaignRecipient.user_id)
            .all()
        ]

    def audit(db, admin, resource, resource_id, version, reason, before, after):
        db.add(
            CatalogueContentAudit(
                actor_id=admin.id,
                resource=resource,
                resource_id=resource_id,
                version=version,
                reason=reason,
                before=before,
                after=after,
            )
        )

    @app.get("/admin/catalogue/home-layout")
    def admin_layout(admin=Depends(permission), db: Session = Depends(get_db)):
        return layout_document(db.get(CatalogueHomeLayout, 1))

    @app.put("/admin/catalogue/home-layout")
    def set_layout(payload: LayoutInput, admin=Depends(permission), db: Session = Depends(get_db)):
        row = db.get(CatalogueHomeLayout, 1)
        before = layout_document(row)
        if before["version"] != payload.expected_version:
            raise HTTPException(409, "Home layout changed; reload before editing")
        version = payload.expected_version + 1
        sections = payload.sections.model_dump()
        if row is None:
            db.add(CatalogueHomeLayout(id=1, version=version, sections=sections))
        else:
            changed = (
                db.query(CatalogueHomeLayout)
                .filter_by(id=1, version=payload.expected_version)
                .update({"version": version, "sections": sections}, synchronize_session=False)
            )
            if changed != 1:
                db.rollback()
                raise HTTPException(409, "Home layout changed; reload before editing")
        after = {"version": version, "sections": sections}
        audit(db, admin, "home_layout", 1, version, payload.reason, before, after)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Home layout changed; reload before editing") from None
        return after

    def campaign_values(payload):
        return dict(
            active=payload.active,
            audience=payload.audience,
            starts_at=payload.starts_at.astimezone(timezone.utc).replace(tzinfo=None),
            ends_at=payload.ends_at.astimezone(timezone.utc).replace(tzinfo=None),
            sort_order=payload.sort_order,
            content={
                "title": payload.title,
                "subtitle": payload.subtitle,
                "image": str(payload.image) if payload.image else "",
                "ctaText": payload.cta_text,
                "target": payload.target,
            },
        )

    def check_recipients(db, ids):
        if ids and db.query(User).filter(User.id.in_(ids)).count() != len(ids):
            raise HTTPException(422, "One or more recipients do not exist")

    @app.get("/admin/catalogue/campaigns")
    def campaigns(
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        admin=Depends(permission),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(CatalogueCampaign)
            .order_by(CatalogueCampaign.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [campaign_document(row, recipients(db, row.id)) for row in rows]

    @app.post("/admin/catalogue/campaigns", status_code=201)
    def create_campaign(
        payload: CampaignInput, admin=Depends(permission), db: Session = Depends(get_db)
    ):
        check_recipients(db, payload.customer_ids)
        row = CatalogueCampaign(version=1, **campaign_values(payload))
        db.add(row)
        db.flush()
        for user_id in payload.customer_ids:
            db.add(CatalogueCampaignRecipient(campaign_id=row.id, user_id=user_id))
        after = campaign_document(row, sorted(payload.customer_ids))
        audit(db, admin, "campaign", row.id, 1, payload.reason, None, after)
        db.commit()
        return after

    @app.put("/admin/catalogue/campaigns/{campaign_id}")
    def edit_campaign(
        campaign_id: int,
        payload: CampaignUpdate,
        admin=Depends(permission),
        db: Session = Depends(get_db),
    ):
        check_recipients(db, payload.customer_ids)
        row = db.get(CatalogueCampaign, campaign_id)
        if row is None:
            raise HTTPException(404, "Campaign not found")
        before = campaign_document(row, recipients(db, campaign_id))
        changed = (
            db.query(CatalogueCampaign)
            .filter_by(id=campaign_id, version=payload.expected_version)
            .update(
                {**campaign_values(payload), "version": payload.expected_version + 1},
                synchronize_session=False,
            )
        )
        if changed != 1:
            db.rollback()
            raise HTTPException(409, "Campaign changed; reload before editing")
        db.query(CatalogueCampaignRecipient).filter_by(campaign_id=campaign_id).delete(
            synchronize_session=False
        )
        for user_id in payload.customer_ids:
            db.add(CatalogueCampaignRecipient(campaign_id=campaign_id, user_id=user_id))
        db.expire(row)
        after = campaign_document(row, sorted(payload.customer_ids))
        audit(db, admin, "campaign", row.id, row.version, payload.reason, before, after)
        db.commit()
        return after

    @app.get("/admin/catalogue/content-audit")
    def audit_history(
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        admin=Depends(permission),
        db: Session = Depends(get_db),
    ):
        rows = (
            db.query(CatalogueContentAudit)
            .order_by(CatalogueContentAudit.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "actor_id": r.actor_id,
                "resource": r.resource,
                "resource_id": r.resource_id,
                "version": r.version,
                "reason": r.reason,
                "before": r.before,
                "after": r.after,
                "created_at": iso(r.created_at),
            }
            for r in rows
        ]
