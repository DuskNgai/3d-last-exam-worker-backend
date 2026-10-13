# Setup

Setup 在 Worker 部署时完成。

## Download

评测所需材料及对应的下载命令见 [materials.md](../definition/materials.md)。部署时可用统一脚本一次下载全部材料：

```sh
scripts/download/materials.py --config src/configuration.yaml
```

每条材料都按表里的 SHA256 核对，对不上即报错。

