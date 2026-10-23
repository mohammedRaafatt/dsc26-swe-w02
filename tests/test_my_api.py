from fastapi.testclient import TestClient

from app.api import create_app


def client():
    return TestClient(create_app())


def mk(c, ref, **kw):
    body = {"merchant_ref": ref, "cod_amount_piastres": 100, "address_text": "Maadi"}
    body.update(kw)
    return c.post("/v1/parcels", json=body)


def test_create_and_get():
    c = client()
    r = mk(c, "a")
    assert r.status_code == 201
    p = r.json()
    assert p["id"].startswith("pcl_") and r.headers["location"].endswith(f"/v1/parcels/{p['id']}")
    assert c.get(f"/v1/parcels/{p['id']}").json() == p


def test_validation_problem():
    c = client()
    r = mk(c, "", cod_amount_piastres=-1)
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert len(r.json()["errors"]) == 2
    assert mk(c, "x" * 64, cod_amount_piastres=5_000_000).status_code == 201


def test_duplicate_and_404():
    c = client()
    mk(c, "a")
    assert mk(c, "a").status_code == 409
    assert c.get("/v1/parcels/nope").status_code == 404
    assert len(c.get("/v1/parcels").json()["items"]) == 1


def test_events_and_conflict():
    c = client()
    pid = mk(c, "a").json()["id"]
    assert c.post(f"/v1/parcels/{pid}/events", json={"type": "deliver"}).status_code == 409
    assert c.post(f"/v1/parcels/{pid}/events", json={"type": "dispatch"}).json()["attempts"] == 1
    assert c.post(f"/v1/parcels/{pid}/events", json={"type": "bogus"}).status_code == 422


def test_cursor_survives_state_changes():
    c = client()
    ids = [mk(c, f"r{i}").json()["id"] for i in range(25)]
    page1 = c.get("/v1/parcels?state=created&limit=10").json()
    for item in page1["items"]:
        c.post(f"/v1/parcels/{item['id']}/events", json={"type": "dispatch"})
    page2 = c.get(f"/v1/parcels?state=created&limit=10&cursor={page1['next_cursor']}").json()
    assert [i["id"] for i in page2["items"]] == ids[10:20]
    page3 = c.get(f"/v1/parcels?state=created&limit=10&cursor={page2['next_cursor']}").json()
    assert page3["next_cursor"] is None and len(page3["items"]) == 5


def test_bad_cursor_and_limit():
    c = client()
    assert c.get("/v1/parcels?cursor=garbage").status_code == 400
    for bad in ("0", "101", "-1", "x"):
        assert c.get(f"/v1/parcels?limit={bad}").status_code == 422
    assert c.get("/v1/parcels?state=nope").status_code == 422


def test_openapi_errors():
    spec = create_app().openapi()
    assert spec["paths"]["/v1/parcels/{parcel_id}/events"]["post"]["operationId"] == "recordParcelEvent"
    assert {"200", "404", "409", "422"} <= set(spec["paths"]["/v1/parcels/{parcel_id}/events"]["post"]["responses"])
