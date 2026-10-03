# 干净提交验收镜像

API 和 Worker 已从干净提交 `f5758db611d5b0fef2075ab934df8580c96e88fe` 的 Git archive 构建为本地 Linux/ARM64 验收镜像。完整结果与证据哈希见 [CLEAN_IMAGE_PACKAGING_RESULTS.json](CLEAN_IMAGE_PACKAGING_RESULTS.json)。真实模型预检仍失败，整轮验收尚未完成。

| 镜像 | Image ID |
| --- | --- |
| `datacenter-vnext-api:accept-f5758db` | `sha256:36df26c7d1ec5d59365a719a026de2e39a0639ae4c6c718db355ee1f5f41d242` |
| `datacenter-vnext-worker:accept-f5758db` | `sha256:af99e41c771ab261f1aec4d56260d397fce0e6b3e9e50cc4bb220d41c479e615` |

两镜像都标注完整 revision 和 `git-archive-clean-commit`。导出的581个跟踪文件中，396个构建相关文件与干净工作树逐字一致，没有复制私有根 `.env`。代码来源提交保持固定；后续交付文档提交不改变这次镜像身份。

镜像内容通过独立读取核对，不能只凭 revision 标签判断源码一致：

- API：全部15个配置和迁移文件与导出文件 SHA256 一致；保存编译后的二进制哈希。
- Worker：实际安装在 `site-packages/nested_doc_rag` 的全部82个文件与导出的包源码 SHA256 和长度一致；全部3个 Python 配置、2个 Go 配置和13个迁移文件也一致。比较只排除缓存和字节码，未放行额外源码或资源文件。
- Worker：容器内 `python -I -B -m nested_doc_rag.cli show-config --config /app/python-core/config/docker.yaml` 退出0，验证安装包导入和生产配置加载。

这些临时检查容器使用固定 Image ID、`--rm --read-only --network none` 和显式 shell/Python 入口，没有挂载卷或执行默认服务入口。检查本身没有模型调用。完整命令、安装依赖版本、stdout/stderr与内容清单位于 `artifacts/vnext/phase5/resume-02/`。

构建沿用现有基础镜像标签及 Python 依赖范围，已记录本次实际依赖版本；不据源码一致宣称独立重建可以得到完全相同的镜像。原909项 Python、523项 Go race leaf、39项前端验证仍为此前执行的结果，本轮未重跑完整回归。容器配置检查也不替代真实链路验收。

本轮真实 embedding、rerank、chat 各执行一次小请求，均在约5.023秒后 `RemoteDisconnected`。上游 usage、费用与权重指纹仍为 unknown。新构建没有生成服务器交付压缩包，也没有启动或发布部署。

后续依原顺序完成三对真模型 clean Docker fresh-upload、真实 challenge/A0–A3及同A3仅禁补查对照、old141 live blank/preserve兼容复现。每次使用新的输出和存储身份。独立原文核准是 old141 质量声明的前提，不新增全141人工签审门槛；A4、GC、outbox和浏览器截图也不作为本轮额外门槛。

原项目103个冻结文件、此前28份及31份绑定证据、旧003的382份运行/首轮对照/配置文件均再次核对不变。原始历史报告保留。
