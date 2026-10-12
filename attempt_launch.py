#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCI ARM 免费实例单次抢购尝试 + 抢到后自动部署 wg-easy VPN"""
import os
import sys
import time
import datetime
import subprocess
import secrets

import oci


VPN_PASSWORD = secrets.token_urlsafe(9)


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def open_ports(vn):
    subnet_id = os.environ["OCI_SUBNET"]
    try:
        subnet = vn.get_subnet(subnet_id).data
    except Exception as e:
        log("获取子网失败: %s" % e)
        return
    if not subnet.security_list_ids:
        log("子网无安全列表")
        return
    sl_id = subnet.security_list_ids[0]
    try:
        sl = vn.get_security_list(sl_id).data
    except Exception as e:
        log("获取安全列表失败: %s" % e)
        return
    ingress = list(sl.ingress_security_rules)

    def exists(port, proto):
        for r in ingress:
            opts = r.udp_options if proto == "17" else r.tcp_options
            if r.protocol == proto and opts and opts.destination_port_range:
                dpr = opts.destination_port_range
                if dpr.min == port and dpr.max == port:
                    return True
        return False

    if not exists(51820, "17"):
        ingress.append(oci.core.models.IngressSecurityRule(
            protocol="17", source="0.0.0.0/0",
            udp_options=oci.core.models.UdpOptions(
                destination_port_range=oci.core.models.PortRange(min=51820, max=51820)),
            description="WireGuard UDP",
        ))
    if not exists(51821, "6"):
        ingress.append(oci.core.models.IngressSecurityRule(
            protocol="6", source="0.0.0.0/0",
            tcp_options=oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(min=51821, max=51821)),
            description="wg-easy panel TCP",
        ))
    try:
        vn.update_security_list(
            sl_id,
            oci.core.models.UpdateSecurityListDetails(
                ingress_security_rules=ingress,
                egress_security_rules=list(sl.egress_security_rules),
            ),
        )
        log("安全列表已开放 51820/udp 与 51821/tcp")
    except Exception as e:
        log("更新安全列表失败: %s" % e)


def deploy_vpn(ip):
    global VPN_PASSWORD
    home = os.path.expanduser("~")
    ssh_dir = os.path.join(home, ".ssh")
    os.makedirs(ssh_dir, exist_ok=True)
    key = os.path.join(ssh_dir, "id_rsa")
    with open(key, "w") as f:
        f.write(os.environ["OCI_KEY"])
    os.chmod(key, 0o600)
    try:
        with open(os.path.join(ssh_dir, "known_hosts"), "a") as f:
            subprocess.run(["ssh-keyscan", "-H", ip], stdout=f, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        pass
    remote = (
        "sudo apt-get update -qq && "
        "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io && "
        "sudo systemctl enable --now docker && "
        "until sudo docker info >/dev/null 2>&1; do sleep 2; done && "
        "sudo docker run -d --name wg-easy --restart=unless-stopped "
        "-e WG_HOST=%s -e PASSWORD=%s -e WG_DEFAULT_ADDRESS=10.8.0.x "
        "-e WG_ALLOWED_IPS=0.0.0.0/0 -e WG_PERSISTENT_KEEPALIVE=25 "
        "-p 51820:51820/udp -p 51821:51821/tcp "
        "--cap-add NET_ADMIN --cap-add SYS_MODULE --sysctl net.ipv4.conf.all.src_valid_mark=1 "
        "-v /home/ubuntu/.wg-easy:/etc/wireguard "
        "weejewel/wg-easy"
    ) % (ip, VPN_PASSWORD)
    ssh_cmd = [
        "ssh", "-i", key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=30",
        "ubuntu@%s" % ip, remote,
    ]
    log("开始部署 wg-easy ...")
    try:
        r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=420)
        log("SSH 退出码: %s" % r.returncode)
        if r.stdout and r.stdout.strip():
            log("部署输出: " + r.stdout.strip()[-400:])
        if r.stderr and r.stderr.strip():
            log("部署 stderr: " + r.stderr.strip()[-400:])
        if r.returncode != 0:
            log("部署可能未完全成功，请稍后手动检查")
    except Exception as e:
        log("SSH 部署异常: %s" % e)


def main():
    cfg = {
        "user": os.environ["OCI_USER"],
        "fingerprint": os.environ["OCI_FINGERPRINT"],
        "tenancy": os.environ["OCI_TENANCY"],
        "region": os.environ["OCI_REGION"],
    }
    oci_dir = os.path.expanduser("~/.oci")
    os.makedirs(oci_dir, exist_ok=True)
    key_path = os.path.join(oci_dir, "api_key.pem")
    with open(key_path, "w") as f:
        f.write(os.environ["OCI_KEY"])
    cfg["key_file"] = key_path

    config_path = os.path.join(oci_dir, "config")
    with open(config_path, "w") as f:
        f.write("[DEFAULT]\n")
        f.write("user=%s\n" % cfg["user"])
        f.write("fingerprint=%s\n" % cfg["fingerprint"])
        f.write("tenancy=%s\n" % cfg["tenancy"])
        f.write("region=%s\n" % cfg["region"])
        f.write("key_file=%s\n" % key_path)

    config = oci.config.from_file()
    compute = oci.core.ComputeClient(config)

    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=config["tenancy"],
        availability_domain=os.environ["AD"],
        display_name="instance-20261012-1202",
        shape="VM.Standard.A1.Flex",
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=2.0, memory_in_gbs=12.0),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=os.environ["OCI_IMAGE"], boot_volume_size_in_gbs=100
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=os.environ["OCI_SUBNET"], assign_public_ip=True
        ),
        metadata={"ssh_authorized_keys": os.environ["OCI_SSH_PUB"]},
    )

    try:
        resp = compute.launch_instance(details)
    except oci.exceptions.ServiceError as e:
        msg = "%s %s %s" % (e.status, e.code, e.message)
        log("尝试失败: " + msg)
        low = (str(e.message) + " " + str(e.code)).lower()
        retryable = (
            any(k in low for k in ["capacity", "out of host", "resource not available", "not enough capacity"])
            or e.status in (500, 429)
        )
        return 0 if retryable else 1

    inst = resp.data
    log(">>> 抢购成功! 实例 OCID: " + inst.id)

    deadline = time.time() + 600
    while time.time() < deadline:
        try:
            cur = compute.get_instance(inst.id).data
        except Exception:
            time.sleep(10)
            continue
        if cur.lifecycle_state == "RUNNING":
            break
        if cur.lifecycle_state in ("TERMINATED", "TERMINATING"):
            log("!!! 实例进入终止状态")
            return 2
        time.sleep(10)

    ip = None
    try:
        for v in compute.list_vnics(inst.id).data:
            if v.public_ip:
                ip = v.public_ip
                break
    except Exception:
        pass
    log("实例已运行, 公网IP: %s" % (ip or "未知(稍后查)"))
    if ip:
        open_ports(oci.core.VirtualNetworkClient(config))
        deploy_vpn(ip)

    body = (
        "ARM 免费实例已抢到!\n\n"
        "- 实例 OCID: %s\n"
        "- 公网 IP: %s\n"
        "- SSH 登录: ssh -i C:\\Users\\Administrator\\.oci\\oci_api_key ubuntu@%s\n"
        % (inst.id, ip or "待查", ip or "<IP>")
    )
    if ip:
        body += (
            "\nVPN 已自动部署:\n"
            "- 管理面板: http://%s:51821\n"
            "- 面板密码: %s\n"
            "- WireGuard 端口: 51820/udp\n"
            "- 手机/电脑装好 WireGuard 客户端后，打开面板创建隧道并扫码即可连接\n"
            % (ip, VPN_PASSWORD)
        )
    try:
        subprocess.run(
            ["gh", "issue", "create", "--title", "ARM 实例已抢到!", "--body", body],
            check=True,
            env=dict(os.environ, GH_TOKEN=os.environ.get("GITHUB_TOKEN", "")),
        )
        log("已在仓库创建通知 Issue")
    except Exception as e:
        log("创建 Issue 失败: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
