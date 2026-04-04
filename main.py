import random, string, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

rooms = {}

@app.get("/create-room")
async def create_room():
    code = "".join(random.choices(string.digits, k=4))
    rooms[code] = {"master": None, "teams": {}, "q_list": [], "q_idx": 0, "pts": 10, "pen": 0, "f_done": False}
    return {"room_code": code}

@app.websocket("/ws/{room}/{role}/{name}")
async def websocket_endpoint(websocket: WebSocket, room: str, role: str, name: str):
    await websocket.accept()
    if room not in rooms: return await websocket.close()
    r = rooms[room]
    if role == "master": r["master"] = websocket
    else: r["teams"][name] = {"ws": websocket, "score": 0, "hist": []}

    try:
        while True:
            data = await websocket.receive_json()
            if role == "master":
                if data["type"] == "LOAD": r["q_list"] = data["payload"]
                if data["type"] == "START":
                    r.update({"f_done": False, "pts": data["pts"], "pen": data["pen"]})
                    for t in r["teams"].values(): 
                        await t["ws"].send_json({"type": "NEW_Q", "q": r["q_list"][r["q_idx"]]})
                if data["type"] == "REVEAL":
                    for t in r["teams"].values(): await t["ws"].send_json({"type": "REV", "ans": r["q_list"][r["q_idx"]]["correct"]})
                if data["type"] == "NEXT": r["q_idx"] += 1
            else:
                t = r["teams"][name]
                is_c = str(data["ans"]).strip().upper() == str(r["q_list"][r["q_idx"]]["correct"]).strip().upper()
                t["score"] += int(r["pts"]) if is_c else int(r["pen"])
                t["hist"].append({"q": r["q_idx"]+1, "ans": data["ans"], "is_c": is_c})
                if is_c and not r["f_done"]:
                    r["f_done"] = True
                    await r["master"].send_json({"type": "BEEP", "url": data["buzzer"]})
                await r["master"].send_json({"type": "UP", "teams": {k: {"s": v["score"], "h": v["hist"]} for k,v in r["teams"].items()}})
    except WebSocketDisconnect: pass
