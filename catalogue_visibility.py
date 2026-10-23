"""Public discovery excludes inactive catalogue drafts; admin access is unchanged."""

from sqlalchemy import func

from models import Product


def public_product_condition():
    return func.coalesce(Product.source_data["active"].as_boolean(), True).is_(True)


def is_public_product(product):
    return product is not None and (product.source_data or {}).get("active") is not False
