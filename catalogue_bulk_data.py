"""Bounded CSV/ZIP validation. No database writes, uploads or extraction."""

import base64
import csv
import hashlib
import io
import json
import re
import stat
import warnings
import zipfile

from PIL import Image, ImageOps

from catalogue_data import number, product_values
from product_readiness import readiness

MAX_ZIP = 20 * 1024 * 1024
MAX_EXPANDED = 24 * 1024 * 1024
MAX_ROWS = 50
HEADERS = [
    "sku",
    "name",
    "category",
    "productType",
    "audience",
    "description",
    "metalType",
    "purity",
    "goldColor",
    "huid",
    "metalWeight",
    "stock",
    "useDynamicPricing",
    "price",
    "makingCharge",
    "stoneCharge",
    "diamondCharge",
    "shippingCharge",
    "wastagePercent",
    "gstPercent",
    "readyToShip",
    "madeToOrder",
    "handlingDays",
    "manufacturingDays",
    "sizeAdjustmentDays",
    "sizes",
    "sizeAdjustmentSizes",
    "sizeType",
    "dispatchNotBefore",
    "image1",
    "image2",
    "image3",
    "image4",
]
REQUIRED = {
    "sku",
    "name",
    "category",
    "stock",
    "useDynamicPricing",
    "readyToShip",
    "madeToOrder",
    "handlingDays",
    "manufacturingDays",
    "sizeAdjustmentDays",
    "image1",
}
CATEGORIES = {
    "Gold": ("gold", {"18K", "22K"}),
    "Diamond": ("gold", {"18K", "22K"}),
    "Silver": ("silver", {"800", "925"}),
    "Platinum": ("platinum", {"PT950"}),
}
AUDIENCES = {"Women", "Men", "Kids", "Unisex"}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def boolean(value, name):
    value = value.lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def integer(value, name, maximum=365):
    result = number(value, name)
    if result != result.to_integral_value() or result > maximum:
        raise ValueError(f"{name} must be a whole number from 0 to {maximum}")
    return int(result)


def https_image(value):
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or len(value) > 2048
    ):
        raise ValueError("Image URLs must be public HTTPS URLs without credentials or fragments")
    return value


def unpack(encoded):
    if not encoded:
        return {}
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_ZIP:
            raise ValueError("Photo ZIP exceeds 20 MB")
        result = {}
        total = 0
        pixel_total = 0
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ROWS * 4:
                raise ValueError("Photo ZIP must contain 1 to 200 flat image files")
            for entry in entries:
                name = entry.filename
                if (
                    not re.fullmatch(r"[A-Za-z0-9_-]{1,64}_[1-4]\.(?:jpg|jpeg|png|webp)", name)
                    or name in result
                    or entry.is_dir()
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.flag_bits & 1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or not 0 < entry.file_size <= 8 * 1024 * 1024
                    or entry.file_size > max(entry.compress_size, 1) * 100
                ):
                    raise ValueError("ZIP contains an unsafe, duplicate or unsupported photo")
                total += entry.file_size
                if total > MAX_EXPANDED:
                    raise ValueError("Expanded photos exceed 24 MB")
                content = archive.read(entry)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(content)) as image:
                        if (
                            image.format not in {"JPEG", "PNG", "WEBP"}
                            or image.width * image.height > 12_000_000
                            or getattr(image, "n_frames", 1) != 1
                        ):
                            raise ValueError(
                                "Photos must be static JPEG, PNG or WebP up to 12 megapixels"
                            )
                        expected = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
                        if image.format != expected[name.rsplit(".", 1)[1]]:
                            raise ValueError("Photo extension does not match its image content")
                        pixel_total += image.width * image.height
                        if pixel_total > 100_000_000:
                            raise ValueError(
                                "Combined photo pixels exceed 100 megapixels; split the batch"
                            )
                        image.verify()
                    with Image.open(io.BytesIO(content)) as image:
                        thumbnail = ImageOps.exif_transpose(image)
                        thumbnail.thumbnail((160, 160))
                        output = io.BytesIO()
                        with thumbnail.convert("RGB") as rgb:
                            rgb.save(output, format="JPEG", quality=70)
                        thumbnail.close()
                result[name] = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "thumbnail_base64": base64.b64encode(output.getvalue()).decode(),
                    "bytes": len(content),
                }
        return result
    except (
        ValueError,
        zipfile.BadZipFile,
        OSError,
        RuntimeError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError(
            "Invalid photo ZIP: use bounded, flat SKU_1.jpg through SKU_4 files"
        ) from None


def plan(csv_text, photos_base64, rates):
    if not csv_text or len(csv_text.encode("utf-8")) > 512 * 1024:
        raise ValueError("CSV must be UTF-8 and at most 512 KB")
    if len(photos_base64) > 28_000_000:
        raise ValueError("Photo ZIP exceeds 20 MB")
    photos = unpack(photos_base64)
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), strict=True)
    fields = reader.fieldnames or []
    if (
        len(fields) != len(set(fields))
        or set(fields) - set(HEADERS)
        or not REQUIRED.issubset(fields)
    ):
        raise ValueError("CSV headers are missing, duplicated or unknown; use the Sona template")
    products, seen, used = [], set(), set()
    try:
        for line, original in enumerate(reader, start=2):
            if len(products) >= MAX_ROWS:
                raise ValueError("A batch supports at most 50 products")
            if None in original or any(v is None for v in original.values()):
                raise ValueError(f"CSV row {line}: column count does not match the header")
            row = {k: v.strip() for k, v in original.items()}
            try:
                sku = row["sku"]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sku) or sku.casefold() in seen:
                    raise ValueError(
                        "SKU must be unique ASCII letters, numbers, hyphens or underscores"
                    )
                seen.add(sku.casefold())
                if (
                    not row["name"]
                    or len(row["name"]) > 160
                    or len(row.get("description", "")) > 5000
                ):
                    raise ValueError(
                        "Provide a product name up to 160 characters and description up to 5000"
                    )
                category = row["category"]
                if category not in {*CATEGORIES, "Gifts"}:
                    raise ValueError("Category must be Gold, Diamond, Silver, Platinum or Gifts")
                data = {
                    k: row[k]
                    for k in (
                        "sku",
                        "name",
                        "description",
                        "category",
                        "productType",
                        "audience",
                        "metalType",
                        "purity",
                        "goldColor",
                        "huid",
                    )
                    if row.get(k)
                }
                if data.get("audience") and data["audience"] not in AUDIENCES:
                    raise ValueError("Audience must be Women, Men, Kids or Unisex")
                data["useDynamicPricing"] = boolean(row["useDynamicPricing"], "useDynamicPricing")
                if category == "Gifts":
                    if data["useDynamicPricing"]:
                        raise ValueError("Gifts require fixed pricing")
                else:
                    metal, purities = CATEGORIES[category]
                    if data.get("metalType") != metal or data.get("purity") not in purities:
                        raise ValueError("Metal and purity do not match the selected category")
                    if not data.get("productType"):
                        raise ValueError("Jewellery productType is required")
                    if metal == "gold" and data.get("goldColor") not in {"Yellow", "Rose"}:
                        raise ValueError("Gold colour must be Yellow or Rose")
                if category not in {"Gold", "Diamond"} and (
                    data.get("goldColor") or data.get("huid")
                ):
                    raise ValueError("Gold colour and HUID apply only to gold-based products")
                for key in (
                    "metalWeight",
                    "price",
                    "makingCharge",
                    "stoneCharge",
                    "diamondCharge",
                    "shippingCharge",
                    "wastagePercent",
                    "gstPercent",
                ):
                    if row.get(key):
                        data[key] = float(number(row[key], key))
                if category != "Gifts" and data.get("metalWeight", 0) <= 0:
                    raise ValueError("Positive metalWeight is required for jewellery")
                if category != "Gifts" and data["useDynamicPricing"]:
                    for key in (
                        "metalWeight",
                        "makingCharge",
                        "stoneCharge",
                        "diamondCharge",
                        "shippingCharge",
                        "wastagePercent",
                        "gstPercent",
                    ):
                        if key not in data:
                            raise ValueError(f"{key} must be supplied explicitly")
                    if data["metalWeight"] <= 0 or data["gstPercent"] <= 0:
                        raise ValueError("Positive weight and GST are required")
                data["stock"] = integer(row["stock"], "stock", 1_000_000)
                for key in ("readyToShip", "madeToOrder"):
                    data[key] = boolean(row[key], key)
                for key in ("handlingDays", "manufacturingDays", "sizeAdjustmentDays"):
                    data[key] = integer(row[key], key)
                for key in ("sizes", "sizeAdjustmentSizes"):
                    values = row.get(key, "").split("|") if row.get(key) else []
                    if (
                        len(values) > 100
                        or any(not v or v != v.strip() or len(v) > 40 for v in values)
                        or len(set(values)) != len(values)
                    ):
                        raise ValueError(f"Invalid {key}; separate distinct values with |")
                    data[key] = values
                if row.get("dispatchNotBefore"):
                    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row["dispatchNotBefore"]):
                        raise ValueError("Earliest dispatch must use YYYY-MM-DD")
                    data["dispatchNotBefore"] = row["dispatchNotBefore"]
                if data["sizeAdjustmentDays"]:
                    for size in data["sizes"] or [""]:
                        readiness(data, size)
                else:
                    if data["sizeAdjustmentSizes"]:
                        raise ValueError("Adjustment sizes require positive adjustment days")
                    readiness(data)
                images = []
                for slot in range(1, 5):
                    value = row.get(f"image{slot}", "")
                    if not value:
                        continue
                    if value.startswith("https://"):
                        https_image(value)
                    else:
                        if (
                            not re.fullmatch(re.escape(sku) + r"_[1-4]\.(jpg|jpeg|png|webp)", value)
                            or value not in photos
                        ):
                            raise ValueError("Photo filename is missing or does not match SKU")
                        used.add(value)
                    if value in images:
                        raise ValueError("Duplicate product photo")
                    images.append(value)
                if not row["image1"] or not images:
                    raise ValueError("Main photo image1 is required")
                if row.get("sizeType"):
                    if len(row["sizeType"]) > 40:
                        raise ValueError("Invalid sizeType")
                    data["sizeType"] = row["sizeType"]
                data["active"] = False
                values = product_values(data, rates)
                products.append(
                    {
                        "row": line,
                        "sku": sku,
                        "data": data,
                        "images": images,
                        "price": f"{values['price']:.2f}",
                        "stock": values["stock_quantity"],
                    }
                )
            except ValueError as error:
                raise ValueError(f"CSV row {line}: {error}") from None
    except csv.Error:
        raise ValueError("Malformed CSV; export as UTF-8 comma-separated data") from None
    if not products or used != set(photos):
        raise ValueError(
            "CSV must contain products; every ZIP photo must be referenced exactly by its SKU"
        )
    return products, photos
