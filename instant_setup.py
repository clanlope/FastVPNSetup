"""
Windows/Android: https://github.com/clash-download/Clash
IOS: shadowrocket: https://apps.apple.com/app/id932747118
"""

# First time: get an API key from https://vultr.com/ and replace the value of API_KEY below.
API_KEY = ""

# region Functions
server_info = "server_info.txt"
yaml_file = "config.yaml"
qr_file = "ss_qr.png"

from datetime import datetime, timezone
import base64
import json
import re
import time

import paramiko
import requests
import yaml
import qrcode

# Vultr API functions


def _list_instances():
    url = "https://api.vultr.com/v2/instances"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(url, headers=headers)
    return response.json()


def _deploy_instance(region, plan, os_id, label):
    url = "https://api.vultr.com/v2/instances"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {
        "region": region,
        "plan": plan,
        "os_id": os_id,
        "label": label,
        "enable_ipv6": False,
    }
    response = requests.post(url, json=data, headers=headers).json()
    password = response["instance"]["default_password"]
    ins_id = response["instance"]["id"]

    with open(server_info, "w", encoding="utf-8") as f:
        f.write(f"id: {ins_id}\n")
        f.write(f"user: root\n")
        f.write(f"password: {password}\n")

    return password, ins_id, json.dumps(response, indent=2)


def _get_instance(instance_id: str):
    url = f"https://api.vultr.com/v2/instances/{instance_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}

    r = requests.get(url, headers=headers)
    return r.json() if r.status_code == 200 else None


def _reboot_instance(instance_id: str):
    url = f"https://api.vultr.com/v2/instances/{instance_id}/reboot"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.post(url, headers=headers)
    if response.status_code == 204:
        print(f"Instance {instance_id} rebooted successfully.")
    else:
        print(
            f"Failed to reboot instance {instance_id}: {response.status_code}, {response.text}"
        )


def _destroy_instance(instance_id: str):
    url = f"https://api.vultr.com/v2/instances/{instance_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    r = requests.delete(url, headers=headers)

    if r.status_code == 204:
        return {"status": "success", "message": f"Instance {instance_id} destroyed."}
    else:
        print(f"Destroy instance failed: {r.status_code}, {r.text}")


def _wait_ip_ready(instance_id: str):
    while True:
        info = _get_instance(instance_id)
        status = info["instance"]["status"]
        server_status = info["instance"]["server_status"]
        print(f"Instance status: {status} - Server status: {server_status}")

        if status == "active" and server_status == "ok":
            ip = info["instance"]["main_ip"]
            print(f"Instance is active now. Host IP Address: {ip}")
            with open(server_info, "a", encoding="utf-8") as f:
                f.write(f"ip: {ip}\n")

            return ip

        time.sleep(10)


# SSH and config generation functions


def _run_cmd(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd)

    exit_status = stdout.channel.recv_exit_status()

    out = stdout.read().decode()
    err = stderr.read().decode()

    return exit_status, out, err


def _ssh_connect(address, password, instance_id):
    attempt, attempt_max = 0, 3
    while True:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(address, username="root", password=password)
            print("SSH connection established.")
            break

        except Exception as e:
            attempt += 1
            print(
                f"SSH connection failed: {e}. Retrying in 20 seconds... ({attempt}/{attempt_max})"
            )

            if attempt >= attempt_max:
                print(f"SSH failed {attempt_max} times. Rebooting instance...")
                _reboot_instance(instance_id)
                attempt = 0

            time.sleep(20)

    _run_cmd(
        client,
        "bash <(wget -qO- -o- https://github.com/233boy/sing-box/raw/main/install.sh)",
    )
    _run_cmd(client, "sb bbr")
    _, output, _ = _run_cmd(client, "sb add ss")
    print(output.split("------------- 链接 (URL) ------------")[0])
    my_ss_key = re.search(r"ss://[^\s]+", output).group(0)
    port = re.search(r":(\d{4,5})", output).group(1)
    print(f"Opening firewall port {port}...")
    _run_cmd(client, f"ufw allow {port}")

    _, output, _ = _run_cmd(client, "ufw status numbered")
    if str(port) not in output:
        print(f"Failed to open port {port} in firewall. Please check.")
    client.close()
    print("SSH connection closed.")
    return my_ss_key


def _get_local_server_info():
    try:
        info = {}

        with open(server_info, "r", encoding="utf-8") as f:
            for line in f:
                key, value = line.strip().split(":", 1)
                info[key] = value.strip()

        if len(info) == 0 or not info:
            print("No valid server info found. Please deploy an instance first.")
            return

    except FileNotFoundError:
        print(f"{server_info} not found.")
        return

    return info


def _create_yaml(ss_url: str):
    body = ss_url[5:]
    if "#" in body:
        body, name = body.split("#", 1)
    else:
        name = "MySS"

    b64_part, server_part = body.split("@")
    decoded = base64.urlsafe_b64decode(b64_part + "=" * (-len(b64_part) % 4)).decode()
    cipher, password = decoded.split(":")
    server, port = server_part.split(":")

    config = {
        "port": 7890,
        "socks-port": 7891,
        "allow-lan": False,
        "mode": "Rule",
        "log-level": "info",
        "proxies": [
            {
                "name": name,
                "type": "ss",
                "server": server,
                "port": int(port),
                "cipher": cipher,
                "password": password,
                "udp": True,
            }
        ],
        "proxy-groups": [
            {"name": "Proxy", "type": "select", "proxies": [name, "DIRECT"]}
        ],
        "rules": ["MATCH,Proxy"],
    }

    with open(yaml_file, "w") as f:
        yaml.dump(config, f, sort_keys=False)

    print("Clash YAML created successfully.")


def _create_qr(ss_url: str):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_Q,
        box_size=10,
        border=4,
    )
    qr.add_data(ss_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(qr_file)


# The main functions


def setup_server():
    if len((ins_list := _list_instances()).get("instances", [])) > 0:
        print("------------------------------")
        for ins in ins_list["instances"]:
            print(f"ID: {ins['id']}, IP: {ins['main_ip']}, Status: {ins['status']}")
            print("------------------------------")
        print("One or more instances are already running...")
        print("Type 'new' to continue deploying a new one: ")
        print("Type 'old' to connect to the existing instance and run script:")

        confirm = input().strip().lower()
    else:
        confirm = "new"

    if confirm == "new":
        print("Deploying a new instance...")
        password, instance_id, dep_info = _deploy_instance(
            "icn", "vc2-1c-1gb", 2657, "AA"
        )
        print(f"Instance deploying: \n{dep_info}")
        print(f"Waiting for the instance to be running and IP to be ready...")
        address = _wait_ip_ready(instance_id)
    elif confirm == "old":
        info = _get_local_server_info()
        if not info:
            return
        address = info.get("ip")
        password = info.get("password")
        instance_id = info.get("id")
        if not address or not password:
            print("Incomplete server info. Please check the server_info.txt file.")
            return
    else:
        print("Invalid input. Deployment canceled.")
        return

    my_ss_key = _ssh_connect(address, password, instance_id)
    _create_yaml(my_ss_key)
    print("Shadowrocket link and QR code created successfully:")
    print((my_ss_key).split("#")[0])
    _create_qr((my_ss_key).split("#")[0])


def bill_info(instance_id: str = None):
    info = _get_local_server_info()
    if not info:
        return print("No info")
    instance_id = info.get("id") if not instance_id else instance_id
    r = _get_instance(instance_id)
    if not r:
        print(f"Instance {instance_id} not found.")
        return
    unpaid_charges = r["instance"]["pending_charges"]
    duration = datetime.now(timezone.utc) - datetime.fromisoformat(
        r["instance"]["date_created"]
    )
    seconds = int(duration.total_seconds())

    hh, mm = seconds // 3600, (seconds % 3600) // 60
    print("------------------------------")
    print(f"ID: {instance_id}, Run-Time: {hh}h {mm}m, Unpaid: ${unpaid_charges:.2f}")
    print("------------------------------")


def shutdown_server(instance_id: str = None):
    info = _get_local_server_info()
    instance_id = info.get("id") if not instance_id else instance_id
    r = _get_instance(instance_id)
    if not r:
        print(f"Instance {instance_id} not found.")
        return
    ip = r["instance"]["main_ip"]
    status = r["instance"]["status"]
    print("------------------------------")
    print(f"ID: {instance_id}, IP: {ip}, Status: {status}")
    print("------------------------------")
    confirm = input(f"Type 'yes' to destroy instance {instance_id}: ")

    if confirm.strip().lower() == "yes":
        destroy_response = _destroy_instance(instance_id)
        print(destroy_response)

        open(server_info, "w").close()
    else:
        print("Destroy canceled.")


funcs = {
    "1": ("Deploy", setup_server),
    "2": ("Duration", bill_info),
    "3": ("Destroy", shutdown_server),
}


def main():
    print("🚀 Fast VPN Server Setup")
    while True:
        print("\n   Server Menu: [0 quit]")
        for key, (desc, _) in funcs.items():
            print(f"{key}. {desc}")

        choice = input("Enter a number: ").strip()

        if choice == "0":
            print("Bye!")
            break
        elif choice in funcs:
            funcs[choice][1]()
        else:
            print("❌ Invalid input! Please enter a valid number.")


if __name__ == "__main__":
    main()

# endregion
