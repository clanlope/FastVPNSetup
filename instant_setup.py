"""
Windows/Android: https://github.com/clash-download/Clash
iOS: Shadowrocket: https://apps.apple.com/app/id932747118
"""

API_KEY = "YOUR_API_KEY_HERE"  # ← Please fill in your Vultr API Key

# ==================== Constants ====================
API_BASE = "https://api.vultr.com/v2"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

SERVER_INFO_FILE = "server_info.txt"
YAML_FILE = "config.yaml"
QR_FILE = "ss_qr.png"

# ==================== Standard Library ====================
from datetime import datetime, timezone
import base64
import json
import re
import time

# ==================== Third Party ====================
import paramiko
import requests
import yaml
import qrcode
from PIL import Image, ImageDraw


# ==================== Utility Functions ====================


def _request(method, endpoint, **kwargs):
    url = f"{API_BASE}{endpoint}"
    headers = kwargs.pop("headers", {})
    headers.update(HEADERS)

    r = requests.request(method, url, headers=headers, **kwargs)

    if not r.ok:
        raise Exception(f"API Error {r.status_code}: {r.text}")

    return r.json() if r.text else {}


# ==================== Vultr API ====================


def _list_instances():
    return _request("GET", "/instances")


def _get_instance(instance_id):
    return _request("GET", f"/instances/{instance_id}")


def _deploy_instance(region, plan, os_id, label):
    data = {
        "region": region,
        "plan": plan,
        "os_id": os_id,
        "label": label,
        "enable_ipv6": False,
    }

    res = _request("POST", "/instances", json=data)
    ins = res["instance"]

    with open(SERVER_INFO_FILE, "w", encoding="utf-8") as f:
        f.write(f"id: {ins['id']}\n")
        f.write(f"user: root\n")
        f.write(f"password: {ins['default_password']}\n")

    return ins["default_password"], ins["id"], json.dumps(res, indent=2)


def _reboot_instance(instance_id):
    _request("POST", f"/instances/{instance_id}/reboot")
    print(f"🔄 Rebooted instance {instance_id}")


def _destroy_instance(instance_id):
    requests.delete(f"{API_BASE}/instances/{instance_id}", headers=HEADERS)
    print(f"🗑 Destroyed instance {instance_id}")


def _wait_ip_ready(instance_id):
    while True:
        info = _get_instance(instance_id)["instance"]
        print(f"Status: {info['status']} / {info['server_status']}")

        if info["status"] == "active" and info["server_status"] == "ok":
            ip = info["main_ip"]

            with open(SERVER_INFO_FILE, "a") as f:
                f.write(f"ip: {ip}\n")

            print(f"✅ IP Ready: {ip}")
            return ip

        time.sleep(10)


# ==================== SSH ====================


def _run_cmd(client, cmd):
    stdin, stdout, stderr = client.exec_command(cmd)
    stdout.channel.recv_exit_status()
    return stdout.read().decode(), stderr.read().decode()


def _ssh_connect(address, password, instance_id):
    for attempt in range(1, 4):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(address, username="root", password=password)
            print("✅ SSH connected")
            break
        except Exception as e:
            print(f"❌ SSH failed ({attempt}/3): {e}")
            time.sleep(20)
    else:
        print("🔄 Rebooting...")
        _reboot_instance(instance_id)
        return _ssh_connect(address, password, instance_id)

    _run_cmd(
        client,
        "bash <(wget -qO- https://github.com/233boy/sing-box/raw/main/install.sh)",
    )
    _run_cmd(client, "sb bbr")

    out, _ = _run_cmd(client, "sb add ss")

    match = re.search(r"ss://[^\s]+", out)
    if not match:
        raise Exception("❌ SS link not found")

    ss_url = match.group(0)
    port = re.search(r":(\d{4,5})", out).group(1)

    _run_cmd(client, "ufw --force enable")
    _run_cmd(client, f"ufw allow {port}")

    client.close()
    return ss_url


# ==================== Configuration ====================


def _parse_ss(ss_url):
    body = ss_url[5:]

    if "#" in body:
        body, _ = body.split("#", 1)

    b64_part, server_part = body.split("@")

    decoded = base64.urlsafe_b64decode(b64_part + "=" * (-len(b64_part) % 4)).decode()

    cipher, password = decoded.split(":")
    server, port = server_part.split(":")

    return {
        "name": "MySS",
        "server": server,
        "port": int(port),
        "cipher": cipher,
        "password": password,
    }


def _create_yaml(ss_url):
    cfg = _parse_ss(ss_url)

    config = {
        "port": 7890,
        "socks-port": 7891,
        "mode": "Rule",
        "log-level": "info",
        "proxies": [
            {
                **cfg,
                "type": "ss",
                "udp": True,
            }
        ],
        "proxy-groups": [
            {"name": "Proxy", "type": "select", "proxies": [cfg["name"], "DIRECT"]}
        ],
        "rules": ["MATCH,Proxy"],
    }

    with open(YAML_FILE, "w") as f:
        yaml.dump(config, f, sort_keys=False)

    print("✅ YAML created")


def _create_qr(ss_url):

    ss_url = ss_url.split("#")[0] if "#" in ss_url else ss_url
    qr = qrcode.make(ss_url).convert("RGB")

    w, h = qr.size
    gradient = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(gradient)

    for y in range(h):
        for x in range(w):
            ratio = (x + y) / (w + h)
            color = (
                int(255 * (1 - ratio) + 80 * ratio),
                int(100 * (1 - ratio) + 255 * ratio),
                int(170 * (1 - ratio) + 255 * ratio),
            )
            draw.point((x, y), fill=color)

    pixels = qr.load()
    grad_pixels = gradient.load()

    for y in range(h):
        for x in range(w):
            if pixels[x, y] == (0, 0, 0):
                pixels[x, y] = grad_pixels[x, y]

    qr.save(QR_FILE)
    print("✅ QR created")


# ==================== Local ====================


def _get_local_info():
    try:
        info = {}
        with open(SERVER_INFO_FILE) as f:
            for line in f:
                k, v = line.strip().split(":", 1)
                info[k] = v.strip()
        return info
    except:
        return None


# ==================== Main Functions ====================


def setup_server():
    instances = _list_instances().get("instances", [])

    if instances:
        print("\nExisting instances:")
        for ins in instances:
            print(f"{ins['id']} | {ins['main_ip']} | {ins['status']}")

        choice = input("new / old: ").strip()
    else:
        choice = "new"

    if choice == "new":
        pwd, ins_id, _ = _deploy_instance("icn", "vc2-1c-1gb", 2657, "node")
        ip = _wait_ip_ready(ins_id)
    else:
        info = _get_local_info()
        if not info:
            return print("❌ No local info")

        ip = info["ip"]
        pwd = info["password"]
        ins_id = info["id"]

    ss = _ssh_connect(ip, pwd, ins_id)

    _create_yaml(ss)
    _create_qr(ss)

    print("\n🔗 SS URL:")
    print(ss.split("#")[0])


def bill_info():
    info = _get_local_info()
    if not info:
        return print("❌ No info")

    ins = _get_instance(info["id"])["instance"]

    duration = datetime.now(timezone.utc) - datetime.fromisoformat(ins["date_created"])
    seconds = int(duration.total_seconds())

    print(f"⏱ {seconds//3600}h {(seconds%3600)//60}m")
    print(f"💰 ${ins['pending_charges']:.2f}")


def destroy_server():
    info = _get_local_info()
    if not info:
        return print("❌ No info")

    confirm = input("Type YES to destroy: ")
    if confirm == "YES":
        _destroy_instance(info["id"])
        open(SERVER_INFO_FILE, "w").close()


def account_info():
    res = _request("GET", "/account")
    print(json.dumps(res, indent=2))


# ==================== CLI ====================

MENU = {
    "1": ("Deploy", setup_server),
    "2": ("Duration", bill_info),
    "3": ("Destroy", destroy_server),
    "4": ("Account", account_info),
}


def main():
    if not API_KEY:
        raise ValueError("❌ Please set API_KEY")

    while True:
        print("\n🚀 Fast VPN")
        for k, v in MENU.items():
            print(f"{k}. {v[0]}")
        print("0. Exit")

        c = input("Select: ").strip()

        if c == "0":
            break

        if c in MENU:
            try:
                MENU[c][1]()
            except Exception as e:
                print(f"❌ Err: {e}")
        else:
            print("❌ Invalid")


if __name__ == "__main__":
    main()
