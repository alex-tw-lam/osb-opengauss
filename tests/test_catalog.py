import base64


def test_catalog_exposes_three_plans_with_schemas(env):
    service = env.broker.catalog()
    assert service.id and service.bindable is True
    assert [p.name for p in service.plans] == ["dev", "standard", "pro"]

    plan = service.plans[0]
    props = plan.schemas.service_instance["create"]["parameters"]["properties"]
    assert props["compatibility"]["enum"] == ["PG", "A", "B", "C"]
    assert props["encoding"]["enum"] == ["UTF8", "GBK", "GB18030", "Latin1"]
    assert props["storage_gb"]["maximum"] == 5
    assert props["max_connections"]["default"] == 20
    # No tablespaces curated by the operator -> parameter not offered at all.
    assert "tablespace" not in props
    bind_props = plan.schemas.service_binding["create"]["parameters"]["properties"]
    assert bind_props["access_role"]["enum"] == ["owner", "readwrite", "readonly"]


def test_catalog_tablespace_enum_when_configured(tmp_path):
    from conftest import make_env

    env = make_env(tmp_path, tablespaces=("ts_ssd", "ts_hdd"))
    props = env.broker.catalog().plans[0].schemas.service_instance["create"]["parameters"]["properties"]
    assert props["tablespace"]["enum"] == ["ts_ssd", "ts_hdd"]


def test_http_catalog_requires_auth_and_version_header(make_app):
    client = make_app.test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"broker:broker-dev-password").decode()}

    no_auth = client.get("/v2/catalog", headers={"X-Broker-API-Version": "2.16"})
    assert no_auth.status_code == 401

    ok = client.get("/v2/catalog", headers={**auth, "X-Broker-API-Version": "2.16"})
    assert ok.status_code == 200
    body = ok.get_json()
    services = body["services"]
    assert len(services) == 1
    assert services[0]["name"] == "gaussdb"
    assert len(services[0]["plans"]) == 3


def test_http_provision_unknown_plan_is_400(make_app):
    client = make_app.test_client()
    headers = {
        "Authorization": "Basic " + base64.b64encode(b"broker:broker-dev-password").decode(),
        "X-Broker-API-Version": "2.16",
    }
    resp = client.put(
        "/v2/service_instances/99999999-9999-9999-9999-999999999999?accepts_incomplete=false",
        headers=headers,
        json={"service_id": "4c6f6a1e-0f5a-4a5b-9d7e-2f8b3a1c5e01", "plan_id": "nope"},
    )
    assert resp.status_code == 400
