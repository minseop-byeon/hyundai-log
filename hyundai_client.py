import os
import base64
from urllib.parse import urlencode

import httpx


class HyundaiClient:
    def __init__(self):
        self.client_id = os.getenv("HYUNDAI_CLIENT_ID")
        self.client_secret = os.getenv("HYUNDAI_CLIENT_SECRET")
        self.redirect_uri = os.getenv("HYUNDAI_REDIRECT_URI")
        self.auth_base = os.getenv("HYUNDAI_AUTH_BASE")
        self.data_base = os.getenv("HYUNDAI_DATA_BASE")
        self.state = os.getenv("APP_STATE")

    def get_login_url(self):
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": self.state,
        }
        return f"{self.auth_base}/api/v1/user/oauth2/authorize?{urlencode(params)}"

    def _basic_auth_header(self):
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("utf-8")

        return {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def exchange_code(self, code):
        url = f"{self.auth_base}/api/v1/user/oauth2/token"

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, data=payload, headers=self._basic_auth_header())
            content_type = r.headers.get("content-type", "")

            return {
                "status_code": r.status_code,
                "headers": dict(r.headers),
                "text": r.text,
                "json": r.json() if "application/json" in content_type else None,
            }

    async def refresh_access_token(self, refresh_token):
        url = f"{self.auth_base}/api/v1/user/oauth2/token"

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, data=payload, headers=self._basic_auth_header())
            content_type = r.headers.get("content-type", "")

            return {
                "status_code": r.status_code,
                "headers": dict(r.headers),
                "text": r.text,
                "json": r.json() if "application/json" in content_type else None,
            }

    async def get_vehicle_list(self, access_token):
        url = f"{self.data_base}/api/v1/car/profile/carlist"

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=headers)
            content_type = r.headers.get("content-type", "")

            return {
                "requested_url": url,
                "status_code": r.status_code,
                "headers": dict(r.headers),
                "text": r.text,
                "json": r.json() if "application/json" in content_type else None,
            }

    async def get_odometer(self, access_token, car_id):
        url = f"{self.data_base}/api/v1/car/status/{car_id}/odometer"

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=headers)
            content_type = r.headers.get("content-type", "")

            return {
                "requested_url": str(r.request.url),
                "status_code": r.status_code,
                "headers": dict(r.headers),
                "text": r.text,
                "json": r.json() if "application/json" in content_type else None,
            }