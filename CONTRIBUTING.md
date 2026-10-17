# 參與開發

歡迎修正錯誤或補上功能，送 pull request 前請先跑過下面的檢查。

---

## 開發環境

需要 Python 3.10 以上。建議使用虛擬環境：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 安裝本專案

以可編輯模式安裝，並一併安裝開發工具：

```bash
pip install -e ".[dev]"
thesis-cli --help
```

測試不需要 TeX。需要 TeX 才會通過的只有 `tests/test_build.py` 裡標了 `requires_latex` 的那一個，沒裝的時候自動略過；在裝了 TeX 的機器上，`doctor` 的 smoke test 那個測試也會實際跑一次引擎。

---

## 提交前的檢查

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

三項都要通過。CI 會跑相同的檢查，在 Linux 與 Windows 上跑 Python 3.10–3.13，另外有一個 job 用最舊的相依版本（numpy 1.24、typer 0.12 搭配 click 8.1）在 3.10 上跑測試。

### Python 3.10 相容性

開發用的 Python 可能比 3.10 新，但程式碼要能在 **3.10** 上跑。
新增程式碼時請避開 3.11 以後才有的語法與標準函式庫：

| 不要用 | 請用 |
| --- | --- |
| `tomllib` | `tomli` |
| `typing.Self` | 類別名稱字串或 `TypeVar` |
| `enum.StrEnum` | `class Foo(str, Enum)` |
| `datetime.UTC` | `datetime.timezone.utc` |
| `except*`、`ExceptionGroup` | 一般的 `except` |
| `asyncio.TaskGroup` | `asyncio.gather` |
| PEP 695 泛型（`class Foo[T]`、`type X = ...`） | `TypeVar` 與 `TypeAlias` |
| `itertools.batched` | 自行實作切片 |

CI 有一道語法閘門會用 `ast.parse(..., feature_version=(3, 10))` 逐檔檢查，
用到新語法會直接失敗。本機可以先跑一次同樣的檢查：

```bash
python -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8'), str(p), feature_version=(3, 10)) for p in pathlib.Path('.').rglob('*.py') if not {'.venv', 'build', 'dist', '__pycache__'} & set(p.parts)]"
```

---

## 程式碼風格

- 每個模組開頭加 `from __future__ import annotations`。
- 公開函式要有 type hints 與英文的 numpy 風格 docstring；行為依據某份規格或文獻時，在 `References` 段寫出處。
- 給人讀的文件用繁體中文寫：`README.zh-TW.md`、本檔案與 `CHANGELOG.md`。`README.md` 是英文版，兩份 README 要一起改。
- 函式庫模組用 `logging` 輸出診斷訊息，不要用 `print`；只有 CLI 會設定 logging。
- 片段與兩張佔位圖都由 `make_snippets.py` 產生。巨集的參數改了，就改腳本裡對應的片段再重跑 `python make_snippets.py`，不要手改 `template/snippets/`。

---

## 資料

**本 repository 不包含任何論文內容。** 測試用的 `.tex`、`.bib`、`.aux`、`.log` 與 PNG 都由測試程式當場產生。請不要提交真實論文的章節、口試資料、真實姓名或圖檔；`template/` 只放佔位文字，`tests/test_documentation_language.py` 會掃描路徑、電子郵件與學號格式的字串。

---

## 提交訊息

請使用 Conventional Commits（`feat`／`fix`／`docs`／`test`／`refactor`）。有功能變動時，請同步更新 `CHANGELOG.md` 的 `[Unreleased]` 段。
