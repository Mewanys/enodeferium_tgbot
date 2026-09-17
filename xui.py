import asyncio
from urllib.parse import quote

import aiohttp


class XUIError(Exception):
    pass


class XUI:
    def __init__(self, url, username, password, token=""):
        self.base = url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token.strip()
        self.session = None
        self.lock = asyncio.Lock()

    async def _session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        return self.session

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def login(self):
        s = await self._session()
        if self.token:
            async with s.get(
                self.base + "/panel/api/inbounds/list",
                headers=self._headers(),
                timeout=20,
            ) as r:
                text = await r.text()
                if r.status >= 400:
                    raise XUIError(f"3x-ui API token rejected: HTTP {r.status}, response: {text[:500]}")
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    raise XUIError(f"3x-ui returned invalid JSON: {text[:500]}")
                if not data.get("success", False):
                    raise XUIError(f"3x-ui API error: {data.get('msg', data)}")
                return data

        async with s.post(
            self.base + "/login",
            json={"username": self.username, "password": self.password},
            headers={"Content-Type": "application/json"},
            timeout=20,
        ) as r:
            text = await r.text()
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = None
            if r.status >= 400:
                raise XUIError(f"3x-ui login failed: HTTP {r.status}, response: {text[:500]}")
            if not data or not data.get("success", False):
                raise XUIError(f"3x-ui login failed: {data or text[:500]}")
            return data

    async def request(self, method, path, **kwargs):
        async with self.lock:
            s = await self._session()
            headers = kwargs.pop("headers", {})
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            headers.setdefault("Accept", "application/json")
            url = self.base + path
            print(f"[3X-UI] {method} {url}")
            async with s.request(method, url, headers=headers, timeout=30, **kwargs) as r:
                text = await r.text()
                print(f"[3X-UI] HTTP {r.status}")
                print(f"[3X-UI] RESPONSE: {text[:2000]}")
                if r.status >= 400:
                    raise XUIError(f"3x-ui API HTTP {r.status}: {text[:1000]}")
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    raise XUIError(f"3x-ui API returned non-JSON: {text[:1000]}")
                if isinstance(data, dict) and data.get("success") is False:
                    raise XUIError(f"3x-ui API error: {data.get('msg', 'unknown error')}")
                return data

    async def get_client_by_email(self, email):
        return await self.request("GET", "/panel/api/clients/get/" + quote(email, safe=""))

    async def get_clients_by_tgid(self, tg_id):
        """Compatibility helper. The fork exposes paged client list, not /get/tgId."""
        data = await self.request("GET", "/panel/api/clients/list/paged?page=1&pageSize=1000")
        obj = data.get("obj") or {}
        clients = obj.get("items") or obj.get("data") or []
        result = []
        for item in clients:
            client = item.get("client", item)
            if str(client.get("tgId", "")) == str(tg_id):
                result.append(item)
        return result

    async def create_client(self, email, tg_id, sub_id, expiry_ms, inbound_ids, flow="xtls-rprx-vision", limit_ip=2, comment="Enferium Telegram Bot"):
        payload = {
            "inboundIds": inbound_ids,
            "client": {
                "email": email,
                "subId": sub_id,
                "flow": flow,
                "limitIp": limit_ip,
                "totalGB": 0,
                "expiryTime": expiry_ms,
                "tgId": tg_id,
                "enable": True,
                "comment": comment,
            },
        }
        return await self.request(
            "POST", "/panel/api/clients/add", json=payload,
            headers={"Content-Type": "application/json"}
        )

    async def delete_client(self, email, keep_traffic=False):
        path = "/panel/api/clients/del/" + quote(email, safe="")
        if keep_traffic:
            path += "?keepTraffic=1"
        return await self.request("POST", path)

    async def update_client(self, email, payload):
        return await self.request(
            "POST", "/panel/api/clients/update/" + quote(email, safe=""),
            json=payload, headers={"Content-Type": "application/json"}
        )


    async def set_client_enabled(self, email, enabled):
        data = await self.get_client_by_email(email)
        obj = data.get("obj") or {}
        client = obj.get("client", obj)
        payload = {
            "email": client.get("email", email),
            "subId": client.get("subId") or "",
            "id": client.get("id") or client.get("uuid"),
            "password": client.get("password"),
            "auth": client.get("auth"),
            "flow": client.get("flow") or "",
            "security": client.get("security") or "auto",
            "totalGB": client.get("totalGB") or 0,
            "expiryTime": int(client.get("expiryTime") or 0),
            "limitIp": client.get("limitIp") or 0,
            "tgId": int(client.get("tgId") or 0),
            "reset": int(client.get("reset") or 0),
            "comment": client.get("comment") or "Enferium Telegram Bot",
            "enable": bool(enabled),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await self.update_client(email, payload)

    async def links(self, email):
        data = await self.request("GET", "/panel/api/clients/links/" + quote(email, safe=""))
        return data.get("obj") or []

    async def sub_links(self, sub_id):
        data = await self.request("GET", "/panel/api/clients/subLinks/" + quote(sub_id, safe=""))
        return data.get("obj") or []

    async def get_client_state(self, email):
        """Return (exists, enabled, expiry_ms) for a client in 3x-ui."""
        try:
            data = await self.get_client_by_email(email)
        except XUIError as e:
            if "HTTP 404" in str(e):
                return False, False, 0
            raise

        obj = data.get("obj") if isinstance(data, dict) else None
        if not obj:
            return False, False, 0

        client = obj.get("client", obj) if isinstance(obj, dict) else {}
        enabled = bool(client.get("enable", True))
        try:
            expiry_ms = int(client.get("expiryTime") or 0)
        except (TypeError, ValueError):
            expiry_ms = 0
        return True, enabled, expiry_ms

    async def client_exists(self, email):
        exists, enabled, expiry_ms = await self.get_client_state(email)
        return exists

    async def close(self):
        if self.session:
            await self.session.close()
