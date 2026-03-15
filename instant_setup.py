"""
Server:
Setup script for Vultr. It deploys a new instance, installs Sing-box, and generates SS link/Clash YAML config.
https://my.vultr.com/settings/#settingsapi -> API -> Add Key -> Access Control Add All

Client:
Windows/Android: https://github.com/clash-download/Clash
IOS: shadowrocket: https://apps.apple.com/app/id932747118
"""

# First time: get an API key from https://vultr.com/ and replace the value of API_KEY below.
API_KEY = "your_api_key_here"

# region Functions
server_info = "server_info.txt"
yaml_file = "config.yaml"

import requests
import paramiko
import re
import time
import base64
import yaml
import json
from datetime import datetime, timezone


def _destroy_instance(instance_id):
    url = f"https://api.vultr.com/v2/instances/{instance_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    r = requests.delete(url, headers=headers)

    if r.status_code == 204:
        return {"status": "success", "message": f"Instance {instance_id} destroyed."}
    else:
        print(f"Destroy instance failed: {r.status_code}, {r.text}")


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


def _get_instance(instance_id):
    url = f"https://api.vultr.com/v2/instances/{instance_id}"
    headers = {"Authorization": f"Bearer {API_KEY}"}

    r = requests.get(url, headers=headers)
    return r.json() if r.status_code == 200 else None


def _wait_ip_ready(instance_id):
    while True:
        info = _get_instance(instance_id)
        status = info["instance"]["status"]
        server_status = info["instance"]["server_status"]

        if status == "active" and server_status == "ok":
            ip = info["instance"]["main_ip"]
            print(f"Instance is active now. Host IP Address: {ip}")
            with open(server_info, "a", encoding="utf-8") as f:
                f.write(f"ip: {ip}\n")

            return ip
        elif status == "pending":
            print("Instance is preparing...")

        time.sleep(5)


def _list_instances():
    url = "https://api.vultr.com/v2/instances"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(url, headers=headers)
    return response.json()


def _run_cmd(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd)

    exit_status = stdout.channel.recv_exit_status()

    out = stdout.read().decode()
    err = stderr.read().decode()

    return exit_status, out, err


def _create_yaml(ss_url):
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


def _get_local_info():
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


def setup_server():
    if len(_list_instances().get("instances", [])) > 0:
        print(
            "One or more instances are already running... Type 'yes' to continue deploying a new one: "
        )
        confirm = input().strip().lower()
        if confirm != "yes":
            print("Deployment canceled.")
            return

    password, instance_id, dep_info = _deploy_instance("icn", "vc2-1c-1gb", 2657, "AA")
    print(f"Instance deploying: \n{dep_info}")
    print(f"Waiting for the instance to be running and IP to be ready...")
    address = _wait_ip_ready(instance_id)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(address, username="root", password=password)

    _run_cmd(
        client,
        "bash <(wget -qO- -o- https://github.com/233boy/sing-box/raw/main/install.sh)",
    )
    _run_cmd(client, "sb bbr")

    _, output, _ = _run_cmd(client, "sb add ss")
    print(output.split("------------- END -------------")[0])
    my_ss_key = re.search(r"ss://[^\s]+", output)
    port = re.findall(r":(\d{4,5})", output)[0]
    print(f"Firewall allowing port {port}...")
    command = f"ufw allow {port}"
    _run_cmd(client, command)

    _, output, _ = _run_cmd(client, "ufw status numbered")
    print(output)

    client.close()
    print("Shadowrocket link:")
    print((my_ss_key.group(0)).split("#")[0])
    _create_yaml(my_ss_key.group(0))


def bill_info():
    info = _get_local_info()
    instance_id = info.get("id")
    r = _get_instance(instance_id)
    unpaid_charges = r["instance"]["pending_charges"]
    duration = datetime.now(timezone.utc) - datetime.fromisoformat(
        r["instance"]["date_created"]
    )
    seconds = int(duration.total_seconds())

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    print(f"Instance ID: {instance_id}")
    print(f"Duration: {hours}h{minutes}m{seconds}s")
    print(f"Unpaid Charges: ${unpaid_charges:.2f}")


def shutdown_server():
    info = _get_local_info()
    instance_id = info.get("id")
    instance = _get_instance(instance_id)
    if not instance:
        print(f"Instance {instance_id} not found.")
        return
    print(f"Instance {instance_id} found. Status: {instance['instance']['status']}")
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
        print("\n⚪ Server Management Menu:")
        for key, (desc, _) in funcs.items():
            print(f"{key}. {desc}")
        print("0. Exit")

        choice = input("Enter a number to select an action: ").strip()

        if choice == "0":
            print("Exit")
            break
        elif choice in funcs:
            funcs[choice][1]()
        else:
            print("❌ Invalid input! Please enter a valid number.")


if __name__ == "__main__":
    main()

# endregion
