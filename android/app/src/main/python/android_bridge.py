import os

_server = None


def start_server(client_id, client_secret, callback_url, data_dir, host, port):
    global _server
    os.environ["EVE_CLIENT_ID"] = str(client_id or "")
    os.environ["EVE_CLIENT_SECRET"] = str(client_secret or "")
    os.environ["EVE_CALLBACK_URL"] = str(callback_url or "http://localhost:8000/callback")
    os.environ["EVE_DATA_DIR"] = str(data_dir)
    os.environ["EVE_HOST"] = str(host or "127.0.0.1")
    os.environ["EVE_PORT"] = str(port or 8000)
    os.environ["EVE_USER_AGENT"] = "eve-pig-management-android/1.0 (local mobile tool)"

    import sys
    import uvicorn

    # Reload application modules so changed credentials are picked up after restart.
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]
    from app.main import app

    config = uvicorn.Config(
        app,
        host=str(host or "127.0.0.1"),
        port=int(port or 8000),
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    _server = server
    server.run()


def stop_server():
    global _server
    if _server is not None:
        _server.should_exit = True
