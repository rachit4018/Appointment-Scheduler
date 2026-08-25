import logging
from contextlib import asynccontextmanager
from pathlib import Path
 
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
 
from app.routers import appointments, users
from app.seed import seed
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
)
 
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
 
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed()
    yield
 
 
app = FastAPI(
    title="Patient Portal",
    description=(
        "Take-home assignment. API docs below; the app itself is at /.\n\n"
        "Identity comes from an X-User-Id header or a user_id cookie."
    ),
    lifespan=lifespan,
)
 
app.include_router(users.router)
app.include_router(appointments.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
 
 
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request):
    """One route for both roles.
 
    Which view renders is decided by the current user's role rather than by
    the URL. Keeping patient and provider on the same route means flipping
    the switcher cannot leave the page and the identity out of sync.
    """
    return templates.TemplateResponse(request, "index.html")
 
 
@app.get("/appointments/{appointment_id}", response_class=HTMLResponse,
         include_in_schema=False)
async def detail(request: Request, appointment_id: int):
    return templates.TemplateResponse(
        request, "detail.html", {"appointment_id": appointment_id}
    )
 
 
@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}
 
 
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse("/static/favicon.svg")
