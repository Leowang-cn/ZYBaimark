from __future__ import annotations

import re

import requests

from ..config import get_settings

VIDEO_TYPE = 2


class CtpError(Exception):
    pass


class CtpAuthError(CtpError):
    pass


def extract_cookie(text: str) -> str | None:
    """从粘贴的 Cookie / cURL 文本中提取 ZYBIPSCAS。"""
    m = re.search(r"ZYBIPSCAS=([^;\s'\"]+)", text or "")
    return f"ZYBIPSCAS={m.group(1)}" if m else None


def _post(path: str, body: dict, cookie: str) -> dict:
    url = get_settings().ctp_base_url + path
    try:
        r = requests.post(url, json=body, headers={"Cookie": cookie}, timeout=20)
        r.raise_for_status()
        j = r.json()
    except (requests.RequestException, ValueError) as e:
        raise CtpError(f"内容生产系统请求失败：{type(e).__name__}") from e
    if j.get("errNo") == 200002:
        raise CtpAuthError("登录已失效，请重新扫码登录")
    if j.get("errNo") != 0:
        raise CtpError(f"内容生产系统返回错误 {j.get('errNo')}：{j.get('errMsg')}")
    return j.get("data") or {}


def check_login(cookie: str) -> dict:
    return _post("/iot-content-backend/backend/user/info", {"publishTarget": 1}, cookie)


def resolve_material(material_id: str, cookie: str) -> dict:
    data = _post("/iot-content-backend/backend/resource/list", {
        "subject": 0, "resourceType": 0, "knowledgeId": "0", "keyword": material_id, "searchCategory": 0,
        "noTag": 0, "phase": 0, "treeSubject": 0, "pageNum": 1, "pageSize": 10, "auditStatus": 0,
        "publishTarget": 1}, cookie)
    hit = next((x for x in data.get("list") or [] if str(x.get("resourceId")) == material_id), None)
    if not hit:
        raise CtpError("未找到该素材")
    if hit.get("resourceType") != VIDEO_TYPE:
        raise CtpError(f"不是视频素材（{hit.get('resourceTypeName') or hit.get('resourceType')}）")
    if not hit.get("resourceUrl"):
        raise CtpError("素材没有视频地址")
    return {"name": hit.get("realName") or material_id, "url": hit["resourceUrl"],
            "encode_url": hit.get("encodeUrl") or "", "duration_s": (hit.get("resourceDuration") or 0) / 1000}
