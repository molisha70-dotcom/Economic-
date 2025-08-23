# keep_alive.py
from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.get("/")
def home():
    return "I'm alive"

def _run():
    port = int(os.getenv("PORT", "8080"))  # Renderが自動で環境変数PORTを注入
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=_run, daemon=True)
    t.start()
