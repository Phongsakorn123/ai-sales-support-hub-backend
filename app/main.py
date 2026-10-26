import re
import secrets
from contextvars import ContextVar
from html import escape

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.ai_agent.api.product_suggestions import router as ai_router
from app.backend.api.auth import router as auth_router
from app.backend.api.documents import product_documents_router, router as documents_router
from app.backend.api.products import router as products_router
from app.core.config import PROJECT_ROOT, settings

app = FastAPI(
    title=settings.app_name,
    description="Backend API for AI Sales Support Hub",
    version=settings.app_version,
    docs_url=None,
    openapi_tags=[
        {
            "name": "Health",
            "description": "Health check endpoint",
        }
    ],
)

SWAGGER_CSS_URL = "/static/swagger-ui/swagger-ui.css"
SWAGGER_JS_URL = "/static/swagger-ui/swagger-ui-bundle.js"
SWAGGER_CSS_SRI = "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW"
SWAGGER_JS_SRI = "sha384-ZPehFMQommnnuaZ4rpxgkgTT2DKFVp4hZC/7pLit+9Lek9T1YGSo23eHFbvNkXkw"
CSP_NONCE: ContextVar[str] = ContextVar("csp_nonce")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(18)
    nonce_token = CSP_NONCE.set(nonce)
    try:
        response = await call_next(request)
    finally:
        CSP_NONCE.reset(nonce_token)
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}' 'strict-dynamic'; "
        "style-src 'self' 'nonce-" + nonce + "'; "
        "style-src-attr 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@app.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request) -> HTMLResponse:
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} - Swagger UI",
        swagger_js_url=SWAGGER_JS_URL,
        swagger_css_url=SWAGGER_CSS_URL,
        swagger_favicon_url="",
    )
    html = response.body.decode("utf-8")
    nonce = escape(CSP_NONCE.get(), quote=True)
    html = html.replace(
        f'<link type="text/css" rel="stylesheet" href="{SWAGGER_CSS_URL}">',
        f'<link type="text/css" rel="stylesheet" href="{SWAGGER_CSS_URL}" '
        f'integrity="{SWAGGER_CSS_SRI}" crossorigin="anonymous">',
    )
    html = html.replace(
        f'<script src="{SWAGGER_JS_URL}"></script>',
        f'<script src="{SWAGGER_JS_URL}" integrity="{SWAGGER_JS_SRI}" '
        'crossorigin="anonymous"></script>',
    )
    html = re.sub(r"<script(?=[\s>])", lambda _: f'<script nonce="{nonce}"', html)
    return HTMLResponse(content=html, status_code=response.status_code)

STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(products_router, prefix=settings.api_prefix)
app.include_router(documents_router, prefix=settings.api_prefix)
app.include_router(product_documents_router, prefix=settings.api_prefix)
app.include_router(ai_router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "ai-sales-support-hub-backend",
    }
