import uvicorn

if __name__ == "__main__":
    # JWT query parameters supplied by upstream menus must never enter access logs.
    uvicorn.run("helper.app:create_app", factory=True, host="0.0.0.0", port=8080,
                workers=1, access_log=False, proxy_headers=False)
