import pytest

from product_media import video_urls


def test_optional_video_normalization_and_deduplication():
    assert video_urls(None) == []
    assert video_urls("") == []
    assert video_urls(
        [" https://media.example.invalid/bangle.mp4 ", "https://media.example.invalid/bangle.mp4"]
    ) == ["https://media.example.invalid/bangle.mp4"]


@pytest.mark.parametrize(
    "raw",
    [
        ["http://media.example.invalid/a.mp4"],
        ["javascript:alert(1)"],
        ["https://example.invalid/video with spaces.mp4"],
        ["https://user:synthetic@example.invalid/a.mp4"],
        [12],
        {"url": "https://example.invalid/a.mp4"},
        [f"https://example.invalid/{i}.mp4" for i in range(6)],
    ],
)
def test_invalid_video_metadata_is_rejected(raw):
    with pytest.raises(ValueError):
        video_urls(raw)


def test_catalogue_video_roundtrip_and_clear(client, admin_headers):
    created = client.post(
        "/admin/catalogue/products",
        headers=admin_headers,
        json={
            "name": "Synthetic video gift",
            "category": "Gifts",
            "stock": 1,
            "price": 500,
            "videoUrls": ["https://media.example.invalid/gift.mp4"],
        },
    )
    assert created.status_code == 200
    product_id = created.json()["id"]
    expected = ["https://media.example.invalid/gift.mp4"]
    assert created.json()["data"]["videoUrls"] == expected
    public = client.get(f"/products/{product_id}")
    assert public.status_code == 200
    assert public.json()["attributes"]["videoUrls"] == expected
    edited = client.patch(
        f"/admin/catalogue/products/{product_id}",
        headers=admin_headers,
        json={"name": "Edited synthetic gift"},
    )
    assert edited.status_code == 200 and edited.json()["data"]["videoUrls"] == expected
    rejected = client.patch(
        f"/admin/catalogue/products/{product_id}",
        headers=admin_headers,
        json={"videoUrls": ["http://insecure.invalid/a.mp4"]},
    )
    assert rejected.status_code == 422
    cleared = client.patch(
        f"/admin/catalogue/products/{product_id}", headers=admin_headers, json={"videoUrls": []}
    )
    assert cleared.status_code == 200 and cleared.json()["data"]["videoUrls"] == []


def test_legacy_invalid_video_is_not_exposed_with_private_admin_fields():
    from models import Product

    product = Product(
        source_data={
            "videoUrls": ["https://user:synthetic@example.invalid/video.mp4"],
            "internal_note": "Synthetic private admin field",
        }
    )
    assert product.attributes["videoUrls"] == []
    assert "internal_note" not in product.attributes
