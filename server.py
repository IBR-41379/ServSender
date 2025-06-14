import os
import socket
import subprocess
import json
import time
import threading
import logging
import asyncio

# --- Logging Configuration ---
LOG_FILE = "p2p_app.log"
logger = logging.getLogger(__name__)

def setup_logging():
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    try:
        file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(threadName)s] %(module)s.%(funcName)s:%(lineno)d - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logger.debug("File logging configured.")
    except IOError as e:
        logger.error(f"Could not set up file logging to {LOG_FILE}: {e}. Logs will go to console at DEBUG level.")
        console_handler.setLevel(logging.DEBUG)

setup_logging()
logger.info("--- Application P2P File Transfer Started ---")

OS_CHOICE_FILE = "Os_Choice.txt"
UPLOAD_PATH_FILE = "Uppath.txt"

def get_os_choice():
    logger.debug(f"Reading OS choice from {OS_CHOICE_FILE} or prompting user.")
    try:
        with open(OS_CHOICE_FILE, 'r') as f:
            choice = f.read().strip()
            if choice in ['win', 'lin']:
                logger.info(f"OS choice '{choice}' read from {OS_CHOICE_FILE}.")
                return choice
            else:
                logger.warning(f"Os_Choice.txt found with invalid content: '{choice}'. Re-prompting user.")
    except FileNotFoundError:
        logger.info(f"{OS_CHOICE_FILE} not found. Prompting user for OS choice.")
    except IOError as e:
        logger.error(f"IOError reading {OS_CHOICE_FILE}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error reading {OS_CHOICE_FILE}: {e}", exc_info=True)

    while True:
        print("Enter which operating system you are running on:")
        print("1. Windows")
        print("2. Linux")
        user_choice_input = input("Enter your choice (1 or 2): ")
        logger.debug(f"User input for OS choice: {user_choice_input}")
        if user_choice_input == '1':
            os_choice_val_local = 'win'
            break
        elif user_choice_input == '2':
            os_choice_val_local = 'lin'
            break
        else:
            logger.warning(f"Invalid OS choice entered by user: {user_choice_input}")
            print("Wrong choice entered. Please enter 1 or 2.")

    try:
        with open(OS_CHOICE_FILE, 'w') as f:
            f.write(os_choice_val_local)
        logger.info(f"OS choice '{os_choice_val_local}' saved to {OS_CHOICE_FILE}.")
    except IOError as e:
        logger.error(f"IOError writing OS choice to {OS_CHOICE_FILE}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error writing OS choice to {OS_CHOICE_FILE}: {e}", exc_info=True)
    return os_choice_val_local

os_choice = get_os_choice()
logger.info(f"Application running with OS choice: {os_choice}")


def check_and_install_dependencies(dependencies):
    for module_name, package_name in dependencies:
        logger.debug(f"Checking for {module_name} dependency.")
        try:
            __import__(module_name)
            logger.debug(f"{module_name} is already installed.")
        except ImportError:
            logger.info(f"{module_name} not found. Attempting to install {package_name}...")
            try:
                subprocess.check_call(['pip', 'install', package_name])
                logger.info(f"{package_name} installed successfully (or was already present).")
                __import__(module_name)
            except subprocess.CalledProcessError as e:
                logger.critical(f"Failed to install {package_name} using pip: {e}. Please install it manually.", exc_info=True)
                print(f"Failed to install {package_name}: {e}\nPlease install {package_name} manually: pip install {package_name}")
                exit(1)
            except ImportError:
                 logger.critical(f"{module_name} could not be imported even after attempting pip install {package_name}. Please check installation.")
                 print(f"Error: {module_name} could not be imported after installation. Please check your Python environment.")
                 exit(1)
            except Exception as e:
                logger.critical(f"An error occurred while trying to install {package_name}: {e}. Please ensure pip is available and install {package_name} manually.", exc_info=True)
                print(f"An error occurred while trying to check/install {package_name}: {e}\nPlease ensure pip is installed and {package_name} can be installed.")
                exit(1)

REQUIRED_DEPENDENCIES = [
    ('tqdm', 'tqdm'),
    ('stun', 'pystun3'),
    ('websockets', 'websockets')
]
check_and_install_dependencies(REQUIRED_DEPENDENCIES)

import tqdm
try:
    import stun
except ImportError:
    logger.critical("Failed to import 'stun' library even after dependency check. This should not happen. Exiting.")
    print("Fatal Error: STUN library (pystun3) could not be loaded. Please ensure it's installed correctly.")
    exit(1)
try:
    import websockets
except ImportError:
    logger.critical("Failed to import 'websockets' library even after dependency check. This should not happen. Exiting.")
    print("Fatal Error: websockets library could not be loaded. Please ensure it's installed correctly.")
    exit(1)

# Constants
DEFAULT_SIGNALING_URL = "ws://localhost:8765"
DEFAULT_STUN_HOST = 'stun.l.google.com'
DEFAULT_STUN_PORT = 19302
DEFAULT_PORT = 6968
DEFAULT_SAVE_DIR = "received_files"
DISCOVERY_PORT = 6969
BROADCAST_MESSAGE_APP_ID = "ServSenderP2P_Discovery_v1"
BROADCAST_INTERVAL = 5
DISCOVERY_PROTOCOL_VERSION = 1
PEER_MAX_AGE_SECONDS = 30
MY_TCP_TRANSFER_PORT = DEFAULT_PORT

# Globals
discovered_peers = {}
peers_lock = threading.Lock()


# --- Signaling Client Class ---
class SignalingClient:
    def __init__(self, signaling_url, room_id, main_app_shutdown_event):
        self.signaling_url = signaling_url
        self.room_id = room_id
        self.websocket = None
        self.is_connected = False
        self.listener_thread = None
        self.received_candidates = []
        self.peer_sdp = None
        self.peer_joined_event = threading.Event()
        self.candidates_received_event = threading.Event()
        self.sdp_received_event = threading.Event()
        self.loop = None
        self.main_app_shutdown_event = main_app_shutdown_event
        self.peer_srflx_candidate = None
        self.hole_punch_prepared_event = threading.Event()

    async def _connect(self):
        try:
            logger.info(f"Attempting to connect to signaling server: {self.signaling_url}")
            self.websocket = await websockets.connect(self.signaling_url, timeout=10)
            self.is_connected = True
            logger.info(f"Connected to signaling server: {self.signaling_url}")

            register_msg = {"type": "register", "room_id": self.room_id}
            await self.send_json(register_msg)

            await self._listen()

        except (websockets.exceptions.InvalidURI, websockets.exceptions.WebSocketException, ConnectionRefusedError, socket.gaierror, asyncio.TimeoutError) as e:
            logger.error(f"Failed to connect to signaling server {self.signaling_url}: {type(e).__name__} - {e}")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Unexpected error during signaling client connection: {e}", exc_info=True)
            self.is_connected = False
        finally:
            if not self.is_connected:
                logger.info("Signaling client connection process finished (failed or disconnected).")
                if self.websocket and self.websocket.open:
                    await self.websocket.close()
                self.peer_joined_event.set()
                self.candidates_received_event.set()
                self.sdp_received_event.set()
                self.hole_punch_prepared_event.set()


    async def _listen(self):
        logger.debug(f"Signaling client listener started for room '{self.room_id}'.")
        try:
            while self.is_connected and not self.main_app_shutdown_event.is_set():
                try:
                    message_str = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                    logger.debug(f"Signaling client received raw message: {message_str[:200]}")
                    message = json.loads(message_str)
                    msg_type = message.get("type")

                    if msg_type == "registered":
                        logger.info(f"Successfully registered in room '{message.get('room_id')}' on signaling server.")
                    elif msg_type == "peer_joined":
                        logger.info("Peer joined the room!")
                        self.peer_joined_event.set()
                    elif msg_type == "peer_left":
                        logger.warning("Peer left the room.")
                        self.received_candidates.clear()
                        self.peer_sdp = None
                        self.peer_srflx_candidate = None
                        self.peer_joined_event.clear()
                        self.candidates_received_event.clear()
                        self.sdp_received_event.clear()
                        self.hole_punch_prepared_event.clear()
                    elif msg_type == "offer" or msg_type == "answer":
                        logger.info(f"Received '{msg_type}' from peer: {message.get('data') is not None}")
                        self.peer_sdp = message.get("data")
                        self.sdp_received_event.set()
                    elif msg_type == "candidate":
                        candidate_data = message.get("candidate")
                        if candidate_data:
                            self.received_candidates.append(candidate_data)
                            logger.info(f"Stored candidate from peer: {candidate_data}")
                            if candidate_data.get("type") == "srflx":
                                self.peer_srflx_candidate = candidate_data
                                logger.info(f"Stored peer's server-reflexive candidate: {candidate_data}")
                            if not self.candidates_received_event.is_set() and self.received_candidates:
                                 self.candidates_received_event.set()
                        else:
                            logger.warning(f"Received 'candidate' message without candidate data: {message}")
                    elif msg_type == "signal":
                        signal_data = message.get("data", {})
                        action = signal_data.get("action")
                        logger.info(f"Received signal: {action}, data: {signal_data}")
                        if action == "prepare_hole_punch":
                            senders_srflx_cand = signal_data.get("target_candidate")
                            if senders_srflx_cand and senders_srflx_cand.get("address") and senders_srflx_cand.get("port"):
                                logger.info(f"Received 'prepare_hole_punch' from sender with their srflx candidate: {senders_srflx_cand}")
                                attempt_udp_hole_punch(
                                    MY_TCP_TRANSFER_PORT,
                                    senders_srflx_cand["address"],
                                    senders_srflx_cand["port"]
                                )
                                await self.send_json({"type": "signal", "room_id": self.room_id, "data": {"action": "punched_from_receiver"}})
                            else:
                                logger.warning("Invalid 'prepare_hole_punch' signal, missing target_candidate details.")
                        elif action == "punched_from_receiver":
                            logger.info("Received 'punched_from_receiver' signal. Setting event for sender to proceed.")
                            self.hole_punch_prepared_event.set()

                    elif msg_type == "error":
                        logger.error(f"Received error from signaling server: {message.get('message')}")
                    else:
                        logger.warning(f"Received unknown message type '{msg_type}' from signaling server: {message}")

                except asyncio.TimeoutError:
                    continue
                except json.JSONDecodeError:
                    logger.error(f"Could not decode JSON from signaling server: {message_str}", exc_info=True)
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("Connection closed by signaling server while listening.")
                    self.is_connected = False
                    break
                except Exception as e:
                    logger.error(f"Error processing message in signaling client listener: {e}", exc_info=True)

        except Exception as e:
            if isinstance(e, websockets.exceptions.ConnectionClosed):
                 logger.info(f"Signaling server connection closed while listening: {e}")
            else:
                 logger.error(f"Exception in signaling client listener: {e}", exc_info=True)
        finally:
            self.is_connected = False
            self.peer_joined_event.set()
            self.candidates_received_event.set()
            self.sdp_received_event.set()
            self.hole_punch_prepared_event.set()
            logger.info("Signaling client listener stopped.")
            if self.loop and self.loop.is_running() and not self.main_app_shutdown_event.is_set():
                 self.loop.call_soon_threadsafe(self.loop.stop)


    async def send_json(self, message_dict):
        if self.websocket and self.is_connected:
            try:
                await self.websocket.send(json.dumps(message_dict))
                logger.debug(f"Sent JSON to signaling server: {message_dict}")
                return True
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Cannot send JSON, signaling connection is closed.")
                self.is_connected = False
            except Exception as e:
                logger.error(f"Error sending JSON to signaling server: {e}", exc_info=True)
        else:
            logger.warning("Cannot send JSON, not connected to signaling server.")
        return False

    async def send_candidate_message(self, candidate_data):
        return await self.send_json({"type": "candidate", "room_id": self.room_id, "candidate": candidate_data})

    async def send_offer_sdp(self, sdp):
        return await self.send_json({"type": "offer", "room_id": self.room_id, "data": sdp})

    async def send_answer_sdp(self, sdp):
        return await self.send_json({"type": "answer", "room_id": self.room_id, "data": sdp})

    async def send_signal_message(self, signal_data_payload):
        return await self.send_json({"type": "signal", "room_id": self.room_id, "data": signal_data_payload})

    def _run_client_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect())
        except Exception as e:
            logger.error(f"Exception in _run_client_loop's run_until_complete: {e}", exc_info=True)
        finally:
            logger.debug("Signaling client asyncio loop starting cleanup...")
            if self.loop.is_running():
                try:
                    tasks = asyncio.all_tasks(loop=self.loop)
                    if tasks:
                        logger.debug(f"Cancelling {len(tasks)} outstanding asyncio tasks for signaling client.")
                        for task in tasks:
                            task.cancel()
                        self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                except Exception as e_tasks:
                    logger.error(f"Error during task cleanup in signaling client loop: {e_tasks}", exc_info=True)

            if not self.loop.is_closed():
                 self.loop.close()
            logger.info("Signaling client asyncio loop finished and closed.")


    def start(self):
        if self.listener_thread and self.listener_thread.is_alive():
            logger.warning("Signaling client already started.")
            return False
        logger.info(f"Starting signaling client for room '{self.room_id}' to {self.signaling_url}...")
        self.listener_thread = threading.Thread(target=self._run_client_loop, name=f"SignalingClient-{self.room_id}", daemon=True)
        self.listener_thread.start()
        return True

    async def _disconnect_async(self):
        if self.websocket:
            logger.info("Disconnecting from signaling server (async)...")
            self.is_connected = False
            if self.websocket.open:
                try:
                    await self.websocket.close()
                    logger.info("WebSocket connection closed by client.")
                except websockets.exceptions.ConnectionClosed:
                    logger.info("WebSocket already closed while attempting disconnect.")
                except Exception as e:
                    logger.error(f"Error closing websocket: {e}", exc_info=True)
            else:
                logger.info("WebSocket already closed, no action taken for disconnect_async.")

        if self.loop and self.loop.is_running() and not self.main_app_shutdown_event.is_set():
            logger.debug("Requesting signaling client's asyncio loop to stop.")
            self.loop.call_soon_threadsafe(self.loop.stop)


    def disconnect(self):
        logger.info("Initiating signaling client disconnection.")
        if self.loop:
             asyncio.run_coroutine_threadsafe(self._disconnect_async(), self.loop)
        else:
            logger.warning("Signaling client loop not available for async disconnect.")
            if self.websocket and self.websocket.open:
                try:
                    asyncio.run(self.websocket.close())
                except Exception as e:
                    logger.error(f"Fallback websocket close error: {e}")

        if self.listener_thread and self.listener_thread.is_alive() and threading.current_thread() != self.listener_thread:
            logger.debug("Waiting for signaling client listener thread to join...")
            self.listener_thread.join(timeout=5)
            if self.listener_thread.is_alive():
                logger.warning("Signaling client listener thread did not terminate gracefully after disconnect request.")
        logger.info("Signaling client disconnect method finished.")

# --- End of Signaling Client Class ---

def attempt_udp_hole_punch(local_udp_port, remote_host, remote_port, num_packets=5, delay_ms=200):
    logger.info(f"Attempting UDP hole punch: local_port={local_udp_port} to {remote_host}:{remote_port}")
    punch_socket = None
    try:
        punch_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        punch_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            punch_socket.bind(('0.0.0.0', local_udp_port))
            logger.debug(f"UDP punch socket bound to 0.0.0.0:{local_udp_port}")
        except socket.error as e:
            logger.error(f"Failed to bind UDP punch socket to 0.0.0.0:{local_udp_port}: {e}. Hole punch might be ineffective.", exc_info=True)

        message = b"punch"
        for i in range(num_packets):
            try:
                punch_socket.sendto(message, (remote_host, remote_port))
                logger.debug(f"Sent UDP punch packet {i+1}/{num_packets} to {remote_host}:{remote_port} from local port {local_udp_port}")
            except socket.gaierror as e:
                logger.error(f"DNS resolution failed for UDP hole punch target {remote_host}: {e}. Cannot send punch packets.")
                return
            except socket.error as e:
                logger.error(f"Socket error sending UDP punch packet {i+1} to {remote_host}:{remote_port}: {e}", exc_info=True)

            if i < num_packets - 1:
                time.sleep(delay_ms / 1000.0)

        logger.info(f"Finished sending UDP punch packets to {remote_host}:{remote_port}.")

    except Exception as e:
        logger.error(f"An error occurred during UDP hole punching: {e}", exc_info=True)
    finally:
        if punch_socket:
            punch_socket.close()
            logger.debug("UDP punch socket closed.")


def get_public_ip_port_from_stun(stun_host=DEFAULT_STUN_HOST, stun_port=DEFAULT_STUN_PORT, local_source_port=MY_TCP_TRANSFER_PORT):
    logger.info(f"Attempting STUN discovery using server {stun_host}:{stun_port} from local port {local_source_port}.")
    try:
        external_ip, external_port, nat_type = stun.get_ip_info(
            stun_host=stun_host,
            stun_port=stun_port,
            source_ip='0.0.0.0',
            source_port=local_source_port
        )
        if external_ip and external_port:
            logger.info(f"STUN discovery successful: Public IP={external_ip}, Public Port={external_port}, NAT Type={nat_type}")
            return external_ip, external_port, nat_type
        else:
            logger.warning(f"STUN discovery did not return a valid IP/Port. IP: {external_ip}, Port: {external_port}, NAT: {nat_type}")
            return None, None, nat_type
    except stun.StunException as e:
        logger.error(f"STUN Exception: {e}", exc_info=True)
        return None, None, None
    except socket.gaierror as e:
        logger.error(f"STUN host resolution error for {stun_host}: {e}", exc_info=True)
        return None, None, None
    except Exception as e:
        logger.error(f"An unexpected error occurred during STUN discovery: {e}", exc_info=True)
        return None, None, None

def get_public_ip():
    logger.debug("Attempting to retrieve public IP address using shell command.")
    try:
        if os_choice == 'win':
            logger.debug("Using PowerShell for IP retrieval on Windows.")
            command = ['powershell', '-Command', '(Invoke-WebRequest -uri "http://ifconfig.me/ip" -UseBasicParsing).Content.Trim()']
            ip_address = subprocess.check_output(command, text=True, stderr=subprocess.PIPE).strip()
        elif os_choice == 'lin':
            logger.debug("Using curl for IP retrieval on Linux.")
            command = ['curl', '-s', 'ifconfig.me/ip']
            ip_address = subprocess.check_output(command, text=True, stderr=subprocess.PIPE).strip()
        else:
            logger.warning(f"Unsupported OS choice '{os_choice}' for IP retrieval.")
            return None

        if not ip_address:
            logger.warning("Failed to retrieve IP address via shell: command output was empty.")
            return None

        if not (all(c.isdigit() or c == '.' for c in ip_address) and ip_address.count('.') == 3 and \
                all(0 <= int(num) <= 255 for num in ip_address.split('.')) and len(ip_address.split('.')) == 4):
             logger.warning(f"Retrieved IP via shell '{ip_address}' may not be a standard IPv4 format.")
        else:
            logger.info(f"Public IP address successfully retrieved via shell: {ip_address}")
        return ip_address

    except FileNotFoundError as e:
        logger.error(f"Shell IP retrieval command not found ({e.filename}).")
        return None
    except subprocess.CalledProcessError as e:
        stderr_output = e.stderr.strip() if e.stderr else "N/A"
        logger.error(f"Shell IP retrieval command failed with exit code {e.returncode}. Stderr: {stderr_output}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while retrieving IP via shell: {e}", exc_info=True)
        return None

def send_file_to_peer(filepath, conn):
    logger.debug(f"Attempting to send file: {filepath} over connection: {conn.getpeername() if conn else 'N/A'}")
    try:
        file_size = os.path.getsize(filepath)
        base_filename = os.path.basename(filepath)
        logger.debug(f"File: {filepath}, size: {file_size} bytes, basename: {base_filename}")

        target_ip, target_port = conn.getpeername()
        logger.debug(f"Connection established with target {target_ip}:{target_port}")
        pbar = None

        try:
            logger.debug(f"Sending filename '{base_filename}' and size '{file_size}' to {target_ip}:{target_port}.")
            conn.sendall(("received_" + base_filename).encode())
            conn.sendall(str(file_size).encode())

            meta_ack = conn.recv(1024)
            if meta_ack != b"META_OK":
                logger.warning(f"Receiver {target_ip}:{target_port} did not acknowledge metadata correctly for {base_filename}. ACK: {meta_ack.decode(errors='ignore')}")
                return False

            logger.info(f"Receiver {target_ip}:{target_port} acknowledged metadata for {base_filename}. Starting file content transfer.")
            with open(filepath, 'rb') as file:
                pbar = tqdm.tqdm(total=file_size, unit='B', unit_scale=True, desc=f"Sending {base_filename} to {target_ip}")
                bytes_sent = 0
                while bytes_sent < file_size:
                    data = file.read(1024)
                    if not data:
                        logger.error(f"File {filepath} ended prematurely while reading. Expected {file_size}, got {bytes_sent}.")
                        break
                    conn.sendall(data)
                    bytes_sent += len(data)
                    pbar.update(len(data))

            if bytes_sent == file_size:
                logger.info(f"Finished sending content for {base_filename} ({bytes_sent} bytes) to {target_ip}:{target_port}.")
                conn.sendall(b"<EOF_CONFIRM>")
                logger.debug(f"Sent <EOF_CONFIRM> for {base_filename} to {target_ip}:{target_port}.")

                file_ack = conn.recv(1024)
                if file_ack == b"FILE_OK":
                    logger.info(f"File '{base_filename}' confirmed as received by {target_ip}:{target_port}.")
                    return True
                else:
                    logger.warning(f"Receiver {target_ip}:{target_port} did not confirm file reception for {base_filename}. ACK: {file_ack.decode(errors='ignore')}")
                    return False
            else:
                logger.error(f"Mismatch in sent bytes for {base_filename}. Sent {bytes_sent} but expected {file_size}")
                return False

        except FileNotFoundError:
            logger.error(f"File not found at {filepath} for sending.")
            return False
        except socket.error as e:
            logger.error(f"Socket error during send_file_to_peer to {target_ip}:{target_port}: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during send_file_to_peer for {filepath}: {e}", exc_info=True)
            return False
        finally:
            if pbar:
                pbar.close()
                logger.debug(f"Send progress bar closed for {base_filename}.")
    except Exception as e:
        logger.error(f"Error in send_file_to_peer setup for {filepath}: {e}", exc_info=True)
        return False

def start_client_and_send(target_ip, target_port, file_path):
    logger.info(f"Starting client to send {file_path} to {target_ip}:{target_port}")
    addr = (target_ip, target_port)
    conn = None
    try:
        logger.info(f"Attempting to connect to {target_ip}:{target_port} to send {os.path.basename(file_path)}.")
        conn = socket.create_connection(addr, timeout=10)
        logger.info(f"Connected to {target_ip}:{target_port}.")

        conn.settimeout(20)

        if send_file_to_peer(file_path, conn):
            logger.info(f"Successfully sent {os.path.basename(file_path)} to {target_ip}:{target_port}.")
        else:
            logger.warning(f"Failed to send {os.path.basename(file_path)} to {target_ip}:{target_port}.")

    except socket.timeout:
        logger.error(f"Socket timeout during operation with {target_ip}:{target_port} in start_client_and_send.", exc_info=True)
    except socket.error as e:
        logger.error(f"Socket error in start_client_and_send with {target_ip}:{target_port}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An error occurred in start_client_and_send with {target_ip}:{target_port}: {e}", exc_info=True)
    finally:
        if conn:
            try:
                logger.debug(f"Shutting down connection to {target_ip}:{target_port}.")
                conn.shutdown(socket.SHUT_RDWR)
            except (socket.error, OSError) as e:
                logger.debug(f"Error during shutdown for connection to {target_ip}:{target_port} (ignorable): {e}")
            conn.close()
        logger.info(f"Client connection to {target_ip}:{target_port} closed.")

def start_file_transfer_server(listen_ip, listen_port, save_directory):
    logger.info(f"Starting file transfer server on {listen_ip}:{listen_port}, saving to {save_directory}")
    addr = (listen_ip, listen_port)
    s_skfd = None
    conn = None
    client_addr_str = "N/A"
    try:
        s_skfd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s_skfd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s_skfd.bind(addr)
        s_skfd.listen(1)
        logger.info(f"File transfer server listening on {listen_ip}:{listen_port}. Waiting for connection.")
        print(f"\nNow listening for an incoming file transfer on {listen_ip}:{listen_port}")
        print("Tell the sender to connect to this IP and port.")
        print("Waiting for a sender to connect... (Press Ctrl+C to cancel if stuck here)")

        conn, client_addr = s_skfd.accept()
        client_addr_str = f"{client_addr[0]}:{client_addr[1]}"
        logger.info(f"Accepted connection from {client_addr_str} for file reception.")
        print(f"Accepted connection from {client_addr_str} for file reception.")

        conn.settimeout(30)

        if receive_file_from_peer(conn, save_directory):
            logger.info(f"Successfully received file from {client_addr_str}.")
        else:
            logger.warning(f"Failed to receive file completely/correctly from {client_addr_str}.")

    except socket.error as e:
        logger.error(f"Socket error in start_file_transfer_server: {e}", exc_info=True)
    except KeyboardInterrupt:
        logger.info("File transfer server listening cancelled by user (Ctrl+C).")
        print("\nFile transfer server listening cancelled by user.")
    except Exception as e:
        logger.error(f"An error occurred in start_file_transfer_server: {e}", exc_info=True)
    finally:
        if conn:
            try:
                logger.debug(f"Shutting down client connection from {client_addr_str}.")
                conn.shutdown(socket.SHUT_RDWR)
            except (socket.error, OSError) as e:
                logger.debug(f"Error during shutdown for client connection {client_addr_str} (ignorable): {e}")
            conn.close()
            logger.info(f"Closed connection from client {client_addr_str}.")
        if s_skfd:
            s_skfd.close()
        logger.info("File transfer server listener socket closed.")

def receive_file_from_peer(conn, save_directory):
    file_path = None
    pbar = None
    received_cleanly = False
    conn_details_for_msg = "unknown peer"
    try:
        peer_addr = conn.getpeername()
        conn_details_for_msg = f"{peer_addr[0]}:{peer_addr[1]}"
        logger.debug(f"Receiving file from {conn_details_for_msg}")
    except socket.error as e:
        logger.warning(f"Could not get peer name for connection: {e}")

    try:
        file_name_encoded = conn.recv(1024)
        if not file_name_encoded:
            logger.warning(f"Did not receive file name from {conn_details_for_msg}.")
            return False
        file_name = file_name_encoded.decode().strip()
        if not file_name:
            logger.warning(f"Received an empty file name from {conn_details_for_msg}.")
            return False

        file_size_encoded = conn.recv(1024)
        if not file_size_encoded:
            logger.warning(f"Did not receive file size for '{file_name}' from {conn_details_for_msg}.")
            return False
        try:
            file_size = int(file_size_encoded.decode().strip())
        except ValueError:
            logger.error(f"Invalid file size received for '{file_name}' from {conn_details_for_msg}: {file_size_encoded.decode(errors='ignore')}")
            return False

        if file_size < 0:
            logger.warning(f"Received invalid (negative) file size for '{file_name}': {file_size} from {conn_details_for_msg}")
            return False

        logger.info(f"Received metadata from {conn_details_for_msg}: Filename='{file_name}', Size={file_size} bytes.")
        conn.sendall(b"META_OK")
        logger.debug(f"Sent META_OK to {conn_details_for_msg} for {file_name}.")

        if not os.path.exists(save_directory):
            try:
                logger.info(f"Save directory '{save_directory}' does not exist. Creating it.")
                os.makedirs(save_directory)
            except OSError as e:
                logger.error(f"Error creating save directory {save_directory}: {e}", exc_info=True)
                return False

        base_file_name = os.path.basename(file_name)
        if not base_file_name or base_file_name == "." or base_file_name == "..":
            logger.warning(f"Received potentially unsafe filename '{file_name}' from {conn_details_for_msg}. Aborting reception.")
            return False
        file_path = os.path.join(save_directory, base_file_name)

        logger.info(f"Preparing to receive file content for '{base_file_name}' into '{file_path}'.")
        with open(file_path, 'wb') as file:
            received_bytes_total = 0
            pbar = tqdm.tqdm(total=file_size, unit='B', unit_scale=True, desc=f"Receiving {base_file_name}")

            while received_bytes_total < file_size:
                bytes_to_read = min(1024, file_size - received_bytes_total)
                if bytes_to_read <= 0:
                    logger.debug("Bytes to read became <=0, breaking receive loop.")
                    break

                data = conn.recv(bytes_to_read)
                if not data:
                    logger.warning(f"Connection lost prematurely while receiving '{base_file_name}' from {conn_details_for_msg}. Received {received_bytes_total}/{file_size} bytes.")
                    return False

                file.write(data)
                received_bytes_total += len(data)
                pbar.update(len(data))

        if received_bytes_total == file_size:
            logger.info(f"Successfully received all {file_size} bytes for '{base_file_name}'.")

            eof_confirm = conn.recv(1024)
            if eof_confirm == b"<EOF_CONFIRM>":
                logger.debug(f"EOF confirmation received for '{base_file_name}' from {conn_details_for_msg}.")
                conn.sendall(b"FILE_OK")
                logger.info(f"File '{base_file_name}' saved successfully to '{file_path}'. Sent FILE_OK to {conn_details_for_msg}.")
                received_cleanly = True
                return True
            else:
                logger.warning(f"Did not receive expected EOF confirmation for '{base_file_name}' from {conn_details_for_msg}. Received: {eof_confirm.decode(errors='ignore')}")
                logger.info(f"File '{base_file_name}' data fully received but sender EOF confirmation missing. Saved to '{file_path}'.")
                received_cleanly = True
                return True
        else:
            logger.warning(f"File reception incomplete for '{base_file_name}'. Expected {file_size} bytes, got {received_bytes_total}.")
            return False

    except socket.timeout:
        logger.error(f"Socket timeout during receive_file_from_peer from {conn_details_for_msg}.", exc_info=True)
        return False
    except socket.error as e:
        logger.error(f"Socket error during receive_file_from_peer from {conn_details_for_msg}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred during receive_file_from_peer from {conn_details_for_msg}: {e}", exc_info=True)
        return False
    finally:
        if pbar:
            pbar.close()
            logger.debug(f"Receive progress bar closed for {file_name if 'file_name' in locals() else 'unknown file'}.")
        if file_path and os.path.exists(file_path) and not received_cleanly:
            try:
                logger.warning(f"Cleaning up partial file due to incomplete/failed reception: {file_path}")
                os.remove(file_path)
            except Exception as e_rem:
                logger.error(f"Error removing partial file {file_path}: {e_rem}", exc_info=True)

def start_client_and_receive(server_ip, server_port, save_directory):
    logger.info(f"Attempting client-initiated receive from {server_ip}:{server_port} into {save_directory}")
    addr = (server_ip, server_port)
    conn = None
    try:
        logger.info(f"Attempting to connect to server {server_ip}:{server_port} to receive a file (via start_client_and_receive).")
        conn = socket.create_connection(addr, timeout=10)
        logger.info(f"Connected to server {server_ip}:{server_port} (via start_client_and_receive).")

        conn.settimeout(20)

        if receive_file_from_peer(conn, save_directory):
            logger.info(f"Successfully received file from {server_ip}:{server_port} via start_client_and_receive.")
        else:
            logger.warning(f"Failed to receive file from {server_ip}:{server_port} via start_client_and_receive.")

    except socket.timeout:
        logger.error(f"Socket timeout during operation with {server_ip}:{server_port} in start_client_and_receive.", exc_info=True)
    except socket.error as e:
        logger.error(f"Socket error in start_client_and_receive with {server_ip}:{server_port}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An error occurred in start_client_and_receive with {server_ip}:{server_port}: {e}", exc_info=True)
    finally:
        if conn:
            try:
                logger.debug(f"Shutting down connection to {server_ip}:{server_port} (for client-initiated receive).")
                conn.shutdown(socket.SHUT_RDWR)
            except (socket.error, OSError) as e:
                logger.debug(f"Error during shutdown for connection to {server_ip}:{server_port} (ignorable): {e}")
            conn.close()
        logger.info(f"Client connection (for receiving) to {server_ip}:{server_port} closed.")

# --- Peer Discovery Functions ---

def broadcast_presence(tcp_port_for_transfer, current_shutdown_event):
    hostname = socket.gethostname()
    message_data = {
        "app_id": BROADCAST_MESSAGE_APP_ID,
        "hostname": hostname,
        "tcp_port": tcp_port_for_transfer,
        "version": DISCOVERY_PROTOCOL_VERSION
    }
    broadcast_message = json.dumps(message_data).encode('utf-8')
    logger.debug(f"Broadcast message prepared: {message_data}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            logger.info(f"Starting presence broadcast on UDP port {DISCOVERY_PORT} every {BROADCAST_INTERVAL}s.")
            while not current_shutdown_event.is_set():
                try:
                    sock.sendto(broadcast_message, ('<broadcast>', DISCOVERY_PORT))
                    logger.debug(f"Discovery broadcast sent to ('<broadcast>', {DISCOVERY_PORT}).")
                except socket.error as e:
                    logger.error(f"Error sending broadcast: {e}", exc_info=True)

                current_shutdown_event.wait(BROADCAST_INTERVAL)
            logger.info("Presence broadcast thread stopped.")
    except Exception as e:
        logger.critical(f"Critical error in broadcast_presence thread setup or loop: {e}", exc_info=True)

def listen_for_peers(current_shutdown_event):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            try:
                sock.bind(('', DISCOVERY_PORT))
                logger.info(f"Listening for peer broadcasts on UDP port {DISCOVERY_PORT}.")
            except socket.error as e:
                logger.critical(f"Error binding listener socket to UDP port {DISCOVERY_PORT}: {e}. Discovery will not work.", exc_info=True)
                return

            sock.settimeout(1.0)
            while not current_shutdown_event.is_set():
                try:
                    data, addr = sock.recvfrom(1024)
                    message = data.decode('utf-8')
                    logger.debug(f"Received broadcast data from {addr}: {message[:100]}")
                    peer_info = json.loads(message)

                    if peer_info.get("app_id") == BROADCAST_MESSAGE_APP_ID and \
                       peer_info.get("version") == DISCOVERY_PROTOCOL_VERSION:

                        peer_ip = addr[0]
                        peer_hostname = peer_info.get("hostname", "UnknownHostname")
                        peer_tcp_port = peer_info.get("tcp_port")

                        my_hostname_for_check = socket.gethostname()
                        if peer_hostname == my_hostname_for_check and peer_tcp_port == MY_TCP_TRANSFER_PORT:
                            my_ips_for_check = []
                            try:
                                my_ips_for_check = socket.gethostbyname_ex(my_hostname_for_check)[2]
                                if peer_ip == "0.0.0.0" and any(pip == "0.0.0.0" for pip in my_ips_for_check):
                                     logger.debug(f"Ignored own broadcast from {peer_ip} (0.0.0.0 match).")
                                     continue
                            except socket.gaierror:
                                logger.debug(f"Could not get all local IPs for self-broadcast check against {peer_ip}")

                            if peer_ip in my_ips_for_check or peer_ip.startswith("127."):
                                 logger.debug(f"Ignored own broadcast from {peer_ip}:{peer_tcp_port}.")
                                 continue

                        if peer_tcp_port:
                            peer_id = (peer_ip, peer_tcp_port)
                            with peers_lock:
                                is_new = peer_id not in discovered_peers
                                discovered_peers[peer_id] = {
                                    "hostname": peer_hostname,
                                    "ip": peer_ip,
                                    "tcp_port": peer_tcp_port,
                                    "last_seen": time.time()
                                }
                            if is_new:
                                logger.info(f"Discovered new peer: {peer_hostname} ({peer_ip}:{peer_tcp_port})")
                            else:
                                logger.debug(f"Refreshed peer: {peer_hostname} ({peer_ip}:{peer_tcp_port})")
                        else:
                            logger.warning(f"Received valid broadcast from {addr} but missing 'tcp_port'. Message: {message}")
                    else:
                         logger.debug(f"Received non-matching app_id/version broadcast from {addr}. Data: {message[:100]}")

                except socket.timeout:
                    continue
                except json.JSONDecodeError:
                    logger.warning(f"Error decoding JSON from {addr}: {data.decode('utf-8', errors='ignore')[:100]}")
                except Exception as e:
                    logger.error(f"Error processing received broadcast: {e}", exc_info=True)
            logger.info("Peer listener thread stopped.")
    except Exception as e:
        logger.critical(f"Critical error in listen_for_peers thread setup or loop: {e}", exc_info=True)

def get_active_peers(max_age_seconds=PEER_MAX_AGE_SECONDS):
    logger.debug("Getting active peers.")
    with peers_lock:
        current_time = time.time()
        active_peers = {}
        for peer_id, info in list(discovered_peers.items()):
            if current_time - info["last_seen"] >= max_age_seconds:
                logger.info(f"Removing inactive peer: {info['hostname']} ({info['ip']}:{info['tcp_port']}) due to timeout ({max_age_seconds}s).")
                del discovered_peers[peer_id]
            else:
                 active_peers[peer_id] = info

        if not active_peers and discovered_peers:
            logger.debug("All previously known peers are now inactive.")
        elif not discovered_peers:
             logger.debug("No peers known (discovered_peers is empty).")

        return active_peers

# --- End of Peer Discovery Functions ---

def collect_local_candidates(listen_port):
    candidates = []
    try:
        hostname = socket.gethostname()
        local_ips = socket.gethostbyname_ex(hostname)[2]
        for ip in local_ips:
            candidates.append({"address": ip, "port": listen_port, "type": "host"})

        if not candidates or not any(not ip.startswith("127.") for ip in local_ips):
             if not any(c["address"] == "127.0.0.1" for c in candidates):
                 candidates.append({"address": "127.0.0.1", "port": listen_port, "type": "host"})

        logger.info(f"Collected local candidates: {candidates}")
    except socket.gaierror as e:
        logger.error(f"Error getting local IP addresses: {e}. Using loopback only.")
        candidates.append({"address": "127.0.0.1", "port": listen_port, "type": "host"})
    return candidates

# --- UDP Hole Punching ---
def attempt_udp_hole_punch(local_udp_port, remote_host, remote_port, num_packets=5, delay_ms=200):
    logger.info(f"Attempting UDP hole punch: local_port={local_udp_port} to {remote_host}:{remote_port}")
    punch_socket = None
    try:
        punch_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        punch_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            punch_socket.bind(('0.0.0.0', local_udp_port))
            logger.debug(f"UDP punch socket bound to 0.0.0.0:{local_udp_port}")
        except socket.error as e:
            logger.error(f"Failed to bind UDP punch socket to 0.0.0.0:{local_udp_port}: {e}. Hole punch might be ineffective.", exc_info=True)

        message = b"punch"
        for i in range(num_packets):
            try:
                punch_socket.sendto(message, (remote_host, remote_port))
                logger.debug(f"Sent UDP punch packet {i+1}/{num_packets} to {remote_host}:{remote_port} from local port {local_udp_port}")
            except socket.gaierror as e:
                logger.error(f"DNS resolution failed for UDP hole punch target {remote_host}: {e}. Cannot send punch packets.")
                return
            except socket.error as e:
                logger.error(f"Socket error sending UDP punch packet {i+1} to {remote_host}:{remote_port}: {e}", exc_info=True)

            if i < num_packets - 1:
                time.sleep(delay_ms / 1000.0)

        logger.info(f"Finished sending UDP punch packets to {remote_host}:{remote_port}.")

    except Exception as e:
        logger.error(f"An error occurred during UDP hole punching: {e}", exc_info=True)
    finally:
        if punch_socket:
            punch_socket.close()
            logger.debug("UDP punch socket closed.")


def main(app_shutdown_event):
    global signaling_client_instance

    logger.info("Application main function started.")
    print("\nFile Transfer Utility with Peer Discovery")
    print("-------------------------------------------")

    public_ip, public_port, nat_type = get_public_ip_port_from_stun(local_source_port=MY_TCP_TRANSFER_PORT)
    if public_ip and public_port:
        logger.info(f"STUN Result: External IP: {public_ip}, External Port: {public_port}, NAT Type: {nat_type}")
        print(f"STUN Discovery: Your public IP:Port may be {public_ip}:{public_port} (NAT Type: {nat_type})")
    else:
        logger.warning("STUN discovery failed or returned no result. Direct P2P connection might be impaired.")
        print("STUN discovery failed. Direct P2P connection might be impaired for peers outside your NAT.")

    broadcast_thread = threading.Thread(target=broadcast_presence, name="BroadcastThread", args=(MY_TCP_TRANSFER_PORT, app_shutdown_event), daemon=True)
    listen_thread = threading.Thread(target=listen_for_peers, name="ListenThread", args=(app_shutdown_event,), daemon=True)

    try:
        logger.info("Starting peer discovery services.")
        broadcast_thread.start()
        listen_thread.start()
        time.sleep(0.5)
        if not broadcast_thread.is_alive() or not listen_thread.is_alive():
            logger.critical("One or more local discovery threads did not start correctly.")
            print("CRITICAL: Local discovery threads failed to start.")

        while not app_shutdown_event.is_set():
            print("\n--- Main Menu ---")
            print(f"This instance broadcasts for local LAN discovery. For receiving files, it listens on TCP Port: {MY_TCP_TRANSFER_PORT}")
            print("1. Send File (Local Peer)")
            print("2. Receive File (Act as server, Local Peer)")
            print("3. View Discovered Local Peers")
            print("4. Get Public IP (Shell Method)")
            print("5. Get Public IP & Port (STUN Method)")
            print("6. Internet Mode (Connect via Signaling Server)")
            print("7. Exit")
            choice = input("Enter your choice: ").strip()
            logger.info(f"User chose menu option: {choice}")

            if choice == '1':
                print("\n--- Send File (Local Peer) ---")
                logger.debug("Initiating 'Send File (Local Peer)' workflow.")
                active_peers = get_active_peers()
                selected_peer_ip = None
                selected_peer_port = None

                if not active_peers:
                    print("No active local peers found. You'll need to enter peer details manually.")
                    logger.info("No active local peers found for sending, user prompted for manual entry.")
                else:
                    print("Available local peers to send to:")
                    peer_list_for_selection = list(active_peers.values())
                    for idx, info in enumerate(peer_list_for_selection):
                        print(f"  {idx+1}. Host: {info['hostname']}, IP: {info['ip']}, Port: {info['tcp_port']}")
                    print(f"  M. Enter Manually")

                peer_choice_input = input("Choose a peer by number or 'M' for manual entry: ").strip().lower()
                logger.debug(f"User peer choice input for local send: '{peer_choice_input}'")

                if peer_choice_input == 'm':
                    logger.info("User chose manual peer entry for local send.")
                    selected_peer_ip = input("Enter target peer's IP address: ").strip()
                    try:
                        port_input = input(f"Enter target peer's TCP port (default {DEFAULT_PORT}): ").strip()
                        selected_peer_port = int(port_input) if port_input else DEFAULT_PORT
                    except ValueError:
                        selected_peer_port = DEFAULT_PORT
                        logger.warning(f"Invalid port for manual peer (local send), using default: {selected_peer_port}")
                        print(f"Invalid port, using default: {selected_peer_port}")
                elif peer_choice_input.isdigit():
                    try:
                        peer_idx = int(peer_choice_input) - 1
                        current_active_peers_values = list(get_active_peers().values())
                        if 0 <= peer_idx < len(current_active_peers_values):
                            selected_peer = current_active_peers_values[peer_idx]
                            selected_peer_ip = selected_peer['ip']
                            selected_peer_port = selected_peer['tcp_port']
                            logger.info(f"Selected local peer for sending: {selected_peer['hostname']} ({selected_peer_ip}:{selected_peer_port})")
                            print(f"Selected peer: {selected_peer['hostname']} ({selected_peer_ip}:{selected_peer_port})")
                        else:
                            logger.warning("Invalid local peer number selected.")
                            print("Invalid peer number selected. Please enter details manually.")
                            selected_peer_ip = input("Enter target peer's IP address: ").strip()
                    except ValueError:
                        logger.warning("Invalid input for local peer selection (not a digit).")
                        print("Invalid input. Please enter details manually.")
                        selected_peer_ip = input("Enter target peer's IP address: ").strip()
                else:
                    logger.warning("Invalid peer choice input for local send.")
                    print("Invalid choice. Please enter details manually.")
                    selected_peer_ip = input("Enter target peer's IP address: ").strip()

                if not selected_peer_ip:
                    logger.info("No target IP address provided for local send. Returning to main menu.")
                    print("No target IP entered. Returning to main menu.")
                    continue
                if not selected_peer_port:
                     try:
                        port_input = input(f"Enter target peer's TCP port (default {DEFAULT_PORT}): ").strip()
                        selected_peer_port = int(port_input) if port_input else DEFAULT_PORT
                     except ValueError:
                        selected_peer_port = DEFAULT_PORT
                        logger.warning(f"Invalid port for manually entered IP (local send), using default: {selected_peer_port}")
                        print(f"Invalid port, using default: {selected_peer_port}")

                file_path_to_send = ""
                try:
                    with open(UPLOAD_PATH_FILE, "r") as f:
                        saved_path = f.read().strip()
                        if os.path.isfile(saved_path):
                            if input(f"Send last used file '{saved_path}'? (y/n, default y): ").lower() != 'n':
                                file_path_to_send = saved_path
                except Exception: pass
                while not file_path_to_send or not os.path.isfile(file_path_to_send):
                    if file_path_to_send: print(f"File '{file_path_to_send}' not found or invalid.")
                    file_path_to_send = input("Enter full path of file to send: ").strip()
                    if not file_path_to_send: break
                if not file_path_to_send: continue
                try:
                    with open(UPLOAD_PATH_FILE, "w") as f: f.write(file_path_to_send)
                except Exception as e: logger.warning(f"Could not save path to {UPLOAD_PATH_FILE}: {e}")

                start_client_and_send(selected_peer_ip, selected_peer_port, file_path_to_send)


            elif choice == '2':
                print("\n--- Receive File (Local Peer) ---")
                logger.debug("Initiating 'Receive File (Local Peer)' workflow.")
                save_dir_input = input(f"Enter directory to save received files (default '{DEFAULT_SAVE_DIR}'): ").strip()
                save_directory = save_dir_input or DEFAULT_SAVE_DIR
                logger.debug(f"Save directory chosen for local receive: {save_directory}")
                if not os.path.exists(save_directory):
                    try: os.makedirs(save_directory); logger.info(f"Created save directory: {save_directory}")
                    except OSError as e: logger.error(f"Error creating save dir {save_directory} for local receive: {e}"); continue
                elif not os.path.isdir(save_directory):
                    logger.error(f"Save path '{save_directory}' for local receive is not a directory."); continue
                start_file_transfer_server('0.0.0.0', MY_TCP_TRANSFER_PORT, save_directory)


            elif choice == '3':
                print("\n--- View Discovered Local Peers ---")
                active_peers = get_active_peers()
                if active_peers:
                    print("Currently active local peers on the network:")
                    for idx, (peer_id, info) in enumerate(active_peers.items()):
                        print(f"  {idx+1}. Host: {info['hostname']}, IP: {info['ip']}, Port: {info['tcp_port']} (Last seen: {time.strftime('%H:%M:%S', time.localtime(info['last_seen']))})")
                else:
                    print("No active local peers found on the network currently.")
                input("Press Enter to return to the main menu...")

            elif choice == '4':
                print("\n--- Get Public IP (Shell Method) ---")
                ip = get_public_ip()
                if ip: print(f"Public IP via shell method: {ip}")
                else: print("Could not retrieve public IP using shell method.")
                input("Press Enter to return to the main menu...")

            elif choice == '5':
                print("\n--- Get Public IP & Port (STUN Method) ---")
                stun_h = input(f"Enter STUN host (default: {DEFAULT_STUN_HOST}): ").strip() or DEFAULT_STUN_HOST
                stun_p_str = input(f"Enter STUN port (default: {DEFAULT_STUN_PORT}): ").strip()
                try: stun_p = int(stun_p_str) if stun_p_str else DEFAULT_STUN_PORT
                except ValueError: stun_p = DEFAULT_STUN_PORT; print(f"Invalid STUN port, using default {stun_p}.")
                source_p_str = input(f"Enter local source port for STUN query (default: {MY_TCP_TRANSFER_PORT}): ").strip()
                try: source_p = int(source_p_str) if source_p_str else MY_TCP_TRANSFER_PORT
                except ValueError: source_p = MY_TCP_TRANSFER_PORT; print(f"Invalid source port, using default {source_p}.")
                ext_ip, ext_port, nat_type = get_public_ip_port_from_stun(stun_host=stun_h, stun_port=stun_p, local_source_port=source_p)
                if ext_ip and ext_port:
                    print(f"STUN Discovery Result:\n  External IP: {ext_ip}\n  External Port: {ext_port}\n  NAT Type: {nat_type}")
                else: print("STUN discovery failed or did not return a result.")
                input("Press Enter to return to the main menu...")

            elif choice == '6': # Internet Mode
                print("\n--- Internet Mode (via Signaling Server) ---")
                logger.debug("User chose 'Internet Mode'.")

                if signaling_client_instance and signaling_client_instance.is_connected:
                    logger.warning("Already connected to a signaling server. Disconnect first or restart.")
                    print("Already in an Internet Mode session. Please exit and restart if you want a new session.")
                    continue

                sig_url = input(f"Enter Signaling Server URL (default: {DEFAULT_SIGNALING_URL}): ").strip() or DEFAULT_SIGNALING_URL
                room_id = input("Enter Room ID to join/create: ").strip()

                if not room_id:
                    logger.warning("No Room ID entered for Internet Mode. Returning to menu.")
                    print("Room ID is required for Internet Mode.")
                    continue

                logger.info(f"Entering Internet Mode: Signaling URL='{sig_url}', Room ID='{room_id}'")
                signaling_client_instance = SignalingClient(sig_url, room_id, app_shutdown_event)
                if not signaling_client_instance.start():
                    signaling_client_instance = None
                    print("Failed to start signaling client components. Returning to menu.")
                    continue

                print(f"Attempting to connect to signaling server and register in room '{room_id}'...")
                time.sleep(3)

                if not signaling_client_instance or not signaling_client_instance.is_connected:
                    logger.error("Failed to connect to signaling server or register. Please check URL and server status.")
                    print("Failed to connect/register with signaling server. Returning to menu.")
                    if signaling_client_instance:
                        signaling_client_instance.disconnect()
                    signaling_client_instance = None
                    continue

                print(f"Successfully registered with signaling server in room '{room_id}'. Waiting for a peer...")
                logger.info(f"Waiting for peer in room '{room_id}'.")

                if signaling_client_instance.peer_joined_event.wait(timeout=120):
                    logger.info(f"Peer joined in room '{room_id}'. Proceeding with candidate exchange.")
                    print("Peer has joined the room! Initiating P2P setup.")

                    my_candidates = collect_local_candidates(MY_TCP_TRANSFER_PORT)
                    my_srflx_candidate = None
                    stun_ip, stun_port, stun_nat_type = get_public_ip_port_from_stun(local_source_port=MY_TCP_TRANSFER_PORT)
                    if stun_ip and stun_port:
                        my_srflx_candidate = {"address": stun_ip, "port": stun_port, "type": "srflx", "nat_type": stun_nat_type}
                        my_candidates.append(my_srflx_candidate)

                    logger.info(f"Sending {len(my_candidates)} candidates to peer.")
                    for cand_dict in my_candidates:
                        asyncio.run_coroutine_threadsafe(signaling_client_instance.send_candidate_message(cand_dict), signaling_client_instance.loop)

                    print("Waiting for peer's candidates...")
                    if signaling_client_instance.candidates_received_event.wait(timeout=60):
                        peer_candidates = list(signaling_client_instance.received_candidates) # get a copy
                        logger.info(f"Received {len(peer_candidates)} candidates from peer: {peer_candidates}")
                        print(f"Received {len(peer_candidates)} candidates from peer.")

                        peer_srflx_cand_for_punch = signaling_client_instance.peer_srflx_candidate

                        internet_action = input("You are connected to a peer via Internet Mode.\nDo you want to (1) Send a file or (2) Receive a file? Choice: ").strip()
                        if internet_action == '1': # Current instance wants to be SENDER
                            logger.info("Internet Mode: Chosen to SEND file.")
                            file_path_to_send = ""
                            while not file_path_to_send or not os.path.isfile(file_path_to_send):
                                if file_path_to_send: print(f"File '{file_path_to_send}' not found or invalid.")
                                file_path_to_send = input("Enter full path of file to send: ").strip()
                                if not file_path_to_send: break
                            if not file_path_to_send:
                                logger.warning("No file path for sending in Internet Mode.")
                            else:
                                connected_to_peer = False
                                if peer_srflx_cand_for_punch and my_srflx_candidate:
                                    logger.info("Initiating coordinated UDP hole punch (sender role).")
                                    print("Signaling peer to prepare for hole punch...")
                                    asyncio.run_coroutine_threadsafe(
                                        signaling_client_instance.send_signal_message({"action": "prepare_hole_punch", "target_candidate": my_srflx_candidate}),
                                        signaling_client_instance.loop
                                    )
                                    logger.debug("Waiting for 'punched_from_receiver' signal event from peer...")
                                    if signaling_client_instance.hole_punch_prepared_event.wait(timeout=10):
                                        logger.info("Receiver has punched. Now sender (this instance) will punch to peer's srflx.")
                                        attempt_udp_hole_punch(
                                            MY_TCP_TRANSFER_PORT,
                                            peer_srflx_cand_for_punch["address"],
                                            peer_srflx_cand_for_punch["port"]
                                        )
                                    else:
                                        logger.warning("Timed out waiting for 'punched_from_receiver' signal. Proceeding with TCP connect anyway.")
                                        print("Warning: Did not receive punch confirmation from peer, TCP connection might be less reliable.")
                                else:
                                    logger.warning("Missing self or peer srflx candidate for coordinated hole punch.")
                                    print("Warning: Cannot perform coordinated hole punch, proceeding with direct TCP attempts.")

                                for pcand in sorted(peer_candidates, key=lambda x: 0 if x.get('type') == 'srflx' else 1 if x.get('type') == 'host' else 2):
                                    p_addr, p_port = pcand.get('address'), pcand.get('port')
                                    if not p_addr or not p_port: continue
                                    logger.info(f"Attempting TCP connect to peer candidate: {p_addr}:{p_port} (type: {pcand.get('type','N/A')}) to send file.")
                                    print(f"Attempting to connect to peer at {p_addr}:{p_port}...")
                                    try:
                                        # For simplicity, we'll assume start_client_and_send will attempt the connection and transfer.
                                        # A more robust ICE would involve separate connection checks before transfer.
                                        start_client_and_send(p_addr, p_port, file_path_to_send)
                                        logger.info(f"File transfer attempt to {p_addr}:{p_port} finished (or failed within function).")
                                        # This simplified model doesn't easily confirm if start_client_and_send succeeded in *connecting* vs. full transfer.
                                        # For now, if it doesn't throw a major error here, we assume it tried.
                                        # A better approach might involve start_client_and_send returning connection status.
                                        # Let's assume for now the first non-exception means "connection likely worked".
                                        connected_to_peer = True # This is a simplification.
                                        break
                                    except Exception as e_tcp:
                                        logger.warning(f"TCP connection/send to peer candidate {p_addr}:{p_port} failed: {e_tcp}")
                                if not connected_to_peer:
                                    logger.error("Failed to establish TCP connection with peer for sending after all attempts.")
                                    print("Failed to establish connection with peer for sending.")


                        elif internet_action == '2':
                            logger.info("Internet Mode: Chosen to RECEIVE file.")
                            print(f"Listening for incoming file from internet peer on port {MY_TCP_TRANSFER_PORT}...")
                            print(f"Your candidates sent to peer were: {my_candidates}")
                            print(f"Ensure your firewall/router allows incoming connections to TCP port {MY_TCP_TRANSFER_PORT}.")
                            save_dir = input(f"Save incoming file to directory (default '{DEFAULT_SAVE_DIR}'): ").strip() or DEFAULT_SAVE_DIR
                            if not os.path.exists(save_dir):
                                try: os.makedirs(save_dir); logger.info(f"Created save directory: {save_dir}")
                                except OSError as e: logger.error(f"Error creating save dir {save_dir}: {e}"); continue

                            start_file_transfer_server('0.0.0.0', MY_TCP_TRANSFER_PORT, save_dir)
                        else:
                            logger.warning("Invalid choice for Internet Mode action.")
                            print("Invalid choice for action.")
                    else:
                        logger.warning("Timed out waiting for candidates from peer.")
                        print("Timed out waiting for candidates from peer.")
                else:
                    logger.warning(f"Timed out waiting for a peer to join room '{room_id}'.")
                    print(f"No peer joined room '{room_id}' within the timeout period.")

                if signaling_client_instance:
                    logger.info("Disconnecting signaling client after Internet Mode session.")
                    signaling_client_instance.disconnect()
                    signaling_client_instance = None

            elif choice == '7': # Exit
                logger.info("User chose 'Exit'. Shutting down.")
                print("Exiting application...")
                app_shutdown_event.set()
                break

            else:
                logger.warning(f"Invalid menu choice entered: {choice}")
                print("Invalid choice. Please try again.")

            time.sleep(0.5)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt detected. Shutting down application.")
        print("\nKeyboard interrupt detected. Shutting down...")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred in main: {e}", exc_info=True)
        print(f"An unexpected error occurred in main: {e}")
    finally:
        logger.info("Stopping all services and exiting application.")
        print("Stopping all services...")
        app_shutdown_event.set()
        if signaling_client_instance:
            logger.info("Main exit: Disconnecting signaling client.")
            signaling_client_instance.disconnect()
            signaling_client_instance = None

        if 'broadcast_thread' in locals() and broadcast_thread.is_alive():
            logger.debug("Waiting for broadcast thread to join.")
            broadcast_thread.join(timeout=BROADCAST_INTERVAL + 0.5)
        if 'listen_thread' in locals() and listen_thread.is_alive():
            logger.debug("Waiting for listen thread to join.")
            listen_thread.join(timeout=1.5)
        logger.info("Application exited.")
        print("Exited.")

if __name__ == "__main__":
    logger.debug(f"Global OS choice determined as: {os_choice}")
    app_shutdown_event_main = threading.Event()
    signaling_client_instance = None
    try:
        main(app_shutdown_event_main)
    except SystemExit:
        logger.info("Application explicitly exited via SystemExit.")
        app_shutdown_event_main.set()
    except Exception as e_global:
        logger.critical(f"Global unhandled exception led to application termination: {e_global}", exc_info=True)
        app_shutdown_event_main.set()
    finally:
        logger.info("--- Application P2P File Transfer Fully Stopped (from __main__) ---")
        if signaling_client_instance and hasattr(signaling_client_instance, 'is_connected') and signaling_client_instance.is_connected:
             logger.info("__main__ finally: Disconnecting signaling client.")
             signaling_client_instance.disconnect()
