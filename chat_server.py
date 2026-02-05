import hashlib
import sqlite3
import time

from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

DB_PATH = "/root/chat.db"
MAX_HISTORY = 100
RATE_LIMIT_SECONDS = 2
MAX_MSG_LENGTH = 300

# Track last message time per session for rate limiting
last_message = {}

# Track connected users
connected_users = set()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nickname TEXT NOT NULL,
        message TEXT NOT NULL,
        color TEXT NOT NULL,
        timestamp REAL NOT NULL
    )""")
    return conn


def name_to_color(name):
    """Generate a consistent color from a nickname."""
    h = hashlib.md5(name.encode()).hexdigest()
    r = (int(h[:2], 16) % 128) + 40
    g = (int(h[2:4], 16) % 128) + 40
    b = (int(h[4:6], 16) % 128) + 40
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


@socketio.on("connect")
def handle_connect():
    connected_users.add(request.sid)
    emit("user_count", {"count": len(connected_users)}, broadcast=True)
    # Send recent history to the newly connected client
    db = get_db()
    rows = db.execute(
        "SELECT nickname, message, color, timestamp FROM messages "
        "ORDER BY id DESC LIMIT ?",
        (MAX_HISTORY,),
    ).fetchall()
    db.close()
    history = [
        {"nickname": r[0], "message": r[1], "color": r[2], "timestamp": r[3]}
        for r in reversed(rows)
    ]
    emit("history", history)


@socketio.on("disconnect")
def handle_disconnect():
    connected_users.discard(request.sid)
    last_message.pop(request.sid, None)
    emit("user_count", {"count": len(connected_users)}, broadcast=True)


@socketio.on("send_message")
def handle_message(data):
    nickname = (data.get("nickname") or "anon").strip()[:20]
    message = (data.get("message") or "").strip()[:MAX_MSG_LENGTH]
    if not message:
        return

    # Rate limiting by session
    now = time.time()
    sid = request.sid
    if sid in last_message and (now - last_message[sid]) < RATE_LIMIT_SECONDS:
        emit("error", {"message": "Slow down!"})
        return
    last_message[sid] = now

    color = name_to_color(nickname)

    # Store in DB
    db = get_db()
    db.execute(
        "INSERT INTO messages (nickname, message, color, timestamp) "
        "VALUES (?, ?, ?, ?)",
        (nickname, message, color, now),
    )
    db.commit()
    # Prune old messages (keep last 500)
    db.execute(
        "DELETE FROM messages WHERE id NOT IN "
        "(SELECT id FROM messages ORDER BY id DESC LIMIT 500)"
    )
    db.commit()
    db.close()

    # Broadcast to all clients
    emit(
        "new_message",
        {
            "nickname": nickname,
            "message": message,
            "color": color,
            "timestamp": now,
        },
        broadcast=True,
    )


if __name__ == "__main__":
    get_db()  # Ensure table exists
    socketio.run(app, host="0.0.0.0", port=5051, allow_unsafe_werkzeug=True)
