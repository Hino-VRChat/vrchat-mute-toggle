# VRChat Mute Toggle - 設計書

## 概要

VRChatのミュート状態をOSC（Open Sound Control）経由でトグルするデスクトップアプリケーション。
グローバルホットキーで操作し、タスクトレイに常駐する。

## 基本仕様

| 項目 | 内容 |
|---|---|
| ファイル | `vrchat_mute_toggle.py` |
| 言語 | Python 3.10+ |
| GUI | tkinter（ダークテーマ、320×240px、常に最前面） |
| ビルド | Pyinstaller onefile（`vrchat_mute_toggle.exe`、約16.5MB） |
| 設定ファイル | `vrchat_mute_config.json`（exeと同じディレクトリに生成） |

## 依存ライブラリ

| ライブラリ | 用途 |
|---|---|
| `python-osc` | OSCメッセージの送受信（VRChatとの通信） |
| `keyboard` | グローバルキーフック（ホットキー検出） |
| `pystray` | タスクトレイアイコン |
| `Pillow` | トレイアイコン画像の動的生成 |

```
pip install python-osc keyboard pystray Pillow
```

## VRChat側の前提設定

1. **OSCを有効化**: Action Menu → Options → OSC → Enabled
2. **Toggle VoiceをON**: VRChatの音声設定でToggle Voiceを有効にする

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────┐
│               vrchat_mute_toggle                        │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  キーフック   │  │   OSC送信    │  │  OSC受信     │  │
│  │  (keyboard   │  │  ワーカー    │  │  リスナー    │  │
│  │   .hook)     │  │  (queue)     │  │  (port 9001) │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │           │
│         ▼                 ▼                 ▼           │
│  ┌──────────────────────────────────────────────────┐   │
│  │              GUI (tkinter mainloop)              │   │
│  │  ┌────────────┐  ┌────────┐  ┌───────────────┐  │   │
│  │  │ ステータス │  │ホット  │  │ VRChat検出    │  │   │
│  │  │ 表示       │  │キー表示│  │ インジケータ  │  │   │
│  │  └────────────┘  └────────┘  └───────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │            タスクトレイ (pystray)                 │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
         │                  │                  ▲
         │                  ▼                  │
         │         ┌────────────────┐  ┌───────────────┐
         │         │ VRChat OSC入力 │  │ VRChat OSC出力│
         │         │ 127.0.0.1:9000 │  │ 127.0.0.1:9001│
         │         │ /input/Voice   │  │ /avatar/params│
         │         └────────────────┘  │ /MuteSelf     │
         │                             └───────────────┘
         ▼
    キーボード入力
```

## スレッド構成

| スレッド | 役割 | 種別 |
|---|---|---|
| メインスレッド | tkinter mainloop、GUI更新 | メイン |
| OSC送信ワーカー | `queue.Queue` から取り出してOSCメッセージを送信。例外時はクライアントを再生成 | daemon |
| OSC受信サーバー | ポート9001でVRChatからの `MuteSelf` パラメータを受信 | daemon |
| VRChatプロセスチェック | 30秒ごとに `tasklist` でVRChat.exeの存在を確認 | daemon（都度生成） |
| キーフック再登録 | 60秒ごとに `keyboard.hook` を再登録（Windowsフック自動解除対策） | メイン（after） |
| タスクトレイ | pystrayのイベントループ | daemon |

## 機能詳細

### 1. ミュートトグル

- ホットキー押下で `/input/Voice` にOSCメッセージを送信
- 送信シーケンス: `1`（press） → 50ms待ち → `0`（release） → 50ms待ち
- `queue.Queue` で直列化。並行送信による衝突を防止
- GUI表示は即座に更新、OSC送信はバックグラウンドで処理
- OSC送信の例外時はクライアントを自動再生成（ワーカースレッドの死亡を防止）

### 2. ホットキー検出

- `keyboard.hook()` でグローバルキーイベントを受信
- **`event.name` ベース**で判定（スキャンコードは環境差があるため不採用）
- `"right ctrl"` / `"ctrl"` で左右Ctrlを区別
- `hotkey_triggered` フラグでキーリピートによる連続発火を防止
- キーを離すまで再トリガーしない → 離した瞬間にリセット
- **60秒ごとにフックを自動再登録**（Windowsが応答遅延の `LowLevelKeyboardProc` フックを自動解除する問題への対策）
- フック再登録時に `pressed_names` をクリア（取りこぼしKEY_UPによる「押しっぱなし」判定を防止）

### 3. ホットキー変更

- 「変更」ボタンで記録モードに入る
- 記録モード中はミュートトグルを無効化
- 押されたキーをリアルタイムで表示
- 全キーが離された時点で確定
- ESCキーでキャンセル
- 設定はJSONファイルに永続化

### 4. VRChat検出

- 起動時に `tasklist /FI "IMAGENAME eq VRChat.exe"` で確認
- 30秒ごとに定期チェック（別スレッド）
- VRChat起動 → OSCリスナー起動 + ミュート状態を「オフ」で初期化
- VRChat終了 → OSCリスナー停止
- フッターに「● VRChat 検出」/「○ VRChat 未検出」を表示

### 5. ミュート状態の同期

- VRChatはOSCポート9001に `/avatar/parameters/MuteSelf`（bool）を送信する
- このツールはポート9001でリスナーを起動して受信
- 受信時にGUIのミュート表示をリアルタイム更新
- **制約**: VRChatのOSCは「変更通知」のみ。「現在値の問い合わせ」APIはない。そのため起動時の初期値はVRChatのデフォルト（ミュートオフ）を仮定

### 6. タスクトレイ

- ウィンドウを最小化するとタスクバーから消えてトレイに格納
- トレイアイコンはミュート状態に応じて変化（赤丸=ミュート、青丸+マイク=通話中）
- ダブルクリックでウィンドウ復元
- 右クリックメニュー: 「表示」「終了」
- トレイ格納中もホットキーは有効

## 設定ファイル仕様

`vrchat_mute_config.json`:

```json
{
  "hotkey_display": "Right Ctrl",
  "hotkey_names": ["right ctrl"],
  "osc_ip": "127.0.0.1",
  "osc_port": 9000
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `hotkey_display` | string | GUI表示用のホットキー名 |
| `hotkey_names` | string[] | `keyboard` ライブラリの `event.name` 値のリスト |
| `osc_ip` | string | VRChatのOSC受信アドレス |
| `osc_port` | int | VRChatのOSC受信ポート |

## Pyinstaller ビルド

```
pyinstaller --onefile --noconsole --name vrchat_mute_toggle .\vrchat_mute_toggle.py
```

## OSCプロトコル

### 送信（このツール → VRChat）

| アドレス | 値 | 説明 |
|---|---|---|
| `/input/Voice` | `1` (int) | ボタン押下（press） |
| `/input/Voice` | `0` (int) | ボタン解放（release） |

送信先: `127.0.0.1:9000`（VRChatのデフォルトOSC入力ポート）

### 受信（VRChat → このツール）

| アドレス | 値 | 説明 |
|---|---|---|
| `/avatar/parameters/MuteSelf` | `true`/`false` (bool) | ミュート状態の変更通知 |

受信元: `127.0.0.1:9001`（VRChatのデフォルトOSC出力ポート）

## 既知の制約

1. **ポート9001の競合**: VRCX等の他OSCツールがポート9001を使用している場合、リスナーが起動できない。手動トグルのみの動作になる
2. **初期ミュート状態の推定**: VRChatのOSCに「現在値の問い合わせ」がないため、起動時はVRChatのデフォルト（ミュートオフ）を仮定する
3. **管理者権限**: `keyboard` ライブラリはWindowsでグローバルキーフックに管理者権限が必要な場合がある
4. **VRChat側の設定依存**: Toggle Voiceが無効の場合、Push-to-Talk動作になりトグルとして機能しない
5. **キーフックの自動解除**: Windowsは応答の遅い `LowLevelKeyboardProc` フックを自動的に解除する。トレイ格納中やスリープ復帰時に発生しやすい。対策と60秒ごとの自動再登録を実装済み
