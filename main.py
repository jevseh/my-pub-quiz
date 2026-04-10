import random, string, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 👑 Security: Super Admin & 3 Hosts
USERS = {
    "admin": "admin123", 
    "host1": "quiz1",
    "host2": "quiz2",
    "host3": "quiz3"
}

rooms = {}

class LoginReq(BaseModel):
    username: str
    password: str

@app.post("/login")
async def login(req: LoginReq):
    if req.username in USERS and USERS[req.username] == req.password:
        code = ''.join(random.choices(string.digits, k=4))
        rooms[code] = {
            "owner": req.username, "master": None, "teams": {}, "q_list": [], 
            "q_idx": 0, "status": "lobby", "pts": 10, "pen": 0, "bonus_pct": 0, 
            "first_correct_found": False, "underdog_allowed": False
        }
        return {"success": True, "room_code": code}
    return {"success": False}

@app.websocket("/ws/{room}/{role}/{name}")
async def websocket_endpoint(websocket: WebSocket, room: str, role: str, name: str):
    await websocket.accept()
    if room not in rooms: 
        return await websocket.close()
    
    r = rooms[room]
    
    if role == "master": 
        r["master"] = websocket
    else:
        if name not in r["teams"]:
            r["teams"][name] = {
                "ws": websocket, "score": 0, "color": "#00E5FF", "buzzer": "", 
                "hist": [], "streak": 0, "max_streak": 0, "first_counts": 0, "underdog_used": False
            }
        else: 
            r["teams"][name]["ws"] = websocket
    
    await sync(room)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            # The Heartbeat
            if data.get("type") == "PING": 
                await websocket.send_json({"type": "PONG"})
                continue
                
            if role == "master":
                if data["type"] == "LOAD": r["q_list"] = data["payload"]
                if data["type"] == "START":
                    r["status"] = "active"
                    r["first_correct_found"] = False
                    r["pts"], r["pen"] = int(data.get("pts", 10)), int(data.get("pen", 0))
                    r["bonus_pct"] = int(data.get("bonus", 0))
                    r["underdog_allowed"] = data.get("underdog_allowed", False)
                if data["type"] == "LOCK": r["status"] = "locked"
                if data["type"] == "REVEAL_MASTER": r["status"] = "revealed_master"
                if data["type"] == "REVEAL_PLAYER": r["status"] = "revealed_player"
                if data["type"] == "BREAK": r["status"] = "break"
                if data["type"] == "NEXT": 
                    r["q_idx"] += 1
                    r["status"] = "idle"
                if data["type"] == "RESET_Q":
                    r["status"] = "idle"
                    r["first_correct_found"] = False
                    for t in r["teams"].values(): 
                        if t["hist"] and t["hist"][-1]["q"] == r["q_idx"]: 
                            t["hist"].pop()
            
            if role == "player":
                team = r["teams"][name]
                if data.get("type") == "JOIN_SETTINGS":
                    team["color"], team["buzzer"] = data["color"], data["buzzer"]
                if r["status"] == "active" and data.get("type") == "ANS":
                    if not r["q_list"]: continue
                    q = r["q_list"][r["q_idx"]]
                    ans = data["ans"].strip().upper()
                    correct_ans = str(q["correct"]).strip().upper()
                    
                    # Smart Scoring (Exact Match OR First Letter)
                    is_c = (ans == correct_ans) or (len(ans) == 1 and correct_ans.startswith(ans))
                    val = r["pts"]
                    
                    # Underdog Multiplier
                    using_underdog = data.get("underdog") and not team["underdog_used"] and r["underdog_allowed"]
                    if using_underdog: 
                        val *= 3
                        team["underdog_used"] = True

                    if is_c:
                        # Fastest Finger
                        if not r["first_correct_found"]:
                            r["first_correct_found"] = True
                            team["first_counts"] += 1
                            val += int(val * (r["bonus_pct"] / 100.0))
                            if r["master"]: 
                                await r["master"].send_json({"type": "BEEP", "url": team["buzzer"]})
                        team["score"] += val
                        team["streak"] += 1
                        team["max_streak"] = max(team["streak"], team["max_streak"])
                    else:
                        team["score"] -= r["pen"]
                        team["streak"] = 0
                    team["hist"].append({"ans": ans, "is_c": is_c, "q": r["q_idx"], "correct_ans": correct_ans})
            await sync(room)
    except WebSocketDisconnect: 
        pass

async def sync(room):
    r = rooms[room]
    q_data = r["q_list"][r["q_idx"]] if r["q_list"] and r["q_idx"]<len(r["q_list"]) else None
    msg = {
        "type": "UP", "status": r["status"], "q_idx": r["q_idx"], "q": q_data, "total_q": len(r["q_list"]),
        "teams": {k: {"s": v["score"], "c": v["color"], "bz": v["buzzer"], "h": v["hist"], "ms": v["max_streak"], "fc": v["first_counts"], "u_used": v["underdog_used"]} for k,v in r["teams"].items()},
        "underdog_allowed": r["underdog_allowed"]
    }
    if r["master"]: 
        try: await r["master"].send_json(msg)
        except: pass
    for t in r["teams"].values():
        try: await t["ws"].send_json(msg)
        except: pass
