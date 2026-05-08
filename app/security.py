from fastapi import Request, HTTPException


def require_auth(request: Request) -> None:
    if not request.session.get("authed"):
        accept = request.headers.get("accept", "")
        if request.headers.get("hx-request") == "true":
            raise HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        raise HTTPException(status_code=401, detail="auth required")
