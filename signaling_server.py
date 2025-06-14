#!/usr/bin/env python

import asyncio
import json
import logging
import websockets
import ssl # For potential WSS in future, not strictly needed for WS

# --- Configuration & Constants ---
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
LOG_FILE = "signaling_server.log"

# --- Logging Setup ---
logger = logging.getLogger(__name__)

def setup_logging():
    if logger.hasHandlers():
        logger.handlers.clear()

    logger.setLevel(logging.DEBUG)

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File Handler
    try:
        file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(threadName)s] %(module)s.%(funcName)s:%(lineno)d - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logger.debug("File logging configured for signaling server.")
    except IOError as e:
        logger.error(f"Could not set up file logging to {LOG_FILE}: {e}. Logs will go to console at DEBUG level.")
        console_handler.setLevel(logging.DEBUG)

setup_logging()

# --- Global Data Structures ---
# ROOMS: {room_id: {websocket_connection1, websocket_connection2}}
ROOMS = {}
# PEER_TO_ROOM: {websocket_connection: room_id}
PEER_TO_ROOM = {}

async def send_to_peer(websocket, message):
    """Sends a JSON message to a single peer."""
    try:
        await websocket.send(json.dumps(message))
        logger.debug(f"Sent message to {websocket.remote_address}: {message}")
    except websockets.exceptions.ConnectionClosed:
        logger.warning(f"Attempted to send to a closed connection: {websocket.remote_address}")
    except Exception as e:
        logger.error(f"Error sending message to {websocket.remote_address}: {e}", exc_info=True)

async def notify_peer_left(room_id, leaving_peer_websocket):
    """Notifies the other peer in the room that a peer has left."""
    if room_id in ROOMS:
        peers_in_room = ROOMS[room_id]
        for peer_ws in peers_in_room:
            if peer_ws != leaving_peer_websocket:
                logger.info(f"Notifying peer {peer_ws.remote_address} in room '{room_id}' that other peer left.")
                await send_to_peer(peer_ws, {"type": "peer_left"})
                break # Should only be one other peer

async def cleanup_peer(websocket):
    """Removes a peer from data structures and notifies the other peer if necessary."""
    if websocket in PEER_TO_ROOM:
        room_id = PEER_TO_ROOM[websocket]
        logger.info(f"Cleaning up peer {websocket.remote_address} from room '{room_id}'.")

        if room_id in ROOMS:
            ROOMS[room_id].discard(websocket)
            if not ROOMS[room_id]: # Room is now empty
                logger.debug(f"Room '{room_id}' is now empty, removing it.")
                del ROOMS[room_id]
            else:
                # If room still exists, means there was another peer. Notify them.
                await notify_peer_left(room_id, websocket)

        del PEER_TO_ROOM[websocket]
    else:
        logger.debug(f"Peer {websocket.remote_address} was not in any room, no cleanup needed from ROOMS/PEER_TO_ROOM.")


async def handler(websocket, path):
    """Handles incoming WebSocket connections and messages."""
    logger.info(f"New connection from {websocket.remote_address}, path: {path}")

    try:
        async for message_str in websocket:
            logger.debug(f"Received message from {websocket.remote_address}: {message_str[:200]}") # Log snippet
            try:
                message = json.loads(message_str)
                msg_type = message.get("type")
                room_id = message.get("room_id") # Expected for most operational messages

                if msg_type == "register":
                    if not room_id:
                        logger.warning(f"Registration from {websocket.remote_address} missing room_id.")
                        await send_to_peer(websocket, {"type": "error", "message": "Room ID is required for registration."})
                        continue

                    if websocket in PEER_TO_ROOM: # Peer is trying to register again or to a new room
                        old_room_id = PEER_TO_ROOM[websocket]
                        logger.warning(f"Peer {websocket.remote_address} already in room '{old_room_id}' tried to register for room '{room_id}'. Cleaning up old registration.")
                        await cleanup_peer(websocket) # Remove from old room first

                    if room_id not in ROOMS:
                        ROOMS[room_id] = set()
                        logger.info(f"Room '{room_id}' created by {websocket.remote_address}.")

                    if len(ROOMS[room_id]) >= 2:
                        logger.warning(f"Room '{room_id}' is full. Connection attempt from {websocket.remote_address} rejected.")
                        await send_to_peer(websocket, {"type": "error", "message": "Room is full"})
                        # Consider not closing immediately to allow client to react, or close after error.
                        # For now, we let the client decide to close or try another room.
                        # await websocket.close() # This would abruptly close.
                        continue

                    ROOMS[room_id].add(websocket)
                    PEER_TO_ROOM[websocket] = room_id
                    logger.info(f"Peer {websocket.remote_address} registered to room '{room_id}'. Room size: {len(ROOMS[room_id])}")
                    await send_to_peer(websocket, {"type": "registered", "room_id": room_id})


                    if len(ROOMS[room_id]) == 2:
                        logger.info(f"Room '{room_id}' now has two peers. Notifying both.")
                        for peer_ws in ROOMS[room_id]:
                            await send_to_peer(peer_ws, {"type": "peer_joined"})

                elif room_id and room_id in ROOMS and websocket in ROOMS[room_id]:
                    # For messages that need to be relayed (offer, answer, candidate, signal)
                    if msg_type in ["offer", "answer", "candidate", "signal", "offer_candidate", "answer_candidate"]: # Support combined types too
                        peers_in_room = ROOMS[room_id]
                        if len(peers_in_room) == 2:
                            other_peer = next(iter(p for p in peers_in_room if p != websocket), None)
                            if other_peer:
                                logger.info(f"Relaying '{msg_type}' from {websocket.remote_address} to {other_peer.remote_address} in room '{room_id}'.")
                                await send_to_peer(other_peer, message) # Relay the original message
                            else:
                                # This case should ideally not happen if room has 2 peers
                                logger.warning(f"Could not find other peer in room '{room_id}' for {websocket.remote_address} to relay '{msg_type}'.")
                        else:
                            logger.warning(f"Peer {websocket.remote_address} in room '{room_id}' tried to send '{msg_type}', but room does not have 2 peers (size: {len(peers_in_room)}). Message not relayed.")
                    else:
                        logger.warning(f"Unknown message type '{msg_type}' from {websocket.remote_address} in room '{room_id}'. Ignoring.")
                elif not room_id and msg_type != "register":
                     logger.warning(f"Received message type '{msg_type}' from {websocket.remote_address} without a room_id. Peer might not be registered or message is malformed.")
                     await send_to_peer(websocket, {"type": "error", "message": "Not registered in a room or room_id missing."})
                elif room_id and room_id not in ROOMS:
                    logger.warning(f"Received message for non-existent room '{room_id}' from {websocket.remote_address}. Type: '{msg_type}'.")
                    await send_to_peer(websocket, {"type": "error", "message": f"Room '{room_id}' does not exist or you are not part of it."})

            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from {websocket.remote_address}: {message_str}", exc_info=True)
                await send_to_peer(websocket, {"type": "error", "message": "Invalid JSON format."})
            except Exception as e:
                logger.error(f"Error processing message from {websocket.remote_address}: {e}", exc_info=True)
                await send_to_peer(websocket, {"type": "error", "message": "Server error processing message."})

    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Connection from {websocket.remote_address} closed normally.")
    except websockets.exceptions.ConnectionClosedError as e:
        logger.warning(f"Connection from {websocket.remote_address} closed with error: {e}")
    except Exception as e:
        logger.error(f"Unhandled exception in handler for {websocket.remote_address}: {e}", exc_info=True)
    finally:
        logger.info(f"Performing cleanup for disconnected peer: {websocket.remote_address}")
        await cleanup_peer(websocket)
        logger.info(f"Connection handler for {websocket.remote_address} finished.")


async def main():
    logger.info(f"Starting WebSocket signaling server on {SERVER_HOST}:{SERVER_PORT}")

    # For WSS (WebSocket Secure), you would configure SSL context here:
    # ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # ssl_context.load_cert_chain(path_to_your_cert, path_to_your_key)
    # start_server = websockets.serve(handler, SERVER_HOST, SERVER_PORT, ssl=ssl_context)

    # For WS (WebSocket, unencrypted):
    async with websockets.serve(handler, SERVER_HOST, SERVER_PORT) as server:
        logger.info("Signaling server is running.")
        try:
            await asyncio.Future()  # Run forever
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, shutting down server.")
        finally:
            # Perform any final cleanup if necessary, though websockets.serve handles its own.
            logger.info("Signaling server stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application shutting down (KeyboardInterrupt in main).")
    except Exception as e:
        logger.critical(f"Application failed to run: {e}", exc_info=True)

    logger.info("--- Application P2P Signaling Server Stopped ---")
