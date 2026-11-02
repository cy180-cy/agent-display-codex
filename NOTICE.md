# 来源与修改说明

本项目基于 [赛博老爸 / cyberdaddyleo 的 agent-display](https://github.com/cyberdaddyleo/agent-display)。原作者提供十款主题及其视觉、模型、动画和交互设计。本项目保留原作者署名，不主张原版视觉设计的原创权。

- 上游固定版本：`ff44824a741d9b2a767b82f39d3dd34c75c87e3f`。
- `upstream/agent-display/` 保留该版本的四个原始文件；构建时校验原版 HTML 的 SHA-256。
- 下游新增：只读采集器、本机服务、配置程序、06 FORGE / 07 WORKS 真实记录映射、共享轮询、代码与用量展示、缓存清理及验证脚本。
- 生成的 `web/index.html` 是下游构建产物；上游源文件不被修改。
- 打包版本：2.1.0，2026-11-02。

## 许可

原项目使用 **PolyForm Noncommercial 1.0.0**，允许范围与限制以 [LICENSE](LICENSE) 原文为准。本仓库按相同许可分发；它并非不受商业使用限制的 MIT 项目。商用请联系相应权利人取得授权。

Required Notice: Copyright (c) 2026 赛博老爸

下游适配代码的贡献记录以本仓库 Git 历史为准。上述原作者版权声明不表示其为本项目新增代码背书。

## 浏览器依赖

原版页面通过 jsDelivr 加载 Three.js（MIT），通过 Google Fonts 加载字体；具体版本与链接保留在原版 HTML 中。这些浏览器资源未被本仓库重新打包。首次加载需要能访问相应站点；本机运行不等于完全离线运行。
