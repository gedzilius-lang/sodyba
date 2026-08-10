"""FastAPI builds every route at import time, so a bad decorator fails here."""


def test_api_module_imports_and_registers_the_delete_route():
    from backend.app.api import router
    paths = {r.path for r in router.routes}
    assert "/api/candidates/{cid}" in paths
    assert "/api/candidates" in paths
