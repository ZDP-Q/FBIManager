import uvicorn

from app import create_app


app = create_app()


if __name__ == "__main__":
    try:
        # reload=False is safer for signal handling in some Windows environments
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, log_level="info")
    except (KeyboardInterrupt, SystemExit):
        # Graceful exit without printing Traceback noise
        pass
