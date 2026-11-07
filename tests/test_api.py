import io

import pytest
from openpyxl import load_workbook


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
    monkeypatch.setenv("APP_PASSWORD", "testpass123")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.sqlite")
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AI_API_KEY", "x")
    from backend import config, storage
    config.settings = None
    storage._storage = None
    from fastapi.testclient import TestClient
    from backend import main
    from backend.pipeline import worker as wk
    monkeypatch.setattr(wk.worker, "start", lambda: None)
    monkeypatch.setattr(wk.worker, "notify", lambda: None)
    with TestClient(main.app) as c:
        yield c


def login(c):
    assert c.post("/api/auth/login", json={"password": "testpass123"}).status_code == 200


def fake_ctp(monkeypatch):
    from backend.ctp import client as ctp

    monkeypatch.setattr(ctp, "check_login", lambda cookie: {})

    def resolve(mid, cookie):
        if mid == "111111":
            raise ctp.CtpError("不是视频素材（AI课）")
        return {"name": f"视频{mid}.mp4", "url": f"https://cdn.example.com/{mid}.mp4", "encode_url": "",
                "duration_s": 120.0}

    monkeypatch.setattr(ctp, "resolve_material", resolve)


def make_generation(video_id, rows):
    from backend import db as dbm
    from backend.db import Video
    from backend.pipeline.worker import save_generation

    with dbm.SessionLocal() as s:
        v = s.get(Video, video_id)
        g = save_generation(s, v, "规则" * 20, {"raw": {}, "excluded": [], "target": 2}, rows)
        v.status = "done"
        s.commit()
        return g.id


ROWS = [{"time": 10.5, "question": "3加4等于多少？", "answer": "7", "review_reason": "", "evidence": "答案是7"},
        {"time": 30.25, "question": "选哪个？", "answer": "C", "review_reason": "", "evidence": ""}]


def test_auth_required(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/tasks").status_code == 401
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


def test_bearer_token(client, monkeypatch):
    from backend import config
    object.__setattr__(config.settings, "access_token", "t" * 32)
    assert client.get("/api/tasks", headers={"Authorization": "Bearer " + "t" * 32}).status_code == 200
    assert client.get("/api/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    object.__setattr__(config.settings, "access_token", "")
    assert client.get("/api/tasks", headers={"Authorization": "Bearer "}).status_code == 401


def test_cross_origin_rejected(client):
    login(client)
    r = client.post("/api/tasks", json={}, headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_task_flow_versions_and_export(client, monkeypatch):
    login(client)
    fake_ctp(monkeypatch)
    r = client.post("/api/tasks", json={"material_ids": ["222222\n111111, 222222"], "prompt_text": "规则" * 20,
                                         "cookie_text": "curl -H 'Cookie: a=1; ZYBIPSCAS=abc123; b=2'"})
    assert r.status_code == 200, r.text
    vids = r.json()["videos"]
    assert [v["status"] for v in vids] == ["queued", "resolve_failed"]
    vid = vids[0]["id"]

    g1 = make_generation(vid, ROWS)
    v = client.get(f"/api/videos/{vid}").json()
    assert v["current_generation_id"] == g1

    gen = client.get(f"/api/generations/{g1}").json()
    m0 = gen["marks"][0]
    assert m0["time"] == "00:00:10.500"
    r = client.patch(f"/api/marks/{m0['id']}", json={"revision": m0["revision"], "time_s": 11.2, "confirmed": True})
    assert r.status_code == 200 and r.json()["revision"] == 2
    # 旧 revision 冲突
    r = client.patch(f"/api/marks/{m0['id']}", json={"revision": 1, "answer": "8"})
    assert r.status_code == 409
    added = client.post(f"/api/generations/{g1}/marks", json={"time_s": 50, "question": "新题", "answer": "B"}).json()
    assert added["source"] == "manual"
    client.patch(f"/api/marks/{gen['marks'][1]['id']}", json={"revision": 1, "deleted": True})
    assert client.post(f"/api/generations/{g1}/marks", json={"time_s": 999}).status_code == 400

    # 当前版本已有人工修改：新版本不自动切换
    g2 = make_generation(vid, ROWS[:1])
    assert client.get(f"/api/videos/{vid}").json()["current_generation_id"] == g1
    assert client.post(f"/api/videos/{vid}/current-generation", json={"generation_id": g2}).status_code == 200
    assert client.get(f"/api/videos/{vid}").json()["current_generation_id"] == g2
    client.post(f"/api/videos/{vid}/current-generation", json={"generation_id": g1})

    r = client.get(f"/api/export?task_id={v['task_id']}&format=xlsx")
    assert r.status_code == 200
    rows = list(load_workbook(io.BytesIO(r.content))["问题抽取"].iter_rows(values_only=True))
    assert rows[0] == ("音频名称", "问题时间点", "问题", "答案")
    assert rows[1:] == [("222222 视频222222.mp4", "00:00:11.200", "3加4等于多少？", "7"),
                        ("222222 视频222222.mp4", "00:00:50.000", "新题", "B")]
    csv = client.get(f"/api/export?video_id={vid}&format=csv").content
    assert csv.startswith(b"\xef\xbb\xbf") and len(csv.decode("utf-8-sig").strip().splitlines()) == 3


def test_export_rejects_empty_answer(client, monkeypatch):
    login(client)
    fake_ctp(monkeypatch)
    vid = client.post("/api/tasks", json={"material_ids": ["222222"], "prompt_text": "规则" * 20,
                                           "cookie_text": "ZYBIPSCAS=x"}).json()["videos"][0]["id"]
    g = make_generation(vid, ROWS)
    client.post(f"/api/generations/{g}/marks", json={"time_s": 5})
    r = client.get(f"/api/export?generation_id={g}")
    assert r.status_code == 400 and "答案为空" in r.json()["detail"]


def test_regenerate_queues_with_prompt(client, monkeypatch):
    login(client)
    fake_ctp(monkeypatch)
    vid = client.post("/api/tasks", json={"material_ids": ["222222"], "prompt_text": "规则" * 20,
                                           "cookie_text": "ZYBIPSCAS=x"}).json()["videos"][0]["id"]
    assert client.post(f"/api/videos/{vid}/generations", json={"prompt_text": "新规则" * 10}).status_code == 409
    make_generation(vid, ROWS)
    assert client.post(f"/api/videos/{vid}/generations", json={"prompt_text": "新规则" * 10}).status_code == 200
    v = client.get(f"/api/videos/{vid}").json()
    assert v["status"] == "queued" and v["pending_prompt"] == "新规则" * 10


def test_default_prompt_versions(client):
    login(client)
    p = client.get("/api/prompts/default").json()
    assert p["version"] == 1 and "互动" in p["content"]
    assert client.put("/api/prompts/default", json={"content": p["content"] + "\n补充"}).json()["version"] == 2
    assert len(client.get("/api/prompts/default/versions").json()) == 2


def test_finalize_and_answers():
    from backend.pipeline.extract import finalize, normalize_answer, parse_ts

    assert [normalize_answer(x) for x in ["C选项", "选C", "C（1000）", "千克"]] == ["C", "C", "C", "千克"]
    assert parse_ts("00:01:02.5") == 62.5
    tr = {"segments": [{"id": "s1", "start": 10, "end": 14, "text": "几袋大米是一吨呢？你来选择一下。"}],
          "words": [{"start": 10 + i * .25, "end": 10.2 + i * .25, "word": ch} for i, ch in enumerate("几袋大米是一吨呢你来选择一下")]}
    rows = finalize([{"question": "几袋大米是一吨呢？你来选择一下。", "answer": "C选项", "anchor_segment": "s1",
                      "anchor_text": "选择一下"}], tr, [[13.6, 15.5]], 400)
    assert rows[0]["time"] == 13.6 and rows[0]["answer"] == "C" and rows[0]["review_reason"] == ""


def test_config_validation(monkeypatch):
    from backend import config

    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(config.ConfigError):
        config.load_settings()
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("APP_PASSWORD", "testpass123")
    monkeypatch.setenv("AI_API_KEY", "x")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    with pytest.raises(config.ConfigError, match="cos"):
        config.load_settings()


def test_cookie_extract():
    from backend.ctp.client import extract_cookie

    assert extract_cookie("a=1; ZYBIPSCAS=abc; b=2") == "ZYBIPSCAS=abc"
    assert extract_cookie("nothing") is None
