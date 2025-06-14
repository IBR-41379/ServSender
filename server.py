import os
import socket
import subprocess
import json
import time
import threading
import logging

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
# --- End of Logging Configuration ---

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
                # Log the specific invalid content before re-prompting
                logger.warning(f"Os_Choice.txt found with invalid content: '{choice}'. Re-prompting user.")
                # print(f"Invalid choice '{choice}' found in {OS_CHOICE_FILE}. Please re-enter.") # Covered by logger
    except FileNotFoundError:
        logger.info(f"{OS_CHOICE_FILE} not found. Prompting user for OS choice.")
        # print(f"{OS_CHOICE_FILE} not found.") # Covered by logger
    except IOError as e: # More specific exception for I/O issues
        logger.error(f"IOError reading {OS_CHOICE_FILE}: {e}", exc_info=True)
        # print(f"Error reading {OS_CHOICE_FILE}: {e}") # Covered by logger
    except Exception as e: # Catch any other unexpected error during read
        logger.error(f"Unexpected error reading {OS_CHOICE_FILE}: {e}", exc_info=True)
        # print(f"Error reading {OS_CHOICE_FILE}: {e}") # Covered by logger


    # This loop is entered if file read fails, content is invalid, or file not found.
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
        # print(f"OS choice '{os_choice_val_local}' saved to {OS_CHOICE_FILE}") # Covered by logger
    except IOError as e: # More specific exception for I/O issues
        logger.error(f"IOError writing OS choice to {OS_CHOICE_FILE}: {e}", exc_info=True)
        # print(f"Error writing to {OS_CHOICE_FILE}: {e}") # Covered by logger
    except Exception as e: # Catch any other unexpected error during write
        logger.error(f"Unexpected error writing OS choice to {OS_CHOICE_FILE}: {e}", exc_info=True)
        # print(f"Error writing to {OS_CHOICE_FILE}: {e}") # Covered by logger
    return os_choice_val_local

os_choice = get_os_choice() # This now uses the refined function
# logger.info(f"Application running with OS choice: {os_choice}") # Logged at the end of get_os_choice or upon use

def check_and_install_tqdm():
    logger.debug("Checking for tqdm dependency.")
    try:
        import tqdm
        logger.debug("tqdm is already installed.")
        return
    except ModuleNotFoundError:
        logger.info("tqdm not found. Attempting to install...")
        try:
            subprocess.check_call(['pip', 'install', 'tqdm'])
            logger.info("tqdm installed successfully (or was already present).")
        except subprocess.CalledProcessError as e:
            logger.critical(f"Failed to install tqdm using pip: {e}. Please install it manually.", exc_info=True)
            print(f"Failed to install tqdm: {e}\nPlease install tqdm manually: pip install tqdm")
            exit(1)
        except Exception as e:
            logger.critical(f"An error occurred while trying to install tqdm: {e}. Please ensure pip is available and install tqdm manually.", exc_info=True)
            print(f"An error occurred while trying to check/install tqdm: {e}\nPlease ensure pip is installed and tqdm can be installed.")
            exit(1)

check_and_install_tqdm()
import tqdm

# Constants for default values
DEFAULT_PORT = 6968
DEFAULT_SAVE_DIR = "received_files"

# Constants for Peer Discovery
DISCOVERY_PORT = 6969
BROADCAST_MESSAGE_APP_ID = "ServSenderP2P_Discovery_v1"
BROADCAST_INTERVAL = 5
DISCOVERY_PROTOCOL_VERSION = 1
PEER_MAX_AGE_SECONDS = 30

MY_TCP_TRANSFER_PORT = DEFAULT_PORT

discovered_peers = {}
peers_lock = threading.Lock()
shutdown_event = threading.Event()


def get_public_ip():
    logger.debug("Attempting to retrieve public IP address.")
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
            logger.warning("Failed to retrieve IP address: command output was empty.")
            return None

        if not (all(c.isdigit() or c == '.' for c in ip_address) and ip_address.count('.') == 3 and \
                all(0 <= int(num) <= 255 for num in ip_address.split('.')) and len(ip_address.split('.')) == 4):
             logger.warning(f"Retrieved IP '{ip_address}' may not be a standard IPv4 format.")
        else:
            logger.info(f"Public IP address successfully retrieved: {ip_address}")
        return ip_address

    except FileNotFoundError as e:
        logger.error(f"IP retrieval command not found ({e.filename}). Ensure curl/powershell is installed and in PATH.")
        return None
    except subprocess.CalledProcessError as e:
        stderr_output = e.stderr.strip() if e.stderr else "N/A"
        logger.error(f"IP retrieval command failed with exit code {e.returncode}. Stderr: {stderr_output}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred while retrieving IP: {e}", exc_info=True)
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

def broadcast_presence(tcp_port_for_transfer, shutdown_event_param):
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
            while not shutdown_event_param.is_set():
                try:
                    sock.sendto(broadcast_message, ('<broadcast>', DISCOVERY_PORT))
                    logger.debug(f"Discovery broadcast sent to ('<broadcast>', {DISCOVERY_PORT}).")
                except socket.error as e:
                    logger.error(f"Error sending broadcast: {e}", exc_info=True)

                shutdown_event_param.wait(BROADCAST_INTERVAL)
            logger.info("Presence broadcast thread stopped.")
    except Exception as e:
        logger.critical(f"Critical error in broadcast_presence thread setup or loop: {e}", exc_info=True)

def listen_for_peers(shutdown_event_param):
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
            while not shutdown_event_param.is_set():
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

def main():
    global shutdown_event
    logger.info("Application main function started.")

    print("\nFile Transfer Utility with Peer Discovery")
    print("-------------------------------------------")

    broadcast_thread = threading.Thread(target=broadcast_presence, name="BroadcastThread", args=(MY_TCP_TRANSFER_PORT, shutdown_event), daemon=True)
    listen_thread = threading.Thread(target=listen_for_peers, name="ListenThread", args=(shutdown_event,), daemon=True)

    try:
        logger.info("Starting peer discovery services.")
        broadcast_thread.start()
        listen_thread.start()
        time.sleep(0.5)
        if not broadcast_thread.is_alive() or not listen_thread.is_alive():
            logger.critical("One or more discovery threads did not start correctly. Discovery will not work.")
            print("CRITICAL: Discovery threads failed to start. The application may not function correctly.")

        while not shutdown_event.is_set():
            print("\n--- Main Menu ---")
            print(f"This instance is broadcasting its availability for receiving files on TCP Port: {MY_TCP_TRANSFER_PORT}")
            print("1. Send File")
            print("2. Receive File (Act as server)")
            print("3. View Discovered Peers")
            print("4. Exit")
            choice = input("Enter your choice: ").strip()
            logger.info(f"User chose menu option: {choice}")

            if choice == '1':
                print("\n--- Send File ---")
                logger.debug("Initiating 'Send File' workflow.")
                active_peers = get_active_peers()
                selected_peer_ip = None
                selected_peer_port = None

                if not active_peers:
                    print("No active peers found. You'll need to enter peer details manually.")
                    logger.info("No active peers found for sending, user prompted for manual entry.")
                else:
                    print("Available peers to send to:")
                    peer_list_for_selection = list(active_peers.values())
                    for idx, info in enumerate(peer_list_for_selection):
                        print(f"  {idx+1}. Host: {info['hostname']}, IP: {info['ip']}, Port: {info['tcp_port']}")
                    print(f"  M. Enter Manually")

                peer_choice_input = input("Choose a peer by number or 'M' for manual entry: ").strip().lower()
                logger.debug(f"User peer choice input: '{peer_choice_input}'")

                if peer_choice_input == 'm':
                    logger.info("User chose manual peer entry.")
                    selected_peer_ip = input("Enter target peer's IP address: ").strip()
                    try:
                        port_input = input(f"Enter target peer's TCP port (default {DEFAULT_PORT}): ").strip()
                        selected_peer_port = int(port_input) if port_input else DEFAULT_PORT
                    except ValueError:
                        selected_peer_port = DEFAULT_PORT
                        logger.warning(f"Invalid port entered for manual peer, using default: {selected_peer_port}")
                        print(f"Invalid port, using default: {selected_peer_port}")
                elif peer_choice_input.isdigit():
                    try:
                        peer_idx = int(peer_choice_input) - 1
                        current_active_peers_values = list(get_active_peers().values())
                        if 0 <= peer_idx < len(current_active_peers_values):
                            selected_peer = current_active_peers_values[peer_idx]
                            selected_peer_ip = selected_peer['ip']
                            selected_peer_port = selected_peer['tcp_port']
                            logger.info(f"Selected peer for sending: {selected_peer['hostname']} ({selected_peer_ip}:{selected_peer_port})")
                            print(f"Selected peer: {selected_peer['hostname']} ({selected_peer_ip}:{selected_peer_port})")
                        else:
                            logger.warning("Invalid peer number selected from potentially outdated list.")
                            print("Invalid peer number selected. Please enter details manually.")
                            selected_peer_ip = input("Enter target peer's IP address: ").strip()
                    except ValueError:
                        logger.warning("Invalid input for peer selection (not a digit).")
                        print("Invalid input. Please enter details manually.")
                        selected_peer_ip = input("Enter target peer's IP address: ").strip()
                else:
                    logger.warning("Invalid peer choice input.")
                    print("Invalid choice. Please enter details manually.")
                    selected_peer_ip = input("Enter target peer's IP address: ").strip()

                if not selected_peer_ip:
                    logger.info("No target IP address provided. Returning to main menu.")
                    print("No target IP entered. Returning to main menu.")
                    continue
                if not selected_peer_port:
                     try:
                        port_input = input(f"Enter target peer's TCP port (default {DEFAULT_PORT}): ").strip()
                        selected_peer_port = int(port_input) if port_input else DEFAULT_PORT
                     except ValueError:
                        selected_peer_port = DEFAULT_PORT
                        logger.warning(f"Invalid port for manually entered IP, using default: {selected_peer_port}")
                        print(f"Invalid port, using default: {selected_peer_port}")

                logger.debug(f"Target for sending: IP={selected_peer_ip}, Port={selected_peer_port}")
                file_path_to_send = ""
                try:
                    with open(UPLOAD_PATH_FILE, "r") as f:
                        saved_path = f.read().strip()
                        logger.debug(f"Read path from {UPLOAD_PATH_FILE}: '{saved_path}'")
                        if not saved_path:
                            logger.debug(f"{UPLOAD_PATH_FILE} was empty.")
                        # Check if the path from file is valid before asking the user
                        elif os.path.isfile(saved_path): # Check if it's a file first
                            logger.info(f"Valid last used file path found in {UPLOAD_PATH_FILE}: {saved_path}")
                            if input(f"Send last used file '{saved_path}'? (y/n, default y): ").lower() != 'n':
                                file_path_to_send = saved_path
                                logger.info(f"User chose to send last used file: {file_path_to_send}")
                        elif os.path.exists(saved_path): # It exists, but not a file (e.g. directory)
                             logger.warning(f"Path from {UPLOAD_PATH_FILE} ('{saved_path}') exists but is not a file. Please enter path manually.")
                             print(f"Info: Last used path '{saved_path}' is a directory, not a file.")
                        else: # Path does not exist
                            logger.warning(f"Last used path from {UPLOAD_PATH_FILE} ('{saved_path}') is no longer valid/found. Please enter a new path.")
                            print(f"Info: Last used path '{saved_path}' is no longer valid.")
                except FileNotFoundError:
                    logger.info(f"{UPLOAD_PATH_FILE} not found. User will be prompted for path.")
                    # print(f"{UPLOAD_PATH_FILE} not found. Please enter the path manually.") # Covered by logger
                except IOError as e: # Catch specific I/O errors for reading
                    logger.error(f"IOError reading {UPLOAD_PATH_FILE}: {e}", exc_info=True)
                    print(f"Error reading saved path file: {e}")
                except Exception as e: # Catch any other unexpected errors
                    logger.error(f"Unexpected error reading {UPLOAD_PATH_FILE}: {e}", exc_info=True)
                    print(f"An unexpected error occurred while reading the saved path: {e}")


                # Loop to get a valid file path if not set from Uppath.txt or if that path was invalid
                while not file_path_to_send or not os.path.isfile(file_path_to_send):
                    if file_path_to_send: # Means the previous attempt (from Uppath.txt or input) was not a valid file
                        logger.warning(f"The path '{file_path_to_send}' is not a valid file.")
                        print(f"File '{file_path_to_send}' not found or is not a valid file.")

                    file_path_to_send = input("Enter the full path of the file you want to send: ").strip()
                    logger.debug(f"User entered file path: '{file_path_to_send}'")
                    if not file_path_to_send:
                        logger.info("No file path entered by user. Returning to main menu.")
                        print("No file path entered. Returning to main menu.")
                        break # Break from while loop
                if not file_path_to_send: continue # Continue to next iteration of main menu loop if no path

                # Save the valid path to Uppath.txt
                try:
                    with open(UPLOAD_PATH_FILE, "w") as f:
                        f.write(file_path_to_send)
                    logger.info(f"Saved current send file path to {UPLOAD_PATH_FILE}: {file_path_to_send}")
                except IOError as e: # Catch specific I/O errors for writing
                    logger.warning(f"IOError: Could not save path to {UPLOAD_PATH_FILE}: {e}", exc_info=True)
                    print(f"Warning: Could not save file path for next session: {e}")
                except Exception as e: # Catch any other unexpected errors
                     logger.warning(f"Unexpected error saving path to {UPLOAD_PATH_FILE}: {e}", exc_info=True)
                     print(f"Warning: Could not save file path for next session: {e}")

                start_client_and_send(selected_peer_ip, selected_peer_port, file_path_to_send)

            elif choice == '2':
                print("\n--- Receive File ---")
                logger.debug("Initiating 'Receive File' workflow.")
                listen_ip = '0.0.0.0'

                save_dir_input = input(f"Enter directory to save received files (default '{DEFAULT_SAVE_DIR}'): ").strip()
                save_directory = save_dir_input or DEFAULT_SAVE_DIR
                logger.debug(f"Save directory chosen: {save_directory}")

                if not os.path.exists(save_directory):
                    try:
                        os.makedirs(save_directory)
                        logger.info(f"Created save directory: {save_directory}")
                        print(f"Created directory: {save_directory}")
                    except OSError as e:
                        logger.error(f"Error creating save directory '{save_directory}': {e}. Returning to menu.", exc_info=True)
                        print(f"Error creating save directory '{save_directory}': {e}. Returning to menu.")
                        continue
                elif not os.path.isdir(save_directory):
                    logger.error(f"Save path '{save_directory}' is not a directory. Returning to menu.")
                    print(f"Error: Save path '{save_directory}' is not a directory. Returning to menu.")
                    continue

                start_file_transfer_server(listen_ip, MY_TCP_TRANSFER_PORT, save_directory)

            elif choice == '3':
                print("\n--- View Discovered Peers ---")
                logger.debug("User chose 'View Discovered Peers'.")
                active_peers = get_active_peers()
                if active_peers:
                    print("Currently active peers on the network:")
                    for idx, (peer_id, info) in enumerate(active_peers.items()):
                        print(f"  {idx+1}. Host: {info['hostname']}, IP: {info['ip']}, Port: {info['tcp_port']} (Last seen: {time.strftime('%H:%M:%S', time.localtime(info['last_seen']))})")
                else:
                    print("No active peers found on the network currently.")
                input("Press Enter to return to the main menu...")


            elif choice == '4':
                logger.info("User chose 'Exit'. Shutting down.")
                print("Exiting application...")
                shutdown_event.set()
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
        logger.info("Stopping discovery services and exiting application.")
        print("Stopping discovery services...")
        shutdown_event.set()
        if broadcast_thread.is_alive():
            logger.debug("Waiting for broadcast thread to join.")
            broadcast_thread.join(timeout=BROADCAST_INTERVAL + 0.5)
        if listen_thread.is_alive():
            logger.debug("Waiting for listen thread to join.")
            listen_thread.join(timeout=1.5)
        logger.info("Application exited.")
        print("Exited.")

if __name__ == "__main__":
    logger.debug(f"Global OS choice determined as: {os_choice}")
    main()
