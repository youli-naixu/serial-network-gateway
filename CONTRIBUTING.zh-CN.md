# 贡献指南

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

提交应围绕一个明确问题，说明行为变化和验证结果。传输层、驱动适配器、界面与板级配置保持分离，不把目标芯片专用烧录字节加入透明数据通道。

## 本地检查

```sh
python -m pip install -e ".[dev]"
python -m pytest -q -W error::ResourceWarning
python -m serial_gateway --language zh-CN selftest
python tools/check_public_tree.py
python firmware/scripts/build.py
python firmware/scripts/build.py --check-enabled
```

固件编译不等于硬件测试。完整链接检查镜像会在网络和GPIO初始化前停止，不能移除保护来宣称测试通过。

中英文文档同步更新；用户自定义配置名称不翻译。GUI修改必须在打包前实际检查两种语言的布局，语法检查不能替代预览。

## 硬件与驱动

自动测试只打开明确选择且核验身份的虚拟端口。真实UART测试先确认引脚、电平和发送端隔离。禁止自动关闭驱动签名、Secure Boot或内存完整性。驱动安装和UAC通过用户正常Windows授权完成。

使用独立分支和清晰提交。提交前检查暂存区，排除凭据、本机路径、真实IP/MAC/设备标识、下载的安装包、私有报告和构建输出。示例只用回环或文档保留地址，不捆绑第三方虚拟COM产品。

## 发布条件

记录源码版本、工具版本、测试和限制。声明支持虚拟COM前，应验证普通Windows COM客户端实际到达网络端，并验证所需参数/控制变化与断线行为。不得将模拟测试写成硬件验证。本项目首次上传必须满足约定的本地验收与脱敏检查条件。
