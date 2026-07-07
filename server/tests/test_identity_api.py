from __future__ import annotations

import base64
import json
import unittest

import server as server_module


class IdentityApiTests(unittest.IsolatedAsyncioTestCase):
    STATIC_PAGES = [
        "/",
        "/travel-chat",
        "/travel-support",
        "/live-transcription",
        "/recap",
        "/transcription",
    ]

    async def asyncSetUp(self):
        self.app = server_module.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    async def test_me_returns_guest_without_auth_headers(self):
        response = await self.client.get("/api/me")
        body = await response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["authenticated"])
        self.assertEqual(body["display_name"], "Guest")
        self.assertEqual(body["email"], "")
        self.assertEqual(body["identity_provider"], "")

    async def test_me_reads_easy_auth_fallback_headers(self):
        response = await self.client.get(
            "/api/me",
            headers={
                "X-MS-CLIENT-PRINCIPAL-NAME": "Mina Patel",
                "X-MS-CLIENT-PRINCIPAL-ID": "user-123",
                "X-MS-CLIENT-PRINCIPAL-IDP": "aad",
            },
        )
        body = await response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["display_name"], "Mina Patel")
        self.assertEqual(body["identity_provider"], "aad")

    async def test_me_reads_encoded_client_principal(self):
        principal = {
            "identityProvider": "aad",
            "userId": "user-456",
            "userDetails": "Ada Lovelace",
            "claims": [
                {
                    "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "val": "ada@example.com",
                }
            ],
        }
        token = base64.b64encode(json.dumps(principal).encode("utf-8")).decode("utf-8")

        response = await self.client.get("/api/me", headers={"X-MS-CLIENT-PRINCIPAL": token})
        body = await response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["display_name"], "Ada Lovelace")
        self.assertEqual(body["email"], "ada@example.com")
        self.assertEqual(body["identity_provider"], "aad")

    async def test_static_pages_include_shared_identity_header(self):
        for path in self.STATIC_PAGES:
            with self.subTest(path=path):
                response = await self.client.get(path)
                html = (await response.get_data()).decode("utf-8")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(html.count('class="app-header"'), 1)
                self.assertEqual(html.count("data-user-identity"), 1)
                self.assertIn("identity.css", html)
                self.assertIn("identity.js", html)
                if path == "/travel-chat":
                    self.assertIn("travel-chat.css?v=8", html)
                if path == "/travel-support":
                    self.assertIn("travel-support.css?v=3", html)