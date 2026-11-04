import base64
import importlib
import io
import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from models import AiGeneratedConcept
from sona_ai.images import MAX_STORED_BYTES, compact_concept_image


def png(size=(32, 32), noise=False):
    image = Image.effect_noise(size, 80).convert("RGB") if noise else Image.new("RGB", size, "gold")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def payload(image):
    return {
        "concept_id": "synthetic-image-roundtrip",
        "name": "Synthetic floral ring",
        "source_prompt": "An original floral ring for testing",
        "design_brief": "A floral gold ring.",
        "image_base64": image,
        "image_mime_type": "image/png",
        "answer_source": "ai",
    }


def test_real_preview_is_bounded_jpeg_and_keeps_full_frame():
    encoded = compact_concept_image(png((1300, 900), noise=True))
    raw = base64.b64decode(encoded)
    assert len(raw) <= MAX_STORED_BYTES
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format == "JPEG"
        assert image.size == (1024, 709)
        assert not image.getexif()


@pytest.mark.parametrize(
    "encoded",
    [
        "not-base64",
        "",
        base64.b64encode(b"<svg></svg>").decode(),
        base64.b64encode(b"not an image").decode(),
    ],
)
def test_invalid_preview_is_rejected(encoded):
    with pytest.raises(ValueError):
        compact_concept_image(encoded)


def test_animated_image_is_rejected():
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(
        output,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (16, 16), "blue")],
        duration=100,
        loop=0,
    )
    with pytest.raises(ValueError):
        compact_concept_image(base64.b64encode(output.getvalue()).decode())


def test_excessive_pixel_count_is_rejected():
    with pytest.raises(ValueError):
        compact_concept_image(png((4000, 4000)))


def test_real_generated_image_saves_under_normal_body_limit(
    client, auth_headers, admin_headers, db
):
    compact = compact_concept_image(png((1024, 1024), noise=True))
    body = payload(compact)
    assert len(json.dumps(body).encode()) < 1024 * 1024
    response = client.post("/ai-concepts", headers=auth_headers, json=body)
    assert response.status_code == 200
    assert response.json()["image_mime_type"] == "image/jpeg"
    assert len(base64.b64decode(response.json()["image_base64"])) <= MAX_STORED_BYTES
    assert db.query(AiGeneratedConcept).filter_by(concept_id=body["concept_id"]).count() == 1
    assert client.post("/ai-concepts", headers=auth_headers, json=body).status_code == 200
    assert db.query(AiGeneratedConcept).filter_by(concept_id=body["concept_id"]).count() == 1
    other = client.get("/ai-concepts/my", headers=admin_headers)
    assert other.status_code == 200
    assert all(row["concept_id"] != body["concept_id"] for row in other.json())


def test_invalid_saved_image_makes_no_concept_record(client, auth_headers, db):
    before = db.query(AiGeneratedConcept).count()
    response = client.post("/ai-concepts", headers=auth_headers, json=payload("not-an-image"))
    assert response.status_code == 422
    assert db.query(AiGeneratedConcept).count() == before


def test_invalid_provider_image_keeps_usable_brief(auth_client, monkeypatch):
    ai_main = importlib.import_module("sona_ai.main")
    monkeypatch.setattr(
        ai_main.ai,
        "json_response",
        AsyncMock(
            return_value={
                "name": "Floral ring",
                "design_brief": "A gold floral ring.",
                "materials": ["Gold"],
                "craft_notes": ["Confirm size and final price."],
            }
        ),
    )
    monkeypatch.setattr(ai_main.ai, "image", AsyncMock(return_value="not-an-image"))
    result = auth_client.post(
        "/sona/ai/concepts",
        json={"description": "An original gold floral ring", "generate_image": True},
    )
    assert result.status_code == 200
    assert result.json()["name"] == "Floral ring"
    assert result.json()["image_base64"] is None
    assert result.json()["image_mime_type"] is None
