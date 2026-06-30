from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts import update_rankings


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request_url = ""
        self.request_headers: dict[str, str] = {}

    def get(self, url: str, *, headers: dict[str, str], timeout: int) -> FakeResponse:
        self.request_url = url
        self.request_headers = headers
        return FakeResponse(self.payload)


class CoupangPartnersTests(unittest.TestCase):
    def test_authorization_signature_is_deterministic(self) -> None:
        authorization = update_rankings.coupang_authorization(
            "GET",
            "/products/search",
            "keyword=laptop&limit=10",
            "test-access",
            "test-secret",
            datetime(2026, 6, 30, 0, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            authorization,
            "CEA algorithm=HmacSHA256, access-key=test-access, "
            "signed-date=260630T000000Z, "
            "signature=6ad87e51b45d5d9b45da3061538bf9dc560eb53b0ecb01c777b53d79a6845a38",
        )

    def test_search_caps_limit_and_maps_ranked_product(self) -> None:
        fake_session = FakeSession(
            {
                "rCode": "0",
                "data": {
                    "productData": [
                        {
                            "rank": 1,
                            "productName": "Acer Swift 16GB 512GB 노트북",
                            "productPrice": 899000,
                            "productUrl": "https://link.coupang.com/example",
                            "isRocket": True,
                            "isFreeShipping": True,
                        }
                    ]
                },
            }
        )

        with (
            patch.dict(
                os.environ,
                {
                    "COUPANG_ACCESS_KEY": "test-access",
                    "COUPANG_SECRET_KEY": "test-secret",
                },
            ),
            patch.object(update_rankings, "session", return_value=fake_session),
        ):
            rows = update_rankings.collect_coupang_partners("2026-06-30", "노트북", 100)

        params = parse_qs(urlparse(fake_session.request_url).query)
        self.assertEqual(params["keyword"], ["노트북"])
        self.assertEqual(params["limit"], ["10"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rank, 1)
        self.assertEqual(rows[0].brand, "Acer")
        self.assertEqual(rows[0].sale_price, "899000")
        self.assertEqual(rows[0].benefits, "로켓배송, 무료배송")
        self.assertNotIn("test-secret", fake_session.request_headers["Authorization"])

    def test_search_requires_both_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {"COUPANG_ACCESS_KEY": "", "COUPANG_SECRET_KEY": ""},
        ):
            with self.assertRaises(update_rankings.CollectorError):
                update_rankings.collect_coupang_partners("2026-06-30", "노트북", 10)


if __name__ == "__main__":
    unittest.main()
