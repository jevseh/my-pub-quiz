import random, string, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

rooms = {}

# 20 Unique Buzzer Sounds
BUZZER_POOL = [
    {"name": "Air Horn", "url": "https://actions.google.com/sounds/v1/alarms/air_horn.ogg"},
    {"name": "Beep", "url": "https://actions.google.com/sounds/v1/emergency/beep_warning.ogg"},
    {"name": "Clown", "url": "https://actions.google.com/sounds/v1/cartoon/clown_horn.ogg"},
    {"name": "Bugle", "url": "https://actions.google.com/sounds/v1/alarms/bugle_tune.ogg"},
    {"name": "Bell", "url": "https://actions.google.com/sounds/v1/transportation/bicycle_bell.ogg"},
    {"name": "Robot", "url": "https://actions.google.com/sounds/v1/science_fiction/robot_code_signal.ogg"},
    {"name": "Applause", "url": "https://actions.google.com/sounds/v1/human_voices/applause.ogg"},
    {"name": "Crash", "url": "https://actions.google.com/sounds/v1/impacts/crash_metal.ogg"},
    {"name": "Thunder", "url": "https://actions.google.com/sounds/v1/weather/thunder_crack.ogg"},
    {"name": "Dog", "url": "https://actions.google.com/sounds/v1/animals/dog_bark.ogg"},
    {"name": "Rooster", "url": "https://actions.google.com/sounds/v1/animals/rooster_crow.ogg"},
    {"name": "Splash", "url": "https://actions.google.com/sounds/v1/water/splash.ogg"},
    {"name": "Alien", "url": "https://actions.google.com/sounds/v1/science_fiction/alien_beacon.ogg"},
    {"name": "Teleport", "url": "https://actions.google.com/sounds/v1/science_fiction/teleport.ogg"},
    {"name": "Shutter", "url": "https://actions.google.com/sounds/v1/foley/camera_shutter.ogg"},
    {"name": "Drill", "url": "https://actions.google.com/sounds/v1/tools/power_drill.ogg"},
    {"name": "Alarm", "url": "https://actions.google.com/sounds/v1/alarms/alarm_clock_short.ogg"},
    {"name": "Creak", "url": "https://actions.google.com/sounds/v1/foley/door_creak.ogg"},
    {"name": "Sharpener", "url": "https://actions.google.com/sounds/v1/office/pencil_sharpener.ogg"},
    {"name": "Short Beep", "url": "https://actions.google.com/sounds/v1/ui/beep_short.ogg"}
]

@app.get("/create-room")
async def create_room():
    code = ''.join(random.choices(string.digits, k=4))
    rooms[code] = {
        "master": None, "teams": {}, "q_list": [], "q_idx": 0, 
        "status": "lobby", "pts": 10, "pen": 0, 
        "first_correct_found": False, "used_buzzers": []
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
        # Reconnection/Join Logic
        if name not in r["teams"]:
            r["teams"][name] = {
                "ws": websocket, "score": 0, "color": "#ffffff", "buzzer": "", 
                "hist": [], "streak": 0, "max_streak": 0, "first_counts": 0, "can_risk": True
            }
        else:
            r["teams"][name]["ws"] = websocket
    
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
                if data["type"] == "REVEAL_MASTER": r["status"] = "revealed_master"
                if data["type"] == "REVEAL_PLAYER": r["status"] = "revealed_player"
                if data["type"] == "NEXT": 
                    r["q_idx"] += 1
                    r["status"] = "idle"
                if data["type"] == "END": r["status"] = "ended"

            if role == "player":
                team = r["teams"][name]
                if data.get("type") == "JOIN_SETTINGS":
                    team["color"] = data["color"]
                    team["buzzer"] = data["buzzer"]
                
                if r["status"] == "active":
                    q = r["q_list"][r["q_idx"]]
                    # Jackpot Security
                    if q["type"].startswith("jackpot"):
                        leader = max(r["teams"].items(), key=lambda x: x[1]["score"])[0]
                        if name != leader: continue

                    ans = data["ans"].strip().upper()
                    is_c = ans == q["correct"].strip().upper()
                    
                    val = r["pts"]
                    if data.get("risking") and team["can_risk"]:
                        val *= 3
                        if not is_c: team["can_risk"] = False

                    if is_c:
                        if not r["first_correct_found"]:
                            r["first_correct_found"] = True
                            team["first_counts"] += 1
                            if r["master"]: await r["master"].send_json({"type": "BEEP", "url": team["buzzer"]})
                        team["score"] += val
                        team["streak"] += 1
                        team["max_streak"] = max(team["streak"], team["max_streak"])
                    else:
                        team["score"] -= r["pen"]
                        team["streak"] = 0
                    team["hist"].append({"ans": ans, "is_c": is_c})
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
