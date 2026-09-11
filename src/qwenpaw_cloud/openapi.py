"""Expose service/version requirements in the exported language-neutral API."""
from fastapi.openapi.utils import get_openapi


def install_openapi(app, *, models=(), request_models=None):
    def schema():
        if app.openapi_schema:
            return app.openapi_schema
        result = get_openapi(
            title=app.title, version="p0.v1", routes=app.routes
        )
        components = result.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        for model in models:
            value = model.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            schemas.update(value.pop("$defs", {}))
            schemas[model.__name__] = value
        components["securitySchemes"] = {
            "ServiceBearer": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "P0 test issuer signature; aud/role/sub/expiry "
                    "plus trusted scope claims"
                ),
            }
        }
        result["security"] = [{"ServiceBearer": []}]
        for path, methods in result["paths"].items():
            for method, operation in methods.items():
                if method not in {"get", "post", "put", "delete", "patch"}:
                    continue
                operation.setdefault("parameters", []).append(
                    {
                        "name": "X-Protocol-Version",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "enum": ["p0.v1"]},
                    }
                )
                for status, description in [
                    ("401", "Invalid service identity"),
                    ("403", "Scope or assignment forbidden"),
                    ("409", "Stale lease/revision or idempotency conflict"),
                    ("413", "Bounded body exceeded"),
                    ("422", "Invalid JSON contract"),
                    ("426", "Unsupported protocol version"),
                ]:
                    operation["responses"].setdefault(
                        status, {"description": description}
                    )
                model = (request_models or {}).get((method, path))
                if model:
                    operation["requestBody"] = {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/"
                                    + model.__name__
                                }
                            }
                        },
                    }
        app.openapi_schema = result
        return result

    app.openapi = schema
