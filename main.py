from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import asyncio
from typing import Dict, Any

app = FastAPI()

# --- SECURITY & CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Change to your Vercel domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURATION & GAME STATE ---
HOST_PASSWORDS = {"host1": "quiz123", "host2": "quiz123", "host3": "quiz123"}
MAX_ROOMS = 3

class LoginRequest(BaseModel):
    username: str
    password: str

# Structure: {
#   "room_code": {
#       "master": WebSocket,
#       "players": {"team_name": WebSocket},
#       "state": {
#           "status": "waiting|active|break",
#           "current_question": {},
#           "scores": {"team_name": 0},
#           "buzzer_selections": {"team_name": {"color": "", "sound": ""}},
#           "answered_current": [] # Tracks who answered and order for fastest finger
#       }
#   }
# }
active_rooms: Dict[str, Dict[str, Any]] = {}

# --- CONNECTION MANAGER ---
class ConnectionManager:
    async def connect_master(self, websocket: WebSocket, room_code: str):
        await websocket.accept()
        if room_code not in active_rooms:
            if len(active_rooms) >= MAX_ROOMS:
                await websocket.close(code=1008, reason="Server at capacity (Max 3 rooms).")
                return False
            # Initialize new room state
            active_rooms[room_code] = {
                "master": websocket,
                "players": {},
                "state": {
                    "status": "waiting",
                    "current_question": None,
                    "scores": {},
                    "buzzer_selections": {},
                    "answered_current": []
                }
            }
        else:
            # Master reconnected, update socket and send current state
            active_rooms[room_code]["master"] = websocket
            await self.send_to_master(room_code, {"type": "state_recovery", "state": active_rooms[room_code]["state"]})
        return True

    async def connect_player(self, websocket: WebSocket, room_code: str, team_name: str):
        await websocket.accept()
        if room_code not in active_rooms:
            await websocket.close(code=1008, reason="Invalid room code.")
            return False
        
        # Add or update player connection
        active_rooms[room_code]["players"][team_name] = websocket
        
        # Ensure team exists in state
        if team_name not in active_rooms[room_code]["state"]["scores"]:
            active_rooms[room_code]["state"]["scores"][team_name] = 0
            
        # Send current state to the reconnecting/new player
        await websocket.send_json({
            "type": "game_state", 
            "state": active_rooms[room_code]["state"]
        })
        
        # Notify master
        await self.send_to_master(room_code, {"type": "player_joined", "team_name": team_name})
        return True

    def disconnect_player(self, room_code: str, team_name: str):
        if room_code in active_rooms and team_name in active_rooms[room_code]["players"]:
            del active_rooms[room_code]["players"][team_name]
            # Note: We do NOT delete them from active_rooms[room_code]["state"] so they keep their score if they rejoin

    async def send_to_master(self, room_code: str, message: dict):
        room = active_rooms.get(room_code)
        if room and room.get("master"):
            try:
                await room["master"].send_json(message)
            except WebSocketDisconnect:
                room["master"] = None # Master dropped, wait for reconnect

    async def broadcast_to_players(self, room_code: str, message: dict):
        room = active_rooms.get(room_code)
        if room:
            disconnected_teams = []
            for team_name, ws in room["players"].items():
                try:
                    await ws.send_json(message)
                except Exception:
                    disconnected_teams.append(team_name)
            
            for team_name in disconnected_teams:
                self.disconnect_player(room_code, team_name)
                await self.send_to_master(room_code, {"type": "player_disconnected", "team_name": team_name})

manager = ConnectionManager()

# --- HTTP ROUTES ---
@app.post("/api/login")
async def login(req: LoginRequest):
    if req.username in HOST_PASSWORDS and HOST_PASSWORDS[req.username] == req.password:
        return {"status": "success"}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

@app.get("/api/room/{room_code}")
async def check_room(room_code: str):
    if room_code in active_rooms:
        return {"status": "exists", "unavailable_buzzers": active_rooms[room_code]["state"]["buzzer_selections"]}
    return {"status": "not_found"}

# --- WEBSOCKET ROUTES ---
@app.websocket("/ws/master/{room_code}")
async def websocket_master(websocket: WebSocket, room_code: str):
    if not await manager.connect_master(websocket, room_code):
        return
    
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            action = payload.get("action")
            
            # --- Master Event Routing ---
            if action == "heartbeat":
                await websocket.send_json({"type": "heartbeat_ack"})
                
            elif action == "send_question":
                # Clear previous answers list
                active_rooms[room_code]["state"]["answered_current"] = []
                active_rooms[room_code]["state"]["current_question"] = payload.get("question_data")
                await manager.broadcast_to_players(room_code, {"type": "new_question", "data": payload.get("question_data")})
                
            elif action == "update_scores":
                active_rooms[room_code]["state"]["scores"] = payload.get("scores")
                await manager.broadcast_to_players(room_code, {"type": "score_update", "scores": payload.get("scores")})
                
            elif action == "trigger_break":
                active_rooms[room_code]["state"]["status"] = "break"
                await manager.broadcast_to_players(room_code, {"type": "break_started", "leaderboard": payload.get("leaderboard")})
                
            elif action == "end_break":
                active_rooms[room_code]["state"]["status"] = "active"
                await manager.broadcast_to_players(room_code, {"type": "break_ended"})
                
            elif action == "declare_winner":
                await manager.broadcast_to_players(room_code, {"type": "quiz_ended", "winner": payload.get("winner")})
                
            elif action == "jackpot_offer":
                await manager.broadcast_to_players(room_code, {"type": "jackpot_trigger", "target_team": payload.get("team_name")})

    except WebSocketDisconnect:
        print(f"Master disconnected from room {room_code}")
        # Room state stays alive so master can refresh and rejoin

@app.websocket("/ws/player/{room_code}/{team_name}")
async def websocket_player(websocket: WebSocket, room_code: str, team_name: str):
    if not await manager.connect_player(websocket, room_code, team_name):
        return
    
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            action = payload.get("action")
            
            # --- Player Event Routing ---
            if action == "heartbeat":
                await websocket.send_json({"type": "heartbeat_ack"})
                
            elif action == "select_buzzer":
                # Save their color and sound choice
                active_rooms[room_code]["state"]["buzzer_selections"][team_name] = {
                    "color": payload.get("color"),
                    "sound": payload.get("sound")
                }
                await manager.send_to_master(room_code, {
                    "type": "buzzer_selected", 
                    "team_name": team_name, 
                    "selection": active_rooms[room_code]["state"]["buzzer_selections"][team_name]
                })
                
            elif action == "submit_answer":
                # Fastest finger logic: order is determined by arrival at backend
                answered_list = active_rooms[room_code]["state"]["answered_current"]
                is_first = len(answered_list) == 0
                
                if team_name not in answered_list:
                    answered_list.append(team_name)
                    
                await manager.send_to_master(room_code, {
                    "type": "answer_submitted",
                    "team_name": team_name,
                    "answer": payload.get("answer"),
                    "is_first": is_first,
                    "used_gamble": payload.get("used_gamble", False)
                })
                
    except WebSocketDisconnect:
        manager.disconnect_player(room_code, team_name)
        await manager.send_to_master(room_code, {"type": "player_disconnected", "team_name": team_name})
