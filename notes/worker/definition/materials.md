# Materials

材料在部署时下载，评测时使用。

## ISOs

下载来的安装镜像，只用来搭建基础镜像。以下列出所有待下载的 ISO 镜像信息。

| 镜像 | 局部路径 | 官方地址 | SHA256 |
| --- | --- | --- | --- |
| `ubuntu-24.04.5.1-desktop-amd64.iso` | `isos/ubuntu-24.04.5.1-desktop-amd64.iso` | [Link](https://releases.ubuntu.com/24.04/ubuntu-24.04.5.1-desktop-amd64.iso) | `4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7` |

此外还有一个相关的目录：

- `images`：构建好的基础镜像，一个镜像一个目录。

### Downloading Script

```sh
scripts/download/isos.py --root <材料目录>
```

## Harnesses

以下列出所有待下载的 harnesses，ZCode 一行例外：官方不托管命令行版发行包，那一份由源码构建。安装包版本尽量选取 2026-09-25 UTC+0 当日（或者更早）发布的最新正式版本，且均可在 [ISOs](#isos) 对应的操作系统上运行。目录结构为 `harnesses/<名字>/<版本>/<厂商原始包>` 与 `harnesses/install.sh`。

| Harness | 版本 | 局部路径 | 官方地址 | SHA256 | 更新时间 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| Antigravity CLI | `1.2.11` | `harnesses/antigravity-cli/1.2.11/agy_cli_linux_x64.tar.gz` | [Link](https://github.com/google-antigravity/antigravity-cli/releases/download/1.2.11/agy_cli_linux_x64.tar.gz) | `c91c62c5e6fa954f5a7e1d7b9ad417d749db4aa60a4ba0b3d604dec1b645d190` | `2026-09-25` |  |
| Claude Code | `2.1.283` | `harnesses/claude-code/2.1.283/claude-code-linux-x64-2.1.283.tgz` | [Link](https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.283.tgz) | `d14ec0fca400151092c926928fea7e8e38231f7a90e6e1f8eeb2a55c24676afb` | `2026-09-25` |  |
| CodeBuddy Code | `2.158.0` | `harnesses/codebuddy-code/2.158.0/codebuddy-code-2.158.0.tgz` | [Link](https://registry.npmjs.org/@tencent-ai/codebuddy-code/-/codebuddy-code-2.158.0.tgz) | `4396a3997906521fe3a8ead7059d61840025b664ca4f65ce6f205960a9f44e0e` | `2026-09-24` |  |
| Codex CLI | `0.157.0` | `harnesses/codex/0.157.0/codex-package-x86_64-unknown-linux-musl.tar.gz` | [Link](https://github.com/openai/codex/releases/download/rust-v0.157.0/codex-package-x86_64-unknown-linux-musl.tar.gz) | `042f851ea3fc1083c45157520520944fc790632b53ebc580fc98eaca55862a25` | `2026-09-25` |  |
| DeepSeek Harness | `0.1.7-rc.2` | `harnesses/deepseek-harness/0.1.7-rc.2/dsh-0.1.7-rc.2.tgz` | [Link](https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.1.7-rc.2.tgz) | `5f2da7272d9485abc223e681075809a8d929697c5232ee445718e1b7e066bff8` | `2026-09-24` | 官方无稳定版，选用 RC |
| Grok Build | `1.0.41` | `harnesses/grok-build/1.0.41/grok-1.0.41-linux-x86_64` | [Link](https://x.ai/cli/grok-1.0.41-linux-x86_64) | `9ce03ed23e16ea01072b4496263d6213a27899e1e3e107f008d36edf82e70407` | `2026-09-22` |  |
| Kimi Code CLI | `2.1.1` | `harnesses/kimi-code/2.1.1/kimi-code-2.1.1.tgz` | [Link](https://registry.npmjs.org/@moonshot-ai/kimi-code/-/kimi-code-2.1.1.tgz) | `6690a29d7b5e14812754dd100136b7f4ee2577add8895f00fe27025b4412049f` | `2026-09-24` |  |
| Kiro CLI | `2.24.1` | `harnesses/kiro-cli/2.24.1/kirocli-x86_64-linux.tar.gz` | [Link](https://desktop-release.q.us-east-1.amazonaws.com/2.24.1/kirocli-x86_64-linux.tar.gz) | `9ddc94fe5d1c7530e14bf93b98e78b0bc74e3a36e14cfcf756373ba65ce41a2a` | `2026-09-25` |  |
| MiniMax Code | `0.5.5` | `harnesses/minimax-code/0.5.5/minimax-code-0.5.5.tgz` | [Link](https://registry.npmjs.org/@minimax-ai/code/-/code-0.5.5.tgz) | `1aee10f29045618d46fd37f341b4708f4ea3c0592cc35b8cb15f23671dbf0456` | `2026-09-25` |  |
| Mistral Vibe | `2.25.8` | `harnesses/mistral-vibe/2.25.8/vibe-acp-linux-x86_64-2.25.8.tar.gz` | [Link](https://github.com/mistralai/mistral-vibe/releases/download/v2.25.8/vibe-acp-linux-x86_64-2.25.8.tar.gz) | `dbe693ee66f29fdfcf9029dcbf3b2d4da138554b193e432b089840148ecf08b3` | `2026-09-23` |  |
| Muse Code | `1.4.0-R4161.1` | `harnesses/muse-code/1.4.0-R4161.1/muse-x86-linux` | [Link](https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=1.4.0-R4161.1&file=muse-x86-linux) | `1b68bd4518d53a2aaff063915df4d141b0a205e6d79038299d04e3a14e85a5b9` | `2026-09-28` |  |
| Qwen Code | `0.24.5` | `harnesses/qwen-code/0.24.5/qwen-code-linux-x64.tar.gz` | [Link](https://github.com/QwenLM/qwen-code/releases/download/v0.24.5/qwen-code-linux-x64.tar.gz) | `61490a0fe061d9fd539f238ef2b6a133ea203eeb1682f65b21b6af2c1c428869` | `2026-09-24` |  |
| TRAE CLI | `0.120.52` | `harnesses/trae-cli/0.120.52/trae-cli_0.120.52_linux_amd64.tar.gz` | [Link](https://lf-cdn.trae.com.cn/obj/trae-com-cn/trae-cli/trae-cli_0.120.52_linux_amd64.tar.gz) | `b92505d7ebfe1956387f4c9368bc66cfbdeb3b5e81ce773e2b0cbca2fb3e4c39` | `2026-08-12` |  |
| ZCode | `3.14.3` | `harnesses/zcode/3.14.3/zcode-3.14.3.tar.gz` | 本地构建（[源码](https://github.com/zai-org/ZCode)） | `137703b43276a93c4ce4c2f5f7c5420c3dd0182c357b34a41f3376e40f4f34f1` | `2026-09-28` | 官方无命令行版发行包，从源码构建 |

### Downloading Script

```sh
scripts/download/harnesses.py --root <材料目录>
```

## Tools

这里列出了所有的工具及其下载链接。一切安装包的版本以任务网页为主。目录结构为 `tools/<名字>/<版本>/<厂商原始包>` 与 `tools/<名字>/install.sh`。

| 任务软件 | 版本 | 局部路径 | 官方地址 | SHA256 | 更新时间 |
| --- | --- | --- | --- | --- | --- |
| Blender | `5.2.2` | `tools/blender/5.2.2/blender-5.2.2-linux-x64.tar.xz` | [Link](https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz) | `84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168` | `2026-09-15` |
| FreeCAD | `1.1.3` | `tools/freecad/1.1.3/FreeCAD_1.1.3-Linux-x86_64-py311.AppImage` | [Link](https://github.com/FreeCAD/FreeCAD/releases/download/1.1.3/FreeCAD_1.1.3-Linux-x86_64-py311.AppImage) | `3a853eb69ee595f779f2255dbf80a765926981d8ff68903cefee4dfb03a8f5ef` | `2026-07-25` |
| MuJoCo | `3.14.0` | `tools/mujoco/3.14.0/mujoco-3.14.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | [Link](https://files.pythonhosted.org/packages/4e/8e/1e50dc6db3c99a5a02642e253641bddef9b1b7c65527660baaae83eb3375/mujoco-3.14.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl) | `922983dfdf88d29f4eb0f451a1d18867156d9fc97e3df6e63b9933cc0eba4d38` | `2026-09-22` |
| Python | `3.11.16` | `tools/python/3.11.16/python-3.11.16-linux-64.conda` | [Link](https://repo.anaconda.com/pkgs/main/linux-64/python-3.11.16-h17756b0_0.conda) | `a095fd0449140bf8c6e77d251d9a425081fc00498337101134ba27e8355cac60` | `2026-08-27` |
| SDF View | `0.2.0` | `tools/sdf-view/0.2.0/sdf-view-x86_64-unknown-linux-gnu.tar.gz` | [Link](https://github.com/d-itu/sdf-view/releases/download/v0.2.0/sdf-view-x86_64-unknown-linux-gnu.tar.gz) | `6bea659124f0c135476b53e11b433f1f1e0a7502b0fe02d47043100a9e87e0e9` | `2026-09-26` |

TODO：tool 安装后验证满足 agent 的要求这步还没做

### Downloading Script

```sh
scripts/download/tools.py --root <材料目录>
```
