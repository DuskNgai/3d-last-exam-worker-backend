# Identity Setup

Worker 身份的安装手册：领取设备标识与设备令牌，生成 TLS 自签证书与私钥，向 Master 登记。

1. 设备标识与设备令牌由 Master 签发。拿到后手工写进配置的 `device_id` 与 `device_token`。

2. 装好 openssl：

```sh
sudo apt install -y openssl
```

3. TLS 自签证书与私钥在本机生成：

```sh
python src/setup/identity.py --config src/configuration.yaml
```

- 脚本以 `device_id` 为 CN 生成有效期十年的自签证书与 RSA 2048 位私钥，写到 `tls_certificate_file` 与 `tls_key_file` 给的两个路径。
- 脚本输出证书的 SHA256 指纹。

4. 把证书指纹、Master 能访问的 Worker 地址与端口由人报给 Master 登记。证书是自签的，Master 靠登记的指纹在每次连接时核对本机身份。
