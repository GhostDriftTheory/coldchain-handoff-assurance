# コールドチェーン 引き渡し品質判定 PoC

> ⚠️ **サンプルデータによる概念実証です。規制上の根拠にはなりません。**
> 本番環境での使用前に、閾値を製品・輸送ルートごとに別途検証してください。

---

## これは何をするシステムか

医薬品がコールドチェーン内で受け渡されるたびに（出荷元 → 輸送業者 → 中継拠点 → 病院）、
「この荷物を次に進めてよいか」を自動判定するPoC（概念実証）です。

**3つのことを実証します：**

| | 機能 | 内容 |
|--|------|------|
| ① | **止める** | 問題のある荷物を止め、上流の問題が下流に波及しないようにする |
| ② | **残す** | 「なぜ通したか」をルールセットダイジェストとともに記録する |
| ③ | **辿る** | 問題が起きたとき、どこで何が起きたかを正確に特定する |

---

## 判定の種類

引き渡し1回ごとに以下のいずれかの判定が出ます：

| 判定 | 意味 | 対応 |
|------|------|------|
| ✅ **RELEASE** | 証拠完備・全指標がプロファイル内 | 次の区間へ進む |
| 🟡 **HOLD** | 証拠不足（記録欠落 / シール破損） | 隔離して調査 |
| 🟠 **QA_REVIEW** | 温度逸脱がグレーゾーン（alpha < 指標 ≤ beta） | QAレビュー後に判断 |
| 🔴 **REJECT** | 重大違反 — 指標がbeta閾値を超過 | 廃棄・配送禁止 |

RELEASE以外の判定が出た時点で**チェーンはデフォルトで停止**します。
後続の区間は「整合性が保証できない」として評価されません。

---

## 実際に動かしてみる

Python 3.8以上。外部ライブラリ不要。

### ① まず組み込みデモを動かす（ファイル不要）

```bash
python poc_handoff_pde.py
```

3つのシナリオ（正常・証拠欠落・温度逸脱）が一度に確認できます。

---

### ② 具体的なケースで確認する

**ケースA：輸送中に温度が致命的水準を超えた → REJECT**

輸送中に +11.5°C まで上昇（上限 +8°C）。書類・シールは正常でも廃棄判定になります。

```bash
python poc_handoff_pde.py samples/sample_beta_breach_reject.json
```

**ケースB：3回の引き渡しのうち2回目でシール破損 → HOLD + チェーン停止**

2回目で保留が出た時点で、3回目の評価は自動停止します。

```bash
python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json
```

`--continue-after-fail` を付けると、停止せず全区間を評価します（監査用）：

```bash
python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json --continue-after-fail
```

---

### ③ 不正入力がどう弾かれるか確認する

```bash
# temperatures=[] なのに temp_missing=false → INPUT ERROR
python poc_handoff_pde.py samples/sample_invalid_empty_temps.json

# duration_minutes=-10 → INPUT ERROR
python poc_handoff_pde.py samples/sample_invalid_negatives.json
```

`INPUT ERROR` は品質判定ではなく、**入力データ自体が不正**という意味です。
上流のデータを修正する必要があります。

---

### ④ Markdownサマリーを出力する

```bash
python poc_handoff_pde.py --chain samples/sample_chain_stop_demo.json --write-summary
```

`docs/summary.md` に調査アンカー（最後の正常区間 / 最初の問題区間）を含むレポートが出力されます。

---

### ⑤ テストを実行する

```bash
pip install pytest
pytest test_poc_handoff_pde.py -v
```

---

## 判定の流れ

```
入力受付
  │
  ├─ validate_handoff_input() ── 不正形式？ ──→ INPUT ERROR（exit 1、判定なし）
  │
  ↓ 正常入力
  ├─ 1. 証拠ゲート ─── 記録欠落 / シール破損？ ──→ HOLD
  ├─ 2. 致命ゲート ─── 指標 > beta？            ──→ REJECT
  ├─ 3. 自動承認 ───── 全指標 ≤ alpha？         ──→ RELEASE
  └─ 4. グレーゾーン ─ alpha < 指標 ≤ beta      ──→ QA_REVIEW
```

判定順序は固定されており、監査可能です。
ルールセットダイジェストにより、各判定は使用したプロファイルの正確なバージョンに紐付けられます。

---

## 各引き渡しで確認される証拠4項目

一つでも確認できなければ、温度が正常でも HOLD になります：

| 項目 | 内容 |
|------|------|
| 🔒 シールの完全性 | 改ざん防止シールが破損していないこと |
| 📋 受け渡し記録の完備 | 誰から誰へ・いつ渡ったかの記録が揃っていること |
| 🌡 温度ロガーの校正記録 | 使用したロガーが正しく校正されている証明があること |
| 📊 温度記録の完備 | 輸送中の温度データが欠けていないこと |

---

## プロファイル閾値（PoC スターター — 2〜8°C）

| 指標 | Alpha（自動承認 ≤） | Beta（致命的 >） |
|------|-------------------|----------------|
| d_plus [°C] | 0.3 | 2.0 |
| d_minus [°C] | 0.3 | 2.0 |
| a_plus [°C·min] | 3.0 | 30.0 |
| a_minus [°C·min] | 3.0 | 30.0 |
| gap [min] | 10 | 45 |
| duration [min] | 30 | 90 |

比較ルール：`観測値 > 閾値` → 違反。`観測値 == 閾値` → 違反ではない（境界はゾーン内に留まる）。

---

## 入力フォーマット

```json
{
  "scenario_name": "任意のラベル",
  "handoffs": [
    {
      "shipment_id":                 "SHIP-001",
      "measure_id":                  "lifescience.coldchain.handoff.2to8c.v1",
      "duration_minutes":            20.0,
      "temperatures":                [[0, 5.0], [10, 5.3], [20, 5.1]],
      "temp_missing":                false,
      "max_temp_sample_gap_minutes": 10.0,
      "handoff_from":                "出荷元-A",
      "handoff_to":                  "輸送業者-X",
      "evidence_ok":                 true,
      "custody_complete":            true,
      "seal_intact":                 true,
      "logger_calibrated":           true,
      "temp_records_complete":       true
    }
  ]
}
```

**主なフィールドルール：**
- boolフィールドは `true` / `false` のみ（文字列 `"false"` や数値 `0` は拒否）
- 数値フィールドは有限値のみ（`NaN`・`Infinity` は拒否）
- `temperatures` のタイムスタンプは厳密昇順（重複・逆順は拒否）
- `measure_id` は `"lifescience.coldchain.handoff.2to8c.v1"` と完全一致必須

温度データが欠落している区間の場合：

```json
"temperatures": [],
"temp_missing": true,
"temp_records_complete": false
```

---

## サンプルと期待される結果

| ファイル | 期待される結果 | シナリオ |
|---------|--------------|---------|
| `samples/sample_beta_breach_reject.json` | 🔴 REJECT | 温度が +11.5°C → d_plus = 3.5 > beta(2.0) |
| `samples/sample_chain_stop_demo.json`（デフォルト） | 🟡 区間2でHOLD、区間3はスキップ | 中継でシール破損 |
| `samples/sample_chain_stop_demo.json --continue-after-fail` | HOLD + RELEASE | 全区間評価（監査用） |
| `samples/sample_invalid_empty_temps.json` | INPUT ERROR、exit 1 | `temperatures=[]` かつ `temp_missing=false` |
| `samples/sample_invalid_negatives.json` | INPUT ERROR、exit 1 | `duration_minutes=-10` |
| `samples/sample_invalid_timestamps.json` | INPUT ERROR、exit 1 | タイムスタンプ逆順 |
| `samples/sample_invalid_evidence.json` | stderrに警告 + 🟡 HOLD | `evidence_ok=false` かつ全サブフラグ true |

---

## ADIC Phase 1 証明書

`ADIC_POC_PHASE1_CERTIFICATE.py` はこのリポジトリの証明書兼検証スクリプトです。
対象ファイルのSHA-256ハッシュ・プロファイルダイジェスト・リプレイ結果を記録し、
実行時に一致を検証します。push のたびに GitHub Actions で自動検証されます（`.github/workflows/adic-verify.yml`）。

```bash
# 証明書を検証する
python ADIC_POC_PHASE1_CERTIFICATE.py

# 証明書の内容をJSON形式で表示する
python ADIC_POC_PHASE1_CERTIFICATE.py --show
```

---

## ファイル構成

```
poc_handoff_pde.py                   メイン実装（単一ファイル、依存なし）
test_poc_handoff_pde.py              テストスイート（pytest）
ADIC_POC_PHASE1_CERTIFICATE.py       ADIC Phase 1 証明書 兼 検証スクリプト
README.md                            このファイル

docs/
  UPGRADE_TO_REAL_DATA.md            実データ接続のステップバイステップガイド
  SECURITY_DESIGN.md                 入力保護と既知の境界値
  RED_TEAM_REPORT.md                 レッドチーム形式シナリオテストレポート（30シナリオ）
  summary.md                         サマリーレポートの出力例

samples/
  sample_beta_breach_reject.json     判定デモ: REJECT（温度 > beta）
  sample_chain_stop_demo.json        判定デモ: HOLD → チェーン停止（3区間）
  sample_invalid_empty_temps.json    不正入力: temp_missing=false なのに temperatures=[]
  sample_invalid_negatives.json      不正入力: duration_minutes < 0
  sample_invalid_timestamps.json     不正入力: タイムスタンプが非昇順
  sample_invalid_evidence.json       エッジケース: evidence_ok=false かつ全サブフラグ true

.github/workflows/
  adic-verify.yml                    GitHub Actions 自動検証ワークフロー
```

---

## レッドチーム形式シナリオテスト

不正入力・改ざん・型強制攻撃への耐性を30シナリオで検証しています。

- **30シナリオをドキュメント化**、うち25シナリオをテストでカバー
- 不正形式・型強制のほとんどは**評価前にブロック**
- 矛盾した証拠は**バイパスではなくHOLD**になる
- 下流の正常区間が**上流の問題を隠蔽することはできない**

詳細は `docs/RED_TEAM_REPORT.md`、境界値の注記は `docs/SECURITY_DESIGN.md` を参照してください。

---

## このPoCが主張しないこと

- **規制準拠** — 閾値は製品ごと・輸送ルートごとに検証が必要
- **責任帰属** — 誰の責任かの判定は対象外
- **リアルタイム処理** — ライブセンサーではなく、記録済みデータを評価するもの
- **本番環境対応** — 移行チェックリストは `docs/UPGRADE_TO_REAL_DATA.md` を参照
- **本番グレードのペネトレーションテスト** — PoC の入力検証境界のみをカバー
