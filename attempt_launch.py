#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCI ARM 免费实例单次抢购尝试（GitHub Actions 每 5 分钟调用）"""
import os
import sys
import time
import datetime
import subprocess

import oci


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


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
    client = oci.core.ComputeClient(config)

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
        resp = client.launch_instance(details)
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

    ip = None
    deadline = time.time() + 600
    while time.time() < deadline:
        try:
            cur = client.get_instance(inst.id).data
        except Exception:
            time.sleep(10)
            continue
        if cur.lifecycle_state == "RUNNING":
            break
        if cur.lifecycle_state in ("TERMINATED", "TERMINATING"):
            log("!!! 实例进入终止状态")
            return 2
        time.sleep(10)

    try:
        for v in client.list_vnics(inst.id).data:
            if v.public_ip:
                ip = v.public_ip
                break
    except Exception:
        pass

    log("实例已运行, 公网IP: %s" % (ip or "未知(稍后查)"))
    body = "ARM 免费实例已抢到!\n\n- 实例 OCID: %s\n- 公网 IP: %s\n- 登录命令: ssh -i C:\\Users\\Administrator\\.oci\\oci_api_key ubuntu@%s" % (
        inst.id, ip or "待查", ip or "<IP>"
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
