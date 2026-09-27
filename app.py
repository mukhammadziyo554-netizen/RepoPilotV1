"""RepoPilot Step 1: public GitHub repository discovery."""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_from_directory

from backend.config import github_token
from backend.conversations import ConversationCache
from backend.routes import api

ROOT = Path(__file__).resolve().parent


def create_app() -> Flask:
    load_dotenv(ROOT / ".env")
    app = Flask(__name__, static_folder=None)
    app.config["GITHUB_TOKEN"] = github_token()
    app.extensions["repopilot_conversations"] = ConversationCache()
    app.register_blueprint(api)

    @app.get("/")
    def home():
        return send_from_directory(ROOT, "index.html")

    @app.get("/style.css")
    def stylesheet():
        return send_from_directory(ROOT, "style.css")

    @app.get("/app.js")
    def frontend_script():
        return send_from_directory(ROOT, "app.js")

    @app.get("/ChatGPT Image Sep 25, 2026, 09_27_09 PM.png")
    def original_repopilot_logo():
        return send_from_directory(ROOT, "ChatGPT Image Sep 25, 2026, 09_27_09 PM.png")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=False,
        threaded=True,
    )
