import os
import socket

if os.path.exists('Os_Choice.txt'):
    file = open('Os_Choice.txt', 'r')
    os_choice = file.read(3)
else:
    print("Enter which operatng system you are running on:")
    print("1.Windows")
    print("2.Linux")
    os_choice = input("Enter your choice:")
    file = open('Os_Choice.txt', 'w')
    if os_choice == '1':
        file.write('win')
    elif os_choice == '2':
        file.write('lin')
    else:
        print("Wrong choice entered")
        exit()
    file.close()


def dependency():
    x = ""
    if os_choice == 'win':
        x = os.popen('pip list | findstr tqdm').read()
    elif os_choice == 'lin':
        x = os.popen('pip list | grep tqdm').read()
    if x == '':
        os.system('pip install tqdm')


try:
    import tqdm
except ModuleNotFoundError:
    dependency()
    import tqdm


def send(path):
    file = open(path, 'rb')
    file_size = os.path.getsize(path)
    PORT = 6968
    SERVER = ''  # socket.gethostbyname(socket.gethostname())
    if os_choice == 'win':
        SERVER = (
            os.popen(
                'powershell -Command (Invoke-WebRequest -uri "http://ifconfig.me/ip").Content.Trim()'
            )
            .read()
            .strip()
    )
    elif os_choice == 'lin':
        SERVER = os.popen('curl ifconfig.me/ip')
    print(SERVER)
    ADDR = (SERVER, PORT)
    s_skfd = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s_skfd.bind(ADDR)
    s_skfd.listen()
    print(f"IP: {ADDR[0]}")
    print(f"PORT: {ADDR[1]}")
    print(f"[LISTENING] Server is listening\n")
    conn, addr = s_skfd.accept()
    print(f"{addr} has connected.")
    conn.send(("received"+os.path.basename(path)).encode())
    conn.send(str(file_size).encode())
    pbar = tqdm.tqdm(total=file_size, unit='B', unit_scale=True, desc=path)
    while True:
        data = file.read()
        if not data:
            break
        conn.sendall(data)
        pbar.update(len(data))
    pbar.close()
    conn.send(b"<END>")
    file.close()
    conn.close()
    s_skfd.close()
    print("\nFile sent successfully")


def receive():
    SERVER = input("Enter server IP: ")
    PORT = int(input("Enter server PORT: "))
    ADDR = (SERVER, PORT)
    c_skfd = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    c_skfd.connect(ADDR)

    file_name = c_skfd.recv(1024).decode()
    file_size = c_skfd.recv(1024).decode()
    file = open(file_name, 'ab')
    file_bytes = b""
    done = False
    pbar = tqdm.tqdm(total=int(file_size), unit='B',
                     unit_scale=True, desc=file_name)
    while not done:
        data = c_skfd.recv(1024)
        if file_bytes[-5:] == b"<END>":
            done = True
        else:
            file_bytes = b""
            file_bytes += data
            file.write(file_bytes)
        pbar.update(len(data))
    file.close()
    c_skfd.close()
    print("File received successfully")
    pbar.close()


def main():
    dependency()
    print("Do you want to send or receive files?")
    print("1.Send")
    print("2.Receive")
    choice = input("Enter your choice:")
    if choice == '1':
        if os.path.exists("Uppath.txt"):
            file = open("Uppath.txt", "r")
            path = file.read()
            ola = os.listdir(path)[0]
            path = path+ola
            file.close()
        else:
            path = input(
                "Enter the path of the file you want to send(Will be your default):")
            # file=open("Uppath.txt","w")
            # file.write(path)
            # file.close()
        send(path)
    elif choice == '2':
        receive()
    else:
        print("Wrong choice entered")
        exit()


main()
