import random, string, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Room Storage
rooms = {}

# Pre-defined professional buzzer sounds (20)
BUZZER_POOL = [
    "https://actions.google.com/sounds/v1/alarms/air_horn.ogg",
    "https://actions.google.com/sounds/v1/emergency/beep_warning.ogg",
    "https://actions.google.com/sounds/v1/cartoon/clown_horn.ogg",
    "https://actions.google.com/sounds/v1/alarms/bugle_tune.ogg",
    "https://actions.google.com/sounds/v1/alarms/alarm_clock_short.ogg",
    "https://actions.google.com/sounds/v1/science_fiction/robot_code_signal.ogg",
    "https://actions.google.com/sounds/v1/human_voices/applause.ogg",
    "https://actions.google.com/sounds/v1/impacts/crash_metal.ogg",
    "https://actions.google.com/sounds/v1/office/pencil_sharpener.ogg",
    "https://actions.google.com/sounds/v1/foley/door_creak.ogg",
    "https://actions.google.com/sounds/v1/transportation/bicycle_bell.ogg",
    "https://actions.google.com/sounds/v1/water/splash.ogg",
    "https://actions.google.com/sounds/v1/weather/thunder_crack.ogg",
    "https://actions.google.com/sounds/v1/animals/dog_bark.ogg",
    "https://actions.google.com/sounds/v1/animals/rooster_crow.ogg",
    "https://actions.google.com/sounds/v1/tools/power_drill.ogg",
    "https://actions.google.com/sounds/v1/science_fiction/alien_beacon.ogg",
    "https://actions.google.com/sounds/v1/science_fiction/teleport.ogg",
    "https://actions.google.com/sounds/v1/foley/camera_shutter.ogg",
    "https://actions.google.com/sounds/v1/ui/beep_short.ogg"
]

COLORS = ["#ff4444", "#44ff44", "#4444ff", "#ffff44", "#ff44ff", "#44ffff", "#ffa500", "#ffffff"]

@app.get("/create-room")
async def create_room():
    code = ''.join(random.choices(string.digits, k=4))
    rooms[code] = {
        "master": None, "teams": {}, "q_list": [], "q_idx": 0, 
        "status": "lobby", "available_buzzers": list(BUZZER_POOL), "available_colors": list(COLORS),
        "pts": 10, "pen": 0, "first_correct_found": False, "risk_enabled": False
    }
    return {"room_code": code}

@app.websocket("/ws/{room}/{role}/{name}")
async def websocket_endpoint(websocket: WebSocket, room: str, role: str, name: str):
    await websocket.accept()
    if room not in rooms: return await websocket.close()
    r = rooms[room]

    if role == "master":
        r["master"] = websocket
    else:
        # Reconnection Logic: If team exists, just update their socket
        if name not in r["teams"]:
            if not r["available_buzzers"]: return await websocket.close()
            bz = r["available_buzzers"].pop(0)
            col = r["available_colors"].pop(0) if r["available_colors"] else "#ffffff"
            r["teams"][name] = {
                "ws": websocket, "score": 0, "color": col, "buzzer": bz, 
                "hist": [], "streak": 0, "max_streak": 0, "first_counts": 0, "risked": False, "can_risk": True
            }
        else:
            r["teams"][name]["ws"] = websocket

    # Send initial state
    await sync(room)

    try:
        while True:
            data = await websocket.receive_json()
            if role == "master":
                if data["type"] == "LOAD": r["q_list"] = data["payload"]
                if data["type"] == "START":
                    r["status"] = "active"
                    r["first_correct_found"] = False
                    r["pts"] = int(data.get("pts", 10))
                    r["pen"] = int(data.get("pen", 0))
                    r["risk_enabled"] = data.get("risk", False)
                if data["type"] == "REVEAL_MASTER": r["status"] = "revealed_master"
                if data["type"] == "REVEAL_PLAYER": r["status"] = "revealed_player"
                if data["type"] == "NEXT": 
                    r["q_idx"] += 1
                    r["status"] = "idle"
                if data["type"] == "BREAK": r["status"] = "break"
                if data["type"] == "END": r["status"] = "ended"
            
            if role == "player" and r["status"] == "active":
                team = r["teams"][name]
                correct_ans = r["q_list"][r["q_idx"]]["correct"].strip().upper()
                is_c = data["ans"].strip().upper() == correct_ans
                
                # Multiplier Logic
                points = r["pts"]
                if data.get("risking") and team["can_risk"]:
                    points *= 3 # The multiplier
                    if not is_c: team["can_risk"] = False # Lose opportunity

                if is_c:
                    if not r["first_correct_found"]:
                        r["first_correct_found"] = True
                        team["first_counts"] += 1
                        if r["master"]: await r["master"].send_json({"type": "BEEP", "url": team["buzzer"]})
                    team["score"] += points
                    team["streak"] += 1
                    team["max_streak"] = max(team["streak"], team["max_streak"])
                else:
                    team["score"] -= r["pen"]
                    team["streak"] = 0
                
                team["hist"].append({"q": r["q_idx"], "ans": data["ans"], "is_c": is_c})

            await sync(room)
    except WebSocketDisconnect: pass

async def sync(room):
    r = rooms[room]
    msg = {
        "type": "UP", "status": r["status"], "q_idx": r["q_idx"],
        "q": r["q_list"][r["q_idx"]] if r["q_list"] and r["q_idx"] < len(r["q_list"]) else None,
        "teams": {k: {"s": v["score"], "c": v["color"], "bz": v["buzzer"], "h": v["hist"], "ms": v["max_streak"], "fc": v["first_counts"], "can_r": v["can_risk"]} for k, v in r["teams"].items()}
    }
    if r["master"]: await r["master"].send_json(msg)
    for t in r["teams"].values():
        try: await t["ws"].send_json(msg)
        except: pass
