"""
Windows/Linux/Android: https://github.com/clash-download/Clash
iOS: Shadowrocket

Set your API key in the environment variable API_KEY_VULTR before using the script.
Windows: setx API_KEY_VULTR "your_key_here"
Linux: echo 'export API_KEY_VULTR="your_key_here"' >> ~/.bashrc
"""

# region Function
import os
import base64
import json
import re
import time

import paramiko
import requests
import yaml
import qrcode
from datetime import datetime, timezone

API_KEY = os.getenv("API_KEY_VULTR", "")
YAML_FILENAME = "config.yaml"


API_BASE = "https://api.vultr.com/v2"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
SCRIPT_URL = "https://github.com/233boy/sing-box/raw/main/install.sh"
REGION = "icn"  # Seoul, South Korea
PLAN = "vc2-1c-1gb"  # vc2-1c-1gb $0.007/hr, vhp-1c-1gb $0.008/hr
OS_ID = 2657  # Ubuntu 22.04 x64
LABEL = "AA_VPN"


def timer(func):
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        print(f"🕛 Starting at {datetime.now(timezone.utc)}")
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"🕛 Executed in {elapsed:.2f}s")
        return result

    return wrapper


def _request(method, endpoint, **kwargs):
    url = f"{API_BASE}{endpoint}"
    headers = kwargs.pop("headers", {})
    headers.update(HEADERS)
    r = requests.request(method, url, headers=headers, **kwargs)
    r.raise_for_status()
    return r.json() if r.text else {}


def _list_instance():
    return _request("GET", "/instances")


def _get_instance(instance_id: str):
    return _request("GET", f"/instances/{instance_id}")


def _reboot_instance(instance_id):
    _request("POST", f"/instances/{instance_id}/reboot")
    print(f"🟢 Rebooted instance {instance_id}")


def _destroy_instance(instance_id):
    requests.delete(f"{API_BASE}/instances/{instance_id}", headers=HEADERS)
    print(f"🟢 Destroyed instance {instance_id}")


def _deploy_instance(region, plan, os_id, label):
    payload = {
        "region": region,
        "plan": plan,
        "os_id": os_id,
        "label": label,
        "enable_ipv6": False,
    }
    res = _request("POST", "/instances", json=payload)
    ins = res["instance"]
    return ins["default_password"], ins["id"]


def _wait_instance(instance_id):
    iteration = 0
    while True:
        info = _get_instance(instance_id)["instance"]
        if iteration % 3 == 0:
            print(f"⚪ Status: {info['status']} / {info['server_status']}")
        if info["status"] == "active" and info["server_status"] == "ok":
            print(f"🟢 IP: {info['main_ip']}")
            return info["main_ip"]
        iteration += 1
        time.sleep(5)


def _run_cmd(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd)
    stdout.channel.recv_exit_status()
    return stdout.read().decode(), stderr.read().decode()


def _ssh_connect(instance_id, password, attempts=10):
    address = _wait_instance(instance_id)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for attempt in range(1, attempts + 1):
        try:
            time.sleep(12)  # Wait a bit before trying to connect
            client.connect(address, username="root", password=password)
            print("🟢 SSH connection established")
            break
        except Exception as e:
            print(f"⚪ SSH no response, retrying... ({attempt}/{attempts}): {e}")
            client.close()

    else:
        print("🔴 Error occurred, try manually...")
        # _reboot_instance(instance_id)
        print(f"🟡 Username: root, Password: {password}")
        raise (Exception("SSH connection failed after multiple attempts"))

    _run_cmd(client, f"bash <(wget -qO- {SCRIPT_URL})")
    _run_cmd(client, "sb bbr")

    out, _ = _run_cmd(client, "sb add ss")
    ss_url = re.search(r"ss://[^\s]+", out).group(0).split("#")[0]
    port = re.search(r":(\d{4,5})", out).group(1)

    _run_cmd(client, "ufw --force enable")
    _run_cmd(client, f"ufw allow {port}")

    client.close()
    return ss_url


def _create_yaml(ss_url, output_path):
    def _parse_ss(ss_url):
        body = ss_url[5:]
        b64_part, server_part = body.split("@")

        decoded = base64.urlsafe_b64decode(
            b64_part + "=" * (-len(b64_part) % 4)
        ).decode()

        cipher, password = decoded.split(":")
        server, port = server_part.split(":")

        return {
            "name": LABEL,
            "server": server,
            "type": "ss",
            "port": int(port),
            "cipher": cipher,
            "password": password,
            "udp": True,
        }

    cfg = _parse_ss(ss_url)

    config = {
        "port": 7890,
        "socks-port": 7891,
        "mode": "Rule",
        "log-level": "info",
        "proxies": [cfg],
        "proxy-groups": [
            {"name": "Proxy", "type": "select", "proxies": [cfg["name"], "DIRECT"]}
        ],
        "rules": ["MATCH,Proxy"],
    }
    with open(output_path, "w") as f:
        yaml.dump(config, f, sort_keys=False)

    print("🟢 YAML.config created [Clash]")


def _create_qr(ss_url):
    qr = qrcode.QRCode()
    qr.add_data(ss_url)
    qr.make()
    qr.print_ascii()
    print("🟢 QR printed in terminal")


# ==================== Main Functions ====================


def setup_a_server():
    print("⚪ Deploying starts in 3s...")
    time.sleep(3)
    pwd, ins_id = _deploy_instance(REGION, PLAN, OS_ID, LABEL)
    ss = _ssh_connect(ins_id, pwd)
    print(f"🟢 {ss}")
    _create_qr(ss)
    try:
        _create_yaml(ss, YAML_FILENAME)
    except Exception as e:
        print(f"⚪ Error creating config: {e}")


def destroy_a_server(earliest=None):
    confirm = input("🟡 Type Y to destroy or enter to cancel: ")
    if confirm.upper() == "Y":
        _destroy_instance(earliest["id"])
    else:
        print("⚪ Cancelled")


def check_account():
    rea = _request("GET", "/account")
    res = rea.get("account", {})
    wd = [
        "name",
        # "email",
        "balance",
        "pending_charges",
        # "last_payment_amount",
        # "last_payment_date",
    ]
    filtered = {k: res.get(k) for k in wd if k in res}
    print("🔍 Account Info:")
    lines = json.dumps(filtered, indent=2).splitlines()
    inner_lines = lines[1:-1]
    print("\n".join(inner_lines))


@timer
def main():
    try:
        print("🚀 Vultr Server Setup")
        check_account()
        instances = _list_instance().get("instances", [])
        if instances:
            earliest = min(instances, key=lambda i: i.get("date_created", ""))
            my_ins = _get_instance(earliest["id"])["instance"]
            created = datetime.fromisoformat(earliest["date_created"])
            seconds = int((datetime.now(timezone.utc) - created).total_seconds())
            print(f"🔵 Instance exists already: {earliest['main_ip']}, destroy it?")
            print(
                f"🕒 Time: {seconds//3600}h {(seconds%3600)//60}m | 💵 Cost: ${my_ins.get('pending_charges', 0.0)}"
            )
            destroy_a_server(earliest)
        else:
            print("🟡 No instance found, creating a new one...")
            setup_a_server()
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()

# endregion
