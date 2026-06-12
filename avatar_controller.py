import asyncio
import json
import os
import websockets

# Load the saved auth token if it exists
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "vts_token.txt")

class JarvisAvatar:
    def __init__(self):
        self.uri = "ws://127.0.0.1:8001"
        self.websocket = None
        self.auth_token = self._load_token()

    def _load_token(self):
        """Load saved VTS auth token from file."""
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r") as f:
                token = f.read().strip()
                if token:
                    print("🔑 VTS auth token loaded from file.")
                    return token
        return None

    def _save_token(self, token: str):
        """Persist new token to file for future sessions."""
        with open(TOKEN_FILE, "w") as f:
            f.write(token)
        self.auth_token = token

    async def _send(self, message: dict) -> dict:
        """Send a message and receive the response."""
        await self.websocket.send(json.dumps(message))
        raw = await self.websocket.recv()
        return json.loads(raw)

    async def connect_body(self):
        """Connect to VTube Studio and authenticate."""
        try:
            self.websocket = await websockets.connect(self.uri)
            print("💖 Connected to VTube Studio!")
            await self._authenticate()
        except Exception as e:
            print(f"⚠️ VTS WARNING: Could not connect: {e}")
            self.websocket = None

    async def _authenticate(self):
        """Handle the VTS plugin auth handshake."""
        # Step 1: If we have a saved token, try using it directly
        if self.auth_token:
            auth_response = await self._send({
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "AuthWithToken",
                "messageType": "AuthenticationRequest",
                "data": {
                    "pluginName": "Jarvis AI",
                    "pluginDeveloper": "Cherry",
                    "authenticationToken": self.auth_token
                }
            })
            if auth_response.get("data", {}).get("authenticated"):
                print("✅ VTS authenticated with saved token!")
                return
            else:
                print("⚠️ Saved token rejected, requesting a new one...")

        # Step 2: Request a fresh token (VTS will show a popup — user must click Allow)
        token_response = await self._send({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "TokenRequest",
            "messageType": "AuthenticationTokenRequest",
            "data": {
                "pluginName": "Jarvis AI",
                "pluginDeveloper": "Cherry",
                "pluginIcon": ""
            }
        })

        new_token = token_response.get("data", {}).get("authenticationToken")
        if not new_token:
            print("❌ VTS did not return a token. Did you click Allow in VTube Studio?")
            return

        self._save_token(new_token)

        # Step 3: Authenticate with the new token
        auth_response = await self._send({
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "AuthWithNewToken",
            "messageType": "AuthenticationRequest",
            "data": {
                "pluginName": "Jarvis AI",
                "pluginDeveloper": "Cherry",
                "authenticationToken": new_token
            }
        })

        if auth_response.get("data", {}).get("authenticated"):
            print("✅ VTS authenticated with new token! Saved for future sessions.")
        else:
            print(f"❌ Auth failed: {auth_response}")

    async def speak_animation(self, duration: float):
        """Inject mouth movement animation into VTS for the given duration."""
        if not self.websocket:
            return

        steps = 15
        interval = duration / steps

        try:
            for i in range(steps):
                value = 0.8 if i % 2 == 0 else 0.1
                await self.websocket.send(json.dumps({
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": "MouthOpen",
                    "messageType": "InjectParameterDataRequest",
                    "data": {
                        "faceFound": True,
                        "mode": "set",
                        "parameterValues": [
                            {"id": "MouthOpen", "value": value, "weight": 1.0}
                        ]
                    }
                }))
                # Drain the response so the socket buffer doesn't back up
                await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                await asyncio.sleep(interval)

            # Close mouth when done
            await self.websocket.send(json.dumps({
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "MouthClose",
                "messageType": "InjectParameterDataRequest",
                "data": {
                    "faceFound": True,
                    "mode": "set",
                    "parameterValues": [{"id": "MouthOpen", "value": 0.0, "weight": 1.0}]
                }
            }))
            await asyncio.wait_for(self.websocket.recv(), timeout=1.0)

        except Exception as err:
            print(f"⚠️ Mouth animation error: {err}")

    async def close_body(self):
        """Close the WebSocket connection cleanly."""
        if self.websocket:
            await self.websocket.close()