from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.get("/")
def home():
    return "I'm alive"

def _run():
    port = int(os.getenv("PORT", "8080"))
    # reloaderを切るのが重要（forkされると検出に失敗しやすい）
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=_run, daemon=True)
    t.start()
