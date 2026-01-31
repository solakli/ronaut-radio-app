from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
@app.route("/now-playing")
def now_playing():
    try:
        with open("/root/now_playing.txt", "r") as f:
            track = f.read().strip()
    except FileNotFoundError:
        track = "No track playing"
    return jsonify(now_playing=track)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
