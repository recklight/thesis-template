# 變更紀錄

本檔案記錄 `thesis-cli` 的所有重要變更。

格式依循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，
版本號依循 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

---

## [0.1.0] - 2026-10-17

首次釋出。

### 新增

- 英文版 README（`README.md`，GitHub 預設顯示），繁體中文版在 `README.zh-TW.md`。
- 流程圖分英文與繁體中文兩版：英文版是 `docs/workflow.html` 與 `docs/images/workflow.png`，繁體中文版是 `docs/workflow.zh-TW.html` 與 `docs/images/workflow.zh-TW.png`。
- LaTeX 模板：文件類別 `thesis_report.cls`、封面到自傳的前後頁、獨立編譯的書背、`\fig` 系列圖檔巨集與 11 個可直接複製的片段。目錄章號格式與浮水印模式由 `thesis.toml` 決定，`build` 時寫進 `config/generated.tex`。
- `thesis-cli` 的八個子指令：`init`、`deps`、`doctor`、`build`、`check-refs`、`figures`、`stats`、`clean`。稽核指令都有 `--strict` 與 `--json`，退出碼固定，可以直接放進 CI。
- 設定集中在 `thesis.toml`，路徑一律相對於專案根目錄，絕對路徑與跑出根目錄的路徑在讀檔時就擋下。
- 測試用的 `.tex`、`.bib`、`.aux`、`.log` 與 PNG 全部由程式產生，不需要 TeX，也不讀任何真實論文。
- 範例腳本 `examples/audit_synthetic_thesis.py`：生出一份埋了四個缺陷的論文專案，稽核、修好、再稽核一次。
- 繁體中文的 README 與 CONTRIBUTING。
