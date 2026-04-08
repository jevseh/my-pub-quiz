import random, string, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

rooms = {}

@app.get("/create-room")
async def create_room():
    code = ''.join(random.choices(string.digits, k=4))
    rooms[code] = {"master": None, "teams": {}, "q_list": [], "q_idx": 0, "status": "lobby", "pts": 10, "pen": 0, "first_correct_found": False}
    return {"room_code": code}

@app.websocket("/ws/{room}/{role}/{name}")
async def websocket_endpoint(websocket: WebSocket, room: str, role: str, name: str):
    await websocket.accept()
    if room not in rooms: return await websocket.close()
    r = rooms[room]
    if role == "master": r["master"] = websocket
    else:
        if name not in r["teams"]:
            r["teams"][name] = {"ws": websocket, "score": 0, "color": "#ffffff", "buzzer": "", "hist": [], "streak": 0, "max_streak": 0, "first_counts": 0, "can_risk": True}
        else: r["teams"][name]["ws"] = websocket
    await sync(room)
    try:
        while True:
            data = await websocket.receive_json()
            if role == "master":
                if data["type"] == "LOAD": r["q_list"] = data["payload"]
                if data["type"] == "START":
                    r["status"] = "active"
                    r["first_correct_found"] = False
                    r["pts"], r["pen"] = int(data.get("pts", 10)), int(data.get("pen", 0))
                if data["type"] == "REVEAL_MASTER": r["status"] = "revealed_master"
                if data["type"] == "REVEAL_PLAYER": r["status"] = "revealed_player"
                if data["type"] == "NEXT": r["q_idx"] += 1; r["status"] = "idle"
                if data["type"] == "END": r["status"] = "ended"
            if role == "player":
                team = r["teams"][name]
                if data.get("type") == "JOIN_SETTINGS":
                    team["color"], team["buzzer"] = data["color"], data["buzzer"]
                if r["status"] == "active":
                    q = r["q_list"][r["q_idx"]]
                    if q["type"].startswith("jackpot"):
                        leader = max(r["teams"].items(), key=lambda x: x[1]["score"])[0]
                        if name != leader: continue
                    ans = data["ans"].strip().upper()
                    is_c = ans == q["correct"].strip().upper()
                    val = r["pts"] * 3 if (data.get("risking") and team["can_risk"]) else r["pts"]
                    if not is_c and data.get("risking"): team["can_risk"] = False
                    if is_c:
                        if not r["first_correct_found"]:
                            r["first_correct_found"] = True
                            team["first_counts"] += 1
                            if r["master"]: await r["master"].send_json({"type": "BEEP", "url": team["buzzer"]})
                        team["score"] += val
                        team["streak"] += 1
                        team["max_streak"] = max(team["streak"], team["max_streak"])
                    else: team["score"] -= r["pen"]; team["streak"] = 0
                    team["hist"].append({"ans": ans, "is_c": is_c})
            await sync(room)
    except WebSocketDisconnect: pass

async def sync(room):
    r = rooms[room]
    msg = {"type":"UP","status":r["status"],"q_idx":r["q_idx"],"q":r["q_list"][r["q_idx"]] if r["q_list"] and r["q_idx"]<len(r["q_list"]) else None,"teams":{k:{"s":v["score"],"c":v["color"],"bz":v["buzzer"],"h":v["hist"],"ms":v["max_streak"],"fc":v["first_counts"],"can_r":v["can_risk"]} for k,v in r["teams"].items()}}
    if r["master"]: await r["master"].send_json(msg)
    for t in r["teams"].values():
        try: await t["ws"].send_json(msg)
        except: pass
