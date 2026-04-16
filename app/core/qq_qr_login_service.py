from __future__ import annotations

from typing import Any, Dict

import httpx


CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _headers() -> Dict[str, str]:
    return {
        "host": "q.qq.com",
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": CHROME_UA,
    }


class QQQRCodeLoginService:
    @staticmethod
    def request_login_code() -> Dict[str, str]:
        """
        Returns:
          {
            "code": str,
            "qrUrl": str
          }
        """

        url = "https://q.qq.com/ide/devtoolAuth/GetLoginCode"
        with httpx.Client(timeout=30.0, headers=_headers()) as client:
            r = client.get(url)
            r.raise_for_status()
            payload = r.json()

        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected payload type (expected object).")

        api_code = payload.get("code", None)
        code = payload.get("data", {}).get("code", "") if isinstance(payload.get("data"), dict) else ""
        code = str(code or "")

        if api_code is None or int(api_code) != 0:
            raise RuntimeError("GetLoginCode failed (unexpected response code).")
        if not code:
            raise RuntimeError("GetLoginCode failed (missing data.code).")

        qr_url = f"https://h5.qzone.qq.com/qqq/code/{code}?_proxy=1&from=ide"
        return {"code": code, "qrUrl": qr_url}

    @staticmethod
    def query_status(code: str) -> Dict[str, Any]:
        """
        Returns:
          - state=wait|used|ok|error
          - if ok: uin
          - if error: msg
        """

        url = "https://q.qq.com/ide/devtoolAuth/syncScanSateGetTicket"
        params = {"code": code}

        try:
            with httpx.Client(timeout=30.0, headers=_headers()) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    return {"state": "error", "msg": "status query network error"}
                payload = r.json()
        except Exception:
            return {"state": "error", "msg": "status query failed"}

        if not isinstance(payload, dict):
            return {"state": "error", "msg": "unexpected status payload"}

        res_code = int(payload.get("code", 0))
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}

        if res_code == 0:
            if int(data.get("ok", 0)) != 1:
                return {"state": "wait"}
            return {"state": "ok", "uin": str(data.get("uin", "") or "")}

        if res_code == -10003:
            return {"state": "used"}

        return {"state": "error", "msg": f"code={res_code}"}

