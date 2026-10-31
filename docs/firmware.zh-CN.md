# ESP32-S3 固件构建

[English](firmware.md) | [简体中文](firmware.zh-CN.md) · [README](../README.zh-CN.md)

## 范围与状态

固件工作面向独立 ESP-IDF 应用，实现 ESP32-S3 网络到 UART 网关，使用 Apache-2.0 的 [igrr/rfc2217-server 组件](https://github.com/igrr/rfc2217-server)。它独立于 PC 包，不复制其他设备的应用固件。

**`0.2.0-alpha.1` 的实现、可复现构建及硬件验收尚待完成。** 以下步骤定义构建和审查门禁，不证明固件产物已存在或能够启动。执行前检查真实项目文件；若独立项目或依赖清单不完整，应停在该前置条件并记录为待完成。

PC 支持 raw TCP 和 RFC2217，不代表固件已经同时实现两种协议。独立固件支持的协议、设置和控制映射，须依据最终实现与测试确认。

## 工具链与项目条件

按官方 [ESP32-S3 ESP-IDF 入门](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/get-started/index.html)配置环境，并使用已激活的 ESP-IDF 终端。记录准确 ESP-IDF 版本和编译器版本。上游组件兼容范围不等于本项目已验证的工具链矩阵。

构建前确认：

- `firmware/` 是独立 ESP-IDF 工程，包含根 `CMakeLists.txt`、应用组件和依赖清单。
- 清单以可复现的版本约束解析 `igrr/rfc2217-server`；保留本次构建的依赖锁定信息。
- 板级设置符合实际模组。ESP32-S3-WROOM-1-N16R8 的 Flash 为 16 MB，PSRAM 为 8 MB Octal/OPI。应用是否使用 PSRAM 必须明确；不可给 Octal 模组使用 Quad-PSRAM 设置。
- 控制台/下载通道与目标 UART 分离。接入目标前审查引脚表和任何控制输出。
- 网络配置在本地提供。生成的配置、构建日志、Flash 镜像和 NVS 可能含网络凭据；分享前检查产物。

准确 ESP-IDF/组件版本、菜单名称、UART 引脚、监听端口和启用的控制输出，待实现和成功构建后确认。

## 构建步骤

在仓库根目录、已激活的 ESP-IDF 终端中，**满足上述条件后**执行标准工程命令：

```powershell
idf.py --version
idf.py -C firmware set-target esp32s3
idf.py -C firmware menuconfig
idf.py -C firmware build
```

`set-target` 可能重新生成构建配置。切换目标前保留需要的本地设置。在 `menuconfig` 中根据实现核对 Flash 容量/模式、启用时的 PSRAM 模式、控制台路由、目标 UART 引脚、网络设置和监听端口。实现完成前不承诺具体菜单名或凭据机制。

不要通过手动添加未固定版本的依赖绕过不完整工程。应在固件实现中解决清单，再用已记录工具链构建。修改生成的依赖代码时须记录补丁并保留上游许可声明。

成功构建须以 `0` 退出，并生成预期应用、Bootloader、分区表和烧录参数。记录源码修订、解析出的组件版本、相关非敏感配置、镜像大小和 SHA-256。使用本次构建生成的烧录参数，不从其他芯片或项目复制固定偏移。

## 固件行为审查

将固件描述为可用远端前，按[远端约定](remote-contract.zh-CN.md)核对实现：

| 范围 | 审查要求 |
| --- | --- |
| RFC2217 | 协商、字面 `0xFF`、命令分片、支持的 UART 设置、如实处理不支持的请求。 |
| UART | 外设和引脚选择、波特率/线路设置、收发完整性、缓冲、溢出处理。 |
| 控制 | 明确 DTR、RTS、BREAK、流控、调制解调器状态及复位/启动输出是支持、未映射还是拒绝。 |
| 会话 | 客户端上限、断线清理、UART 缓存策略、网络中断行为、不跨会话重放。 |
| 诊断 | 控制台不输出到目标 UART；公开报告不包含凭据和唯一设备信息。 |
| 网络 | 不宣称 RFC2217 自带认证/加密；由部署网络边界限制访问。 |

上游 UART 或 `uart_esptool` 示例不能证明其他 Bootloader 兼容。ESP 专用自动复位行为尤其不能直接当作 ASR 烧录约定。

## 烧录与首次启动门禁

烧录会覆盖所选 ESP32-S3 板卡。只有完成[电气和板卡身份检查](hardware-validation.zh-CN.md)，且操作人选择了可覆盖或已备份的板卡后才执行。首次启动固件前断开目标 UART/控制接线。

在 PowerShell 中由本地操作人确认网关下载端口：

```powershell
$GatewayPort = Read-Host "Confirmed ESP32-S3 gateway download port"
idf.py -C firmware -p "$GatewayPort" flash monitor
```

选择适合板卡的接口和启动步骤。DevKitC 类板的 USB-to-UART 与原生 USB 是不同通道。需要手动进入 ROM 下载模式时，按住 BOOT、点按 RESET、释放 BOOT。此步骤针对 ESP32-S3 网关，不是下游目标。按 ESP-IDF 文档规定的退出序列结束监视器。

接入回环或目标前，确认启动稳定、存储器设置、控制台路由和配置的网络监听。不要公开包含网络名称或设备标识的原始启动日志。启动日志和 TCP 端口可连接仍不能验证 UART 或目标烧录。

## 验收与发布证据

依次执行[硬件验证阶段](hardware-validation.zh-CN.md)，再更新对应[验证矩阵](verification.zh-CN.md)。分别记录仅构建、仅启动、UART 回环、驱动和目标应用结果。

发布固件二进制前，确认产物没有嵌入凭据或私有配置，保留适用组件/框架声明并记录准确构建来源。本地含凭据构建不能直接作为公开发布产物。所声明阶段全部通过前，固件应描述为实验性，并保留未完成阶段的待验证状态。
