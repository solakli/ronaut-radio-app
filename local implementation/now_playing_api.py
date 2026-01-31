from pathlib import Path
import os

from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

NOW_PLAYING_FILE = os.environ.get("NOW_PLAYING_FILE", "run/now_playing.txt")
NOW_PLAYING_PATH = Path(NOW_PLAYING_FILE)


@app.route("/now-playing")
def now_playing():
    try:
        track = NOW_PLAYING_PATH.read_text().strip()
        if not track:
            track = "No track playing"
    except FileNotFoundError:
        track = "No track playing"
    return jsonify(now_playing=track)


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", "5050"))
    app.run(host="0.0.0.0", port=port)
