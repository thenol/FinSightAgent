from fastapi.testclient import TestClient

from app.api.errors import OPENAPI_ERROR_EXAMPLES
from app.main import create_app


def test_openapi_documents_dd00_error_envelope_and_examples() -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()

    components = schema["components"]
    assert "ErrorEnvelope" in components["schemas"]
    assert components["schemas"]["ErrorEnvelope"]["required"] == ["error", "meta"]
    assert "ApiErrorBody" in components["schemas"]
    assert components["schemas"]["ApiErrorBody"]["properties"]["code"]["type"] == "string"

    responses = components["responses"]
    for status in OPENAPI_ERROR_EXAMPLES:
        key = f"Error{status}"
        assert key in responses
        content = responses[key]["content"]["application/json"]
        assert content["schema"] == {"$ref": "#/components/schemas/ErrorEnvelope"}
        example = content["examples"][f"error_{status}"]["value"]
        assert example["error"]["code"]
        assert "request_id" in example["meta"]


def test_openapi_write_routes_reference_shared_error_responses() -> None:
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()

    def assert_error_ref(responses: dict, status: int) -> None:
        entry = responses[str(status)]
        assert entry["$ref"] == f"#/components/responses/Error{status}"

    login = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert_error_ref(login, 401)
    assert_error_ref(login, 422)

    create_source = schema["paths"]["/api/v1/sources"]["post"]["responses"]
    for status in (400, 401, 409, 422):
        assert_error_ref(create_source, status)

    ingest = schema["paths"]["/api/v1/documents/ingest"]["post"]["responses"]
    for status in (400, 401, 409, 422):
        assert_error_ref(ingest, status)

    review = schema["paths"]["/api/v1/reviews/{task_id}/decision"]["post"]["responses"]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(review, status)

    transition = schema["paths"]["/api/v1/reports/{report_id}/transition"]["post"]["responses"]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(transition, status)

    resume = schema["paths"]["/api/v1/workflows/{workflow_id}/resume"]["post"]["responses"]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(resume, status)

    create_llm = schema["paths"]["/api/v1/llm/providers"]["post"]["responses"]
    for status in (400, 401, 409, 422):
        assert_error_ref(create_llm, status)

    patch_llm = schema["paths"]["/api/v1/llm/providers/{provider_id}"]["patch"]["responses"]
    for status in (400, 401, 404, 422):
        assert_error_ref(patch_llm, status)

    rotate_llm = schema["paths"]["/api/v1/llm/providers/{provider_id}/rotate-key"]["post"][
        "responses"
    ]
    for status in (400, 401, 404, 422):
        assert_error_ref(rotate_llm, status)

    delete_llm = schema["paths"]["/api/v1/llm/providers/{provider_id}"]["delete"]["responses"]
    for status in (401, 404):
        assert_error_ref(delete_llm, status)

    test_llm = schema["paths"]["/api/v1/llm/providers/{provider_id}/test"]["post"]["responses"]
    for status in (400, 401, 404):
        assert_error_ref(test_llm, status)

    put_binding = schema["paths"]["/api/v1/llm/bindings"]["put"]["responses"]
    for status in (400, 401, 404, 422):
        assert_error_ref(put_binding, status)

    put_bindings_bulk = schema["paths"]["/api/v1/llm/bindings/bulk"]["put"]["responses"]
    for status in (400, 401, 404, 422):
        assert_error_ref(put_bindings_bulk, status)

    delete_document = schema["paths"]["/api/v1/documents/{document_id}"]["delete"]["responses"]
    for status in (401, 404, 409):
        assert_error_ref(delete_document, status)

    patch_document = schema["paths"]["/api/v1/documents/{document_id}"]["patch"]["responses"]
    for status in (400, 401, 404, 422):
        assert_error_ref(patch_document, status)

    sync_source = schema["paths"]["/api/v1/sources/{source_id}/sync"]["post"]["responses"]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(sync_source, status)

    sync_all = schema["paths"]["/api/v1/sources/sync-all"]["post"]["responses"]
    for status in (400, 401, 409, 422):
        assert_error_ref(sync_all, status)

    patch_source = schema["paths"]["/api/v1/sources/{source_id}"]["patch"]["responses"]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(patch_source, status)

    seed_sources = schema["paths"]["/api/v1/sources/seed"]["post"]["responses"]
    for status in (400, 401, 409, 422):
        assert_error_ref(seed_sources, status)

    create_workflow = schema["paths"]["/api/v1/events/{event_id}/workflows"]["post"][
        "responses"
    ]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(create_workflow, status)

    run_workflow = schema["paths"]["/api/v1/workflows/{workflow_id}/run"]["post"][
        "responses"
    ]
    for status in (400, 401, 404, 409, 422):
        assert_error_ref(run_workflow, status)

    purge_document = schema["paths"]["/api/v1/documents/{document_id}/purge"]["post"][
        "responses"
    ]
    for status in (401, 404, 409):
        assert_error_ref(purge_document, status)


def test_runtime_error_envelope_matches_documented_shape() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/auth/login", json={})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error", "meta"}
    assert body["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["error"]["retryable"] is False
    assert "request_id" in body["meta"]
