# Tasks: add-chart-persistent-storage

## 1. TDD 先红

- [ ] 1.1 失败测试：`_charts_dir("600519")` 默认 `data/charts/600519`（非 tempdir）
- [ ] 1.2 失败测试：`CHARTS_DIR` 环境变量覆盖生效（E2E/测试隔离注入惯例）
- [ ] 1.3 实现 report.py 接缝 + 调用点 + .gitignore

## 2. 验证

- [ ] 2.1 全量 pytest -m not live 0 失败；ruff/mypy 任务范围零错误
- [ ] 2.2 openspec validate --strict
- [ ] 2.3 持久性论证：compose `./data:/app/data` 卷挂载即持久性证明（容器重启路径存活）；存量 /tmp 引用降级语义有既有场景覆盖（Markdown 导出图片自包含·源文件缺失时优雅降级），无需新验证
